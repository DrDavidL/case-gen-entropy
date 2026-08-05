"""Rating-stem registry for Final Order appropriateness items.

Both wordings are held here verbatim rather than one replacing the other, for two
reasons:

1. **The stem is the instrument.** Changing it invalidates every distribution generated
   under the old wording. `panel_runs.stem_version` records which one produced a given
   distribution, so a wording change is visible in the data instead of silently
   reinterpreting it.
2. **Both survive the approval.** Cory's 2026-07-29 review asked to see any proposed
   change before adopting it, and approved `v2_revised` on 2026-08-04. `v1_original`
   stays here for provenance rather than as a live alternative — a distribution stamped
   with it must remain interpretable. See `Decisions.md` ADR-014.

Set `ORACLE_STEM_VERSION` to pick one. The learner-facing and Oracle-facing renderings
of a version are the *same item* differing only in the stated information state — that
difference is load-bearing and is the point of Change 1 in the proposal (§4.3).
"""

import os
import re

from pydantic import BaseModel, Field

# --- Anchor sets ---------------------------------------------------------------
#
# The scale is ordinal and its direction is part of the instrument. Do not reorder,
# and do not renumber.

ANCHORS_V1: list[tuple[int, str]] = [
    (-2, "Strongly inappropriate"),
    (-1, "Probably inappropriate"),
    (0, "Uncertain / depends on additional information"),
    (1, "Reasonable / defensible"),
    (2, "Strongly indicated"),
]

ANCHORS_V2: list[tuple[int, str]] = [
    (-2, "Clearly inappropriate"),
    (-1, "Probably inappropriate"),
    (0, "Equally appropriate to order or not to order"),
    (1, "Probably appropriate"),
    (2, "Clearly appropriate"),
]

INFO_DEFICIT_CHECKBOX = (
    "My rating would change substantially with information I was not able "
    "to obtain during this encounter."
)


class StemTemplate(BaseModel):
    """One version of the rating item.

    `learner_lead` and `oracle_lead` both take a single `{action}` placeholder holding
    the action as a gerund phrase — "ordering a brain MRI", "activating the stroke team".
    A gerund rather than a bare noun because not every Final Order is an order: hard-coding
    "ordering {order}" into the lead renders "ordering activating the stroke team".
    With `action` supplied, both stems below reproduce the proposal's §4 wording verbatim.
    """

    version: str
    label: str
    learner_lead: str
    oracle_lead: str
    anchors: list[tuple[int, str]]
    # v1 folds information deficit into the midpoint; v2 splits it out. This is Change 4
    # in the proposal and the reason the two are not interchangeable.
    has_info_deficit_checkbox: bool = Field(default=False)
    notes: str = ""


STEM_V1_ORIGINAL = StemTemplate(
    version="v1_original",
    label="Original draft (Alex)",
    learner_lead=(
        "Considering the information available in this case, the appropriateness of "
        "{action} is:"
    ),
    # v1 never distinguished rater information state. Using the identical lead for the
    # Oracle is faithful to the original rather than a quiet half-adoption of v2.
    oracle_lead=(
        "Considering the information available in this case, the appropriateness of "
        "{action} is:"
    ),
    anchors=ANCHORS_V1,
    has_info_deficit_checkbox=False,
    notes=(
        "Information state is unstated and identical for both rater types, so learner "
        "and Oracle ratings are not strictly comparable. Anchors span three constructs "
        "(inappropriateness, defensibility, indication)."
    ),
)

STEM_V2_REVISED = StemTemplate(
    version="v2_revised",
    label="Proposed revision (proposal §4.2)",
    learner_lead=(
        "Based on the information you gathered during this encounter, and before any "
        "pending results return, {action} now would be:"
    ),
    oracle_lead=(
        "Based on all clinical information documented in this case record, and before "
        "any pending results return, {action} now would be:"
    ),
    anchors=ANCHORS_V2,
    has_info_deficit_checkbox=True,
    notes=(
        "States the information state explicitly and differently per rater type, "
        "anchors the decision in time, holds one construct across the scale, and moves "
        "information deficit out of the midpoint into a separate checkbox."
    ),
)

STEMS: dict[str, StemTemplate] = {
    STEM_V1_ORIGINAL.version: STEM_V1_ORIGINAL,
    STEM_V2_REVISED.version: STEM_V2_REVISED,
}

DEFAULT_STEM_VERSION = os.getenv("ORACLE_STEM_VERSION", "v2_revised")


def get_stem(version: str | None = None) -> StemTemplate:
    """Return a stem template, falling back to the configured default.

    Raises on an unknown explicit version rather than silently substituting: a run
    labelled with a stem it did not use is worse than a failed run.
    """
    if version is None:
        version = DEFAULT_STEM_VERSION
    if version not in STEMS:
        raise ValueError(
            f"Unknown stem version {version!r}. Known: {sorted(STEMS)}. "
            "Set ORACLE_STEM_VERSION to one of these."
        )
    return STEMS[version]


