"""Version-controlled Oracle panelist roster.

Fifteen calls to one model with one prompt are fifteen draws from a single posterior,
not fifteen experts (ADR-005). Variance is induced deliberately through a fixed roster
of clinician personas, the way a real SCT reference panel derives its value from
heterogeneity — spanning specialty, practice setting, experience, and risk orientation.

The roster is versioned and `panel_roster_version` is stored on every run, so a roster
change is visible in the data rather than silently reinterpreting old distributions.

**Do not edit a published roster in place.** Add a new version. The whole point of the
version stamp is that it means something.
"""

import hashlib
import os

from pydantic import BaseModel

ROSTER_VERSION = "v2"

# --- Model families -------------------------------------------------------
#
# v2 splits the panel across two model families (ADR-018). Fifteen personas driven by one
# model share that model's priors, and the first live run showed it: three EM seats
# returned identical ratings with near-identical *wording*, not merely the same
# conclusion. Persona prompting alone was not producing independent raters.
#
# The split is applied where personas are genuinely similar and left alone where they are
# not, because a model difference between two seats that are meant to contrast would
# confound the contrast with a provider effect.
#
# Anthropic candidates tested against this account on 2026-07-30, structured outputs plus
# reasoning, under its zero-data-retention policy. ZDR narrows which upstreams OpenRouter
# may route to, and the survivors do not all accept a strict response schema:
#
#   anthropic/claude-opus-5     400 — routed to Bedrock, "output_config.format:
#                                     Extra inputs are not permitted". Pinning or
#                                     excluding providers leaves no eligible endpoint.
#   anthropic/claude-opus-4.7   400 — same failure, same cause.
#   anthropic/claude-opus-4.6   works via Bedrock, ~22s per call.
#   anthropic/claude-sonnet-5   worked via Azure, ~8s per call.
#
# **Re-tested 2026-08-12: `anthropic/claude-sonnet-5` no longer works at all.** Every seat
# on it returns 400 `unsupported_request_argument: output_config.format` — the same
# strict-schema rejection that ruled out the Opus 5 line in July, now reaching Sonnet 5 as
# OpenRouter's ZDR-eligible routing moved. Confirmed on both endpoints: Azure rejects it,
# and `provider.ignore: ["Azure"]` falls through to Bedrock which rejects it too
# ("output_config.format: Extra inputs are not permitted"). There is no eligible endpoint,
# so this is not fixable by pinning a provider. `anthropic/claude-opus-4.6` still works via
# Bedrock and remains the only tested secondary.
#
# This failed silently in the sense that mattered: the panel still returned 12 of 15
# ratings and a well-formed distribution. What it cost was not 3 opinions out of 15 — on
# every item checked, *every* dissenting vote came from the three Anthropic seats and the
# twelve OpenAI seats were unanimous. Losing the family turns an item with 77-80%
# agreement into one with 100%, which is an item that cannot separate learners. Hence the
# `model_family_silent` warning in `panel_aggregate`, which fires ahead of every other flag.
#
# Retention was not relaxed to reach a better model. It is a privacy control over
# student-adjacent content, and trading it for model quality is the wrong direction.
#
# Sonnet-5 is the default over the working Opus for a research reason as much as a speed
# one: it is the same generation as the primary. A secondary that is materially weaker or
# older confounds "different model family" with "worse model", which is precisely the
# comparison the split exists to make cleanly. To use Opus anyway:
#   ORACLE_MODEL_SECONDARY=anthropic/claude-opus-4.6
MODEL_PRIMARY = os.getenv("ORACLE_MODEL_PRIMARY", "openai/gpt-5.6-sol")
MODEL_SECONDARY = os.getenv("ORACLE_MODEL_SECONDARY", "anthropic/claude-sonnet-5")

