"""The Oracle view: maximum clinical information, minus the ground-truth diagnosis.

**Built, never stripped.** The blinded context is assembled field by field from the
structured case record. It is not produced by redacting the rendered markdown, and that
distinction is the whole safety argument:

- Construction fails **closed** — a field nobody explicitly included cannot leak.
- Redaction fails **open** — a phrasing nobody anticipated leaks silently.

The rendered case content is unusable for this directly. It carries a Diagnostic
Reasoning section with the differential and rationale, a Teaching Points section, and a
summary paragraph that routinely names the diagnosis outright.

See `docs/llm-panels.md` §3 and `Decisions.md` ADR-005.
"""

import hashlib
import logging
import re
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Sections deliberately never included. Kept as data so the Oracle view can report what
# it withheld, which is what makes the blinding auditable rather than asserted.
EXCLUDED_FIELDS: tuple[str, ...] = (
    "case_title",  # frequently names the diagnosis
    "paragraph_summary",  # routinely names the diagnosis outright
    "diagnostic_reasoning",  # the differential and the author's rationale
    "teaching_points",  # learning objectives name the diagnosis
    "presentation",  # legacy equivalent of paragraph_summary
    "patient_personality",  # acting guidance, not clinical information
    "patient_approach",  # acting guidance, not clinical information
)

# Diagnosis tokens too generic to match on. Matching these produces hits on every case
# ("acute", "syndrome") and an audit that cries wolf gets disabled by whoever it blocks.
_TOKEN_STOPWORDS = frozenset(
    {
        "acute",
        "and",
        "chronic",
        "disease",
        "disorder",
        "episode",
        "failure",
        "for",
        "in",
        "left",
        "lower",
        "of",
        "or",
        "possible",
        "primary",
        "probable",
        "right",
        "secondary",
        "severe",
        "suspected",
        "syndrome",
        "the",
        "type",
        "unspecified",
        "upper",
        "with",
        "without",
    }
)

# Curated abbreviations and lay synonyms. Deliberately small and specific — a broad
# thesaurus would fire on unrelated text and train authors to click through the block.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "myocardial infarction": ("MI", "STEMI", "NSTEMI", "heart attack", "ACS"),
    "acute coronary syndrome": ("ACS", "STEMI", "NSTEMI", "unstable angina"),
    "pulmonary embolism": ("PE", "pulmonary embolus", "clot in the lung"),
    "aortic dissection": ("dissection", "dissecting aneurysm"),
    "subarachnoid hemorrhage": ("SAH", "subarachnoid bleed"),
    "intracranial hemorrhage": ("ICH", "intracerebral hemorrhage", "brain bleed"),
    "cerebrovascular accident": ("CVA", "stroke", "brain attack"),
    "stroke": ("CVA", "cerebrovascular accident", "brain attack", "infarct"),
    "posterior circulation stroke": (
        "vertebrobasilar stroke",
        "cerebellar stroke",
        "brainstem stroke",
        "CVA",
    ),
    "transient ischemic attack": ("TIA", "mini-stroke"),
    "vestibular neuritis": ("labyrinthitis", "vestibular neuronitis"),
    "benign paroxysmal positional vertigo": ("BPPV", "positional vertigo"),
    "deep vein thrombosis": ("DVT", "venous thrombosis"),
    "diabetic ketoacidosis": ("DKA",),
    "congestive heart failure": ("CHF", "heart failure", "ADHF"),
    "chronic obstructive pulmonary disease": ("COPD", "emphysema"),
    "gastrointestinal bleed": ("GI bleed", "GIB", "upper GI bleed"),
    "sepsis": ("septic shock", "septicemia"),
    "meningitis": ("meningeal infection",),
    "appendicitis": ("appendiceal inflammation",),
    "ectopic pregnancy": ("tubal pregnancy",),
    "temporal arteritis": ("giant cell arteritis", "GCA"),
    "cauda equina syndrome": ("cauda equina",),
    "necrotizing fasciitis": ("nec fasc", "flesh-eating"),
    "testicular torsion": ("torsion",),
    "aortic aneurysm": ("AAA", "abdominal aortic aneurysm"),
}