def _render_anchors(anchors: list[tuple[int, str]]) -> str:
    # Zero renders as " 0", not "+0" — matches the proposal's §4 wording exactly, and a
    # signed zero reads as a typo to anyone filling the item in.
    return "\n".join(
        f"  {value:+d} = {label}" if value != 0 else f"   0 = {label}"
        for value, label in anchors
    )


# Letters whose spoken name begins with a vowel sound, so an acronym starting with one
# takes "an": an MRI, an EKG, an ABG — but a CT, a BNP, a PET.
_VOWEL_SOUND_LETTERS = frozenset("AEFHILMNORSX")

# Words spelled with a leading vowel but pronounced with a "y" glide, which take "a".
# "a urinalysis" is correct; "an ultrasound" is also correct, which is why this cannot be
# decided from the letter alone.
_CONSONANT_SOUND_PREFIXES = ("uri", "ure", "uro", "eu", "one", "unil", "unip", "univ")


def _lower_first(label: str) -> str:
    """Lowercase the opening word unless it is an acronym.

    The lowercasing exists so the label reads naturally mid-sentence ("ordering a brain
    MRI"). Applied blindly it mangles any label that opens with an acronym: "MRI of the
    brain" became "mRI of the brain" and "EKG" became "eKG" in the item a learner reads.
    """
    first = label.split()[0]
    stripped = re.sub(r"[^A-Za-z]", "", first)
    if len(stripped) >= 2 and stripped.isupper():
        return label
    return label[0].lower() + label[1:]


def _article_for(label: str) -> str:
    """ "a" or "an", decided by sound rather than by spelling."""
    first = re.sub(r"[^A-Za-z]", "", label.split()[0])
    if not first:
        return "a"
    if len(first) >= 2 and first.isupper():
        return "an" if first[0].upper() in _VOWEL_SOUND_LETTERS else "a"
    lowered = first.lower()
    if lowered.startswith(_CONSONANT_SOUND_PREFIXES):
        return "a"
    return "an" if lowered[0] in "aeiou" else "a"


def default_action_phrase(order_text: str) -> str:
    """Derive the gerund phrase from an order label.

    Correct for the overwhelming majority of Final Orders, which are tests and
    treatments. Activations and consults ("Stroke team activation") need an explicit
    `stem_action`; the authoring UI prompts for one and shows the rendered item so the
    author reads the sentence a learner will read.

    The article and the casing are chosen carefully because this text lands inside the
    rating item, and the item is the measurement instrument (ADR-005). "ordering a
    anti-centromere antibody" and "ordering a mRI of the brain" both reached production;
    a visible grammatical error in an assessment item costs credibility with exactly the
    clinicians whose ratings the instrument depends on.
    """
    label = (order_text or "").strip().rstrip(".")
    if not label:
        return "taking this action"
    first = label.split()[0].lower()
    # Already a gerund — "Activating the stroke team", "Ordering a brain MRI".
    if first.endswith("ing"):
        return _lower_first(label)
    if re.match(r"^(a|an|the)\b", label, re.IGNORECASE):
        return f"ordering {_lower_first(label)}"
    return f"ordering {_article_for(label)} {_lower_first(label)}"


def render_item(
    action: str,
    *,
    audience: str,
    stem_version: str | None = None,
    stem_template_override: str | None = None,
) -> str:
    """Render the full rating item for one Final Order.

    `action` is the gerund phrase ("ordering a brain MRI"). `audience` is "learner" or
    "oracle". `stem_template_override` is a per-order custom lead from
    `case_final_orders.stem_template`; it takes precedence over the registry lead but
    still uses the version's anchors, so an author cannot accidentally change the scale
    by editing the wording.
    """
    if audience not in ("learner", "oracle"):
        raise ValueError(f"audience must be 'learner' or 'oracle', got {audience!r}")

    stem = get_stem(stem_version)
    lead_template = stem_template_override or (
        stem.learner_lead if audience == "learner" else stem.oracle_lead
    )
    lead = lead_template.replace("{action}", action)

    parts = [lead, "", _render_anchors(stem.anchors)]

    if audience == "learner" and stem.has_info_deficit_checkbox:
        parts += ["", f"  [ ] {INFO_DEFICIT_CHECKBOX}"]

    return "\n".join(parts)


def comparison_table() -> str:
    """Side-by-side of every registered stem, for the research group's review.

    Exists because "show me the change first" is a reasonable request that should be
    answerable from the code that will actually run, not from a document that can drift
    away from it.
    """
    lines = []
    for stem in STEMS.values():
        lines += [
            f"### {stem.version} — {stem.label}",
            "",
            "Learner-facing:",
            "```",
            render_item(
                "ordering a brain MRI", audience="learner", stem_version=stem.version
            ),
            "```",
            "",
            "Oracle-facing:",
            "```",
            render_item(
                "ordering a brain MRI", audience="oracle", stem_version=stem.version
            ),
            "```",
            "",
            f"_{stem.notes}_",
            "",
        ]
    return "\n".join(lines)
