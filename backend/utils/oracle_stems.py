"""Rating-stem registry for Final Order appropriateness items.

Both wordings are held here verbatim rather than one replacing the other, for two
reasons:

1. **The stem is the instrument.** Changing it invalidates every distribution generated
   under the old wording. `panel_runs.stem_version` records which one produced a given
   distribution, so a wording change is visible in the data instead of silently
   reinterpreting it.
2. **The revision is not yet approved.** Cory's 2026-07-29 review said the current stem
   "seems reasonable" and asked to see any proposed change before adopting it. Keeping
   `v1_original` live and switchable by env var means honouring that is a config change,
   not a rewrite. See `Decisions.md` ADR-014.

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


def default_action_phrase(order_text: str) -> str:
    """Derive the gerund phrase from an order label.

    Correct for the overwhelming majority of Final Orders, which are tests and
    treatments. Activations and consults ("Stroke team activation") need an explicit
    `stem_action`; the authoring UI prompts for one and shows the rendered item so the
    author reads the sentence a learner will read.
    """
    label = (order_text or "").strip().rstrip(".")
    if not label:
        return "taking this action"
    first = label.split()[0].lower()
    # Already a gerund — "Activating the stroke team", "Ordering a brain MRI".
    if first.endswith("ing"):
        return label[0].lower() + label[1:]
    article = "" if re.match(r"^(a|an|the)\b", label, re.IGNORECASE) else "a "
    return f"ordering {article}{label[0].lower() + label[1:]}"


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