# The applicable-subspecialist seat. Cory's 2026-07-29 review asked that the fixed
# otolaryngologist seat be generalised, since the relevant surgeon or subspecialist
# depends on the case (ADR-014). Set per case via `case_versions.oracle_specialty`;
# this is only the fallback when a case does not name one.
DEFAULT_SPECIALTY = os.getenv(
    "ORACLE_DEFAULT_SPECIALTY", "applicable specialty surgeon or subspecialist"
)

SPECIALTY_SEAT_INDEX = 12


# Seats whose personas are near-duplicates of a same-specialty neighbour, and which
# therefore get the secondary family. Everything else stays on the primary.
#
#   1-5  five emergency medicine attendings differing only in setting and seniority —
#        the cluster that produced identical rationales on the first live run
#   6-7  general internist and hospitalist, overlapping scope
#
# Seats 13 and 14 (stewardship vs. risk-averse) are deliberately NOT split. They exist to
# measure a real practice-variation axis, and giving them different models would make any
# gap between them uninterpretable — persona effect and model effect would be perfectly
# confounded. Same reasoning for the distinct specialists at 8-12 and the educator at 15:
# holding the model constant is what keeps their differences attributable to the role.
SECONDARY_MODEL_SEATS: frozenset[int] = frozenset({2, 4, 7})


class Panelist(BaseModel):
    index: int
    persona_id: str
    role: str
    persona: str
    model: str

    @property
    def persona_hash(self) -> str:
        """Stable hash of the persona text actually sent to the model.

        Hashing the rendered text rather than the id catches the case where a roster is
        edited without bumping its version — the hash diverges and the data shows it.
        """
        return hashlib.sha256(self.persona.encode("utf-8")).hexdigest()[:16]


def _experience_clause(years: str) -> str:
    return f"You are {years} into independent practice."


# Each persona states setting, experience, and orientation, because those are the axes
# real appropriateness disagreement runs along. None of them mention the diagnosis or
# the case — blinding is a property of the context we build, never of the persona
# (see blinded_context.py).
_ROSTER: list[tuple[str, str, str]] = [
    (
        "em_community",
        "Emergency medicine attending — community ED",
        "You are an emergency medicine attending in a community emergency department "
        "without in-house subspecialty coverage or immediate advanced imaging overnight. "
        "Transfers are costly and slow. You weigh what you can actually obtain and act "
        "on locally.",
    ),
    (
        "em_academic",
        "Emergency medicine attending — academic ED",
        "You are an emergency medicine attending at an academic medical center with "
        "residents, full subspecialty consultation, and 24-hour advanced imaging. You "
        "are attentive to teaching value and to evidence quality behind a decision.",
    ),
    (
        "em_high_volume_urban",
        "Emergency medicine attending — high-volume urban ED",
        "You are an emergency medicine attending in a very high-volume urban emergency "
        "department that is routinely boarding admitted patients. Throughput, and the "
        "opportunity cost that a test imposes on the rest of the department, weigh on "
        "your decisions.",
    ),
    (
        "em_early_career",
        "Emergency medicine attending — 2 years post-residency",
        "You are an emergency medicine attending two years out of residency. "
        + _experience_clause("two years")
        + " Your practice closely reflects current guidelines and recent training.",
    ),
    (
        "em_late_career",
        "Emergency medicine attending — 20 years post-residency",
        "You are an emergency medicine attending twenty years out of residency. "
        + _experience_clause("twenty years")
        + " You rely substantially on pattern recognition and accumulated experience of "
        "how these presentations actually behave.",
    ),
    (
        "general_internist",
        "General internist",
        "You are a general internist who sees these presentations in ambulatory and "
        "urgent-care settings and often manages the follow-up after an ED visit. You "
        "think about what can safely be deferred to outpatient workup.",
    ),
    (
        "hospitalist",
        "Hospitalist",
        "You are a hospitalist who admits and manages these patients after the ED "
        "evaluation. You think about which tests genuinely change inpatient management "
        "and which merely repeat work.",
    ),
    (
        "neurologist",
        "Neurologist",
        "You are a neurologist who takes ED consultations. You bring detailed knowledge "
        "of neurological localisation and of the test characteristics of neuroimaging.",
    ),
    (
        "cardiologist",
        "Cardiologist",
        "You are a cardiologist who takes ED consultations. You bring detailed knowledge "
        "of cardiac risk stratification and of the test characteristics of cardiac "
        "diagnostics.",
    ),
    (
        "family_medicine",
        "Family medicine physician",
        "You are a family medicine physician with broad undifferentiated-presentation "
        "experience and continuity relationships with your patients. You weigh the "
        "downstream burden a test places on the patient.",
    ),
    (
        "geriatrician",
        "Geriatrician",
        "You are a geriatrician. You weigh functional status, comorbidity, polypharmacy, "
        "and the real harms of testing and hospitalisation in older adults, including "
        "the consequences of incidental findings.",
    ),
    (
        # Index 12 — the seat Cory asked to generalise. `role` and `persona` are
        # rewritten at build time from the case's `oracle_specialty`.
        "applicable_specialist",
        "Applicable specialty surgeon or subspecialist",
        "You are the specialty surgeon or subspecialist most relevant to this "
        "presentation. You bring detailed knowledge of the definitive diagnostics and "
        "interventions in your field, including which pre-referral tests actually change "
        "what you would do.",
    ),
    (
        "em_stewardship",
        "Emergency physician — diagnostic-stewardship orientation",
        "You are an emergency physician with an explicit diagnostic-stewardship "
        "orientation. You are attentive to overtesting, incidental findings, "
        "radiation and contrast exposure, cost, and the harms of cascades that begin "
        "with a low-yield test. You require a positive justification for testing.",
    ),
    (
        "em_risk_averse",
        "Emergency physician — risk-averse orientation",
        "You are an emergency physician with an explicitly risk-averse orientation. You "
        "weigh missed catastrophic diagnoses very heavily and are willing to accept "
        "substantial overtesting to reduce that risk. You are attentive to the "
        "medicolegal consequences of a miss.",
    ),
    (
        "medical_educator",
        "Medical educator — clinical reasoning",
        "You are a medical educator with expertise in clinical reasoning and diagnostic "
        "decision-making. You attend to whether a test meaningfully changes the "
        "post-test probability given what is already known.",
    ),
]