class BlindedContext(BaseModel):
    text: str
    content_hash: str
    included_sections: list[str]
    excluded_sections: list[str]
    # Final Order text filtered out of the available-tests list, recorded so the audit
    # trail shows what was withheld and why.
    suppressed_tests: list[str]

    @property
    def is_empty(self) -> bool:
        """No clinical information survived into the panel's view.

        Reached when `content_structured` has none of the expected fields — a structured
        record that a resync rebuilt badly, or one written under a field naming this code
        no longer recognises. It is not a leak and not a parity break, so nothing else
        catches it, and the leak audit passes an empty string having checked nothing.
        Callers must treat this as blocking: a panel rating no clinical information still
        returns five well-formed distributions.
        """
        return not self.text.strip()


class LeakHit(BaseModel):
    term: str
    kind: str  # full_diagnosis | token | synonym
    section: str
    snippet: str


class LeakAuditResult(BaseModel):
    passed: bool
    hits: list[LeakHit]
    terms_checked: list[str]


# --- Context construction -------------------------------------------------------


def _clean(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _kv_block(title: str, mapping: dict[str, Any], keys: list[str]) -> str | None:
    """Render selected keys of a sub-object. Returns None if nothing survived."""
    if not isinstance(mapping, dict):
        return None
    lines = []
    for key in keys:
        text = _clean(mapping.get(key))
        if text:
            lines.append(f"- {key.replace('_', ' ').title()}: {text}")
    if not lines:
        return None
    return f"### {title}\n" + "\n".join(lines)


def _normalize_for_match(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


# Filler words that carry no identity for a test name. Without removing them, "the" as a
# token would make every entry a superset of every other.
_TEST_NAME_FILLER = frozenset({"a", "an", "and", "for", "of", "the", "to", "with"})


def _test_tokens(text: str) -> frozenset[str]:
    return frozenset(_normalize_for_match(text).split()) - _TEST_NAME_FILLER


def _is_suppressed_test(test_name: str, suppression_terms: list[str]) -> bool:
    """True when a case-specified test is one of the Final Orders being rated.

    Matched on **token containment**, not substrings, in both directions: a Final Order of
    "brain MRI" suppresses a workup entry of "MRI of the brain with contrast", and vice
    versa.

    Substring matching was the original implementation and it silently failed on exactly
    the example above, because word order differs — "brain mri" is not a substring of "mri
    of the brain with contrast". The consequence is not cosmetic: an unsuppressed entry
    puts the test under appropriateness review into the "Diagnostics Specified by the
    Case" list the panel reads, which tells the raters the case author called for the very
    thing they are being asked to judge. Suppression synonyms could paper over it, but
    only for an author who noticed, and the blinding must not depend on that.

    Substring matching is kept as well, since it catches hyphen and spacing variants that
    tokenisation splits differently.
    """
    normalized = _normalize_for_match(test_name)
    entry_tokens = _test_tokens(test_name)

    for term in suppression_terms:
        term_norm = _normalize_for_match(term)
        if not term_norm.strip():
            continue
        if term_norm in normalized or normalized in term_norm:
            return True
        term_tokens = _test_tokens(term)
        if not term_tokens or not entry_tokens:
            continue
        if term_tokens <= entry_tokens or entry_tokens <= term_tokens:
            return True
    return False


def build_oracle_context(
    case_details: dict[str, Any],
    *,
    suppression_terms: list[str] | None = None,
) -> BlindedContext:
    """Assemble the Oracle view from structured case fields.

    `suppression_terms` are the Final Order texts plus their synonyms. Matching entries
    are dropped from the available-tests list: telling the panel that the case author
    specified a brain MRI is itself a hint about whether ordering one is appropriate.

    Tolerates both the sim-ready and legacy structured shapes, and tolerates missing
    fields — a thin context is a visible problem, whereas raising here would block
    authoring on a field the generator happened not to fill.
    """
    suppression_terms = suppression_terms or []
    sections: list[str] = []
    included: list[str] = []
    suppressed: list[str] = []

    door = case_details.get("door_chart")
    if isinstance(door, dict):
        block = _kv_block(
            "Presenting Information",
            door,
            ["age", "legal_sex", "chief_complaint", "clinical_setting"],
        )
        if block:
            sections.append(block)
            included.append("door_chart (demographics, chief complaint, setting)")

        vitals = door.get("vital_signs")
        vitals_block = _kv_block(
            "Initial Vital Signs",
            vitals if isinstance(vitals, dict) else {},
            [
                "blood_pressure",
                "pulse_rate",
                "respiratory_rate",
                "temperature_celsius",
                "spo2",
            ],
        )
        if vitals_block:
            sections.append(vitals_block)
            included.append("vital_signs")

    hpi_block = _kv_block(
        "History of Present Illness",
        case_details.get("hpi") or {},
        [
            "onset",
            "location",
            "duration",
            "character",
            "aggravating_alleviating_factors",
            "radiation",
            "timing",
            "severity",
            "additional_details",
        ],
    )
    if hpi_block:
        sections.append(hpi_block)
        included.append("hpi (full OLDCARTS)")

    pmh_block = _kv_block(
        "Past Medical History",
        case_details.get("past_medical_history") or {},
        [
            "active_problems",
            "inactive_problems",
            "hospitalizations",
            "surgical_history",
            "immunizations",
        ],
    )
    if pmh_block:
        sections.append(pmh_block)
        included.append("past_medical_history")

    shx_block = _kv_block(
        "Social History",
        case_details.get("social_history") or {},
        [
            "tobacco",
            "alcohol",
            "substances",
            "diet",
            "exercise",
            "sexual_activity",
            "home_life_safety",
            "mood",
            "contextual_details",
        ],
    )
    if shx_block:
        sections.append(shx_block)
        included.append("social_history")

    fhx_block = _kv_block(
        "Family History",
        case_details.get("family_history") or {},
        ["parents", "siblings"],
    )
    if fhx_block:
        sections.append(fhx_block)
        included.append("family_history")

    meds_block = _kv_block(
        "Medications and Allergies",
        case_details.get("medications_allergies") or {},
        ["medications", "allergies"],
    )
    if meds_block:
        sections.append(meds_block)
        included.append("medications_allergies")

    ros = _clean(case_details.get("ros_pertinent_findings"))
    if ros:
        sections.append(f"### Review of Systems\n{ros}")
        included.append("ros_pertinent_findings")

    exam_text = _clean(case_details.get("physical_exam_findings_text"))
    if exam_text:
        sections.append(f"### Physical Examination\n{exam_text}")
        included.append("physical_exam_findings_text")

    # History Q&A: what the patient would say if asked. Patient information, so included.
    history_lines = []
    for item in case_details.get("history_questions") or []:
        if not isinstance(item, dict):
            continue
        question = _clean(item.get("question"))
        answer = _clean(item.get("expected_answer"))
        if question and answer:
            history_lines.append(f"- {question} — {answer}")
    if history_lines:
        sections.append(
            "### Additional History Elicited on Questioning\n"
            + "\n".join(history_lines)
        )
        included.append("history_questions (question + expected answer)")

    # Itemised exam findings. Included; the *maneuver* and its result are patient data.
    exam_lines = []
    for item in case_details.get("physical_exam_findings") or []:
        if not isinstance(item, dict):
            continue
        maneuver = _clean(item.get("examination"))
        finding = _clean(item.get("findings"))
        if maneuver and finding:
            exam_lines.append(f"- {maneuver}: {finding}")
    if exam_lines:
        sections.append(
            "### Examination Findings by Component\n" + "\n".join(exam_lines)
        )
        included.append("physical_exam_findings (itemised)")

    # Available diagnostics: test names only. `rationale` is excluded deliberately — it
    # is the author's reasoning for ordering the test, not patient information, and it
    # very often names the diagnosis ("to rule out posterior circulation stroke").
    test_lines = []
    for item in case_details.get("diagnostic_workup") or []:
        if not isinstance(item, dict):
            continue
        test = _clean(item.get("test"))
        if not test:
            continue
        if _is_suppressed_test(test, suppression_terms):
            suppressed.append(test)
            continue
        test_lines.append(f"- {test}")
    if test_lines:
        sections.append(
            "### Diagnostics Specified by the Case\n"
            "(Test names only. Any test under appropriateness review is withheld.)\n"
            + "\n".join(test_lines)
        )
        included.append("diagnostic_workup (test names only, rationale excluded)")

    text = "\n\n".join(sections).strip()
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    excluded = list(EXCLUDED_FIELDS) + [
        "diagnostic_workup.rationale",
        "primary_diagnosis",
    ]

    if not text:
        logger.warning(
            "Oracle context is empty — the case record has none of the expected "
            "structured fields. The panel would be rating on nothing."
        )

    return BlindedContext(
        text=text,
        content_hash=content_hash,
        included_sections=included,
        excluded_sections=excluded,
        suppressed_tests=suppressed,
    )


# --- Leak audit -----------------------------------------------------------------


def _diagnosis_terms(primary_diagnosis: str) -> list[tuple[str, str]]:
    """Build (term, kind) pairs to search for. Longest first so hits report specifically."""
    diagnosis = (primary_diagnosis or "").strip()
    if not diagnosis:
        return []

    terms: list[tuple[str, str]] = [(diagnosis, "full_diagnosis")]
    lowered = diagnosis.lower()

    for key, synonyms in _SYNONYMS.items():
        # Match either direction: a case diagnosed "posterior circulation stroke" should
        # pick up the "stroke" synonym set too.
        if key in lowered or lowered in key:
            terms.extend((syn, "synonym") for syn in synonyms)

    for token in re.split(r"[^A-Za-z0-9]+", diagnosis):
        if len(token) >= 4 and token.lower() not in _TOKEN_STOPWORDS:
            terms.append((token, "token"))

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for term, kind in sorted(terms, key=lambda t: len(t[0]), reverse=True):
        key = term.lower()
        if key not in seen:
            seen.add(key)
            unique.append((term, kind))
    return unique


def audit_leak(
    text: str,
    primary_diagnosis: str,
    extra_terms: list[str] | None = None,
) -> LeakAuditResult:
    """Check the blinded context for the diagnosis, its tokens, and known synonyms.

    Word-boundary matched and case-insensitive. Boundaries matter: without them "MI"
    hits inside "mild" and the audit becomes noise.

    A failure is **blocking**, not a warning. A warning on a screen an author sees fifty
    times gets dismissed, and the leak it described ships.
    """
    terms = _diagnosis_terms(primary_diagnosis)
    for term in extra_terms or []:
        if term and term.strip():
            terms.append((term.strip(), "synonym"))

    hits: list[LeakHit] = []
    for term, kind in terms:
        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + 60)
            snippet = text[start:end].replace("\n", " ")
            hits.append(
                LeakHit(
                    term=term,
                    kind=kind,
                    section=_section_of(text, match.start()),
                    snippet=f"...{snippet}...",
                )
            )

    return LeakAuditResult(
        passed=not hits,
        hits=hits,
        terms_checked=[term for term, _ in terms],
    )


def _section_of(text: str, position: int) -> str:
    """The `### ` heading a match falls under.

    Reported because where a term appears changes what it means. "CVA" under Family
    History is a risk factor in the patient's father; the same token under Review of
    Systems is the answer. The audit still blocks on both — an author judging a hit
    benign has to say so explicitly and it is recorded — but they cannot judge it
    without knowing where it came from.
    """
    heading = "unknown section"
    for match in re.finditer(r"^### (.+)$", text[:position], re.MULTILINE):
        heading = match.group(1).strip()
    return heading