def build_roster(specialty: str | None = None) -> list[Panelist]:
    """Return the roster, with the subspecialist seat bound to `specialty`.

    Passing None uses `DEFAULT_SPECIALTY`, which is a generic description rather than a
    named field — a case that does not specify a specialty gets a generalist reading of
    that seat, not a silently otolaryngological one.
    """
    resolved = (specialty or DEFAULT_SPECIALTY).strip() or DEFAULT_SPECIALTY
    # "a otolaryngologist" reads as a defect in a prompt a clinician may well be shown
    # alongside the distribution it produced.
    article = "an" if resolved[:1].lower() in "aeiou" else "a"

    roster: list[Panelist] = []
    for i, (persona_id, role, persona) in enumerate(_ROSTER, start=1):
        if i == SPECIALTY_SEAT_INDEX:
            role = f"{resolved} (case-specific specialty seat)"
            persona = (
                f"You are {article} {resolved}. You bring detailed knowledge of the "
                "definitive diagnostics and interventions in your field, including which "
                "pre-referral tests actually change what you would do."
            )
        roster.append(
            Panelist(
                index=i,
                persona_id=persona_id,
                role=role,
                persona=persona,
                model=(
                    MODEL_SECONDARY if i in SECONDARY_MODEL_SEATS else MODEL_PRIMARY
                ),
            )
        )
    return roster


def model_mix(roster: list[Panelist]) -> dict[str, int]:
    """Seat count per model. Recorded on the run so the split is visible in the data."""
    mix: dict[str, int] = {}
    for panelist in roster:
        mix[panelist.model] = mix.get(panelist.model, 0) + 1
    return mix


PANEL_SIZE = len(_ROSTER)
