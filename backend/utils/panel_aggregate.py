"""Aggregation and item-quality signals for Oracle panel runs.

The histogram is stored; **everything else is computed at read time**. That is
deliberate: the scoring rule is a research decision that will change, and recomputing
from per-rating rows costs nothing while regenerating 75 model calls per case costs
real money and real time.

Null-outcome ratings (parse errors, refusals, API failures) are excluded from the
denominator, never silently counted as anything. `realized_n` is what every proportion
here divides by.

See `docs/llm-panels.md` §7.
"""

import math
import re
from typing import Any

from pydantic import BaseModel

RATING_BINS: tuple[int, ...] = (-2, -1, 0, 1, 2)
MAX_ENTROPY = math.log2(len(RATING_BINS))  # 2.3219...

# The identity of the rule that turns a histogram into learner credit.
#
# **Bump this whenever anything below changes what `sct_credit` returns for a given
# histogram** — the credit formula, the bins, the null-outcome policy, or the denominator.
# Flag thresholds are advisory and do not count.
#
# Why it exists: aggregates are recomputed from the per-rating rows on every read, so a
# later change to this module retroactively changes the credit awarded for answers already
# given. The learner side of the instrument is already frozen — `direct-sim` migration 0005
# stores each learner's `rating` alongside `stem_version` and the rendered `item_text` — and
# the scoring half was not, which made the two halves disagree about whether the past is
# fixed. `complete_run` stamps this onto `panel_runs.aggregates`, so a run always carries
# the rule it was scored under and "as administered" stays reconstructible.
SCORING_RULE_VERSION = "sct-credit-v1"

# Thresholds for the author-facing flags. Named constants because these are judgement
# calls that the research group may well want to move, and a magic number buried in a
# comparison is a judgement call nobody can find.
LOW_DISCRIMINATION_MODAL_PROPORTION = 0.80
NO_SIGNAL_ENTROPY = 2.00
# Controversy is measured on the *extreme* bins, not on each half of the scale. Testing
# p(-2)+p(-1) against p(+1)+p(+2) also matches a flat distribution, which is the opposite
# finding: a uniform spread is absence of signal, not polarised disagreement.
CONTROVERSY_EXTREME_MASS = 0.25
CONTROVERSY_CENTER_MASS = 0.15
GOOD_MODAL_RANGE = (0.40, 0.70)
GOOD_ADJACENT_MASS = 0.75
TRANSPARENCY_THRESHOLD = 0.80
MIN_USEFUL_N = 8


# Plain-language gloss for each null-outcome status. The author needs to know whether a
# missing rating means "the wire cut" or "the model declined", because those lead to
# different actions: re-run versus rewrite the item.
STATUS_EXPLANATIONS: dict[str, str] = {
    "truncated": "the response was cut off before the JSON finished — a transport-level "
    "truncation, not a judgement about the item. Re-running usually fills the seat.",
    "parse_error": "the response did not match the required rating schema.",
    "empty_response": "the provider returned an empty or malformed response body. "
    "Re-running usually fills the seat.",
    "refusal": "the model declined to answer this item.",
    "content_filter": "the request or response was blocked by a moderation filter.",
    "api_error": "the call failed at the API level (rate limit, timeout, or HTTP error) "
    "after all retries.",
    "out_of_range": "the model returned a rating outside the -2..+2 scale.",
}


def explain_status(status: str) -> str:
    return STATUS_EXPLANATIONS.get(status, "the call did not return a usable rating.")


class ExcludedCall(BaseModel):
    """One seat that was asked and did not answer.

    Kept per-panelist rather than as a bare count so the author can see whether the panel
    lost its EM voice or one of three internists, and can tell a truncation from a
    refusal without opening the database.
    """

    panelist_index: int | None = None
    persona_id: str | None = None
    model: str | None = None
    status: str
    explanation: str
    error: str | None = None


class QualityFlag(BaseModel):
    code: str
    severity: str  # info | caution | warning
    message: str


class OracleAggregate(BaseModel):
    # Which rule produced `sct_credit`. Stored with the run, so a distribution can always
    # say what it was scored under rather than inheriting whatever the code says today.
    scoring_rule_version: str = SCORING_RULE_VERSION
    requested_n: int
    realized_n: int
    null_outcomes: dict[str, int]
    excluded_calls: list[ExcludedCall] = []

    histogram: dict[str, int]
    proportions: dict[str, float]

    modal_rating: int | None
    modal_proportion: float | None
    mean: float | None
    sd: float | None
    entropy: float | None
    normalized_entropy: float | None

    # credit(k) = count(k) / count(mode) — the standard SCT partial-credit vector.
    sct_credit: dict[str, float]

    # Per-model breakdown. The panel spans two model families (ADR-018), and the open
    # question the split exists to answer is whether disagreement comes from the personas
    # or from the models. That is only answerable if the ratings are separable by model,
    # so the separation is computed here rather than left for an ad-hoc query later.
    by_model: dict[str, dict[str, Any]]

    # Fraction of panelists who named the ground-truth diagnosis among their top
    # concerns. High values mean the case is diagnostically transparent.
    transparency_rate: float | None
    flags: list[QualityFlag]


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


def _names_ground_truth(concerns: Any, primary_diagnosis: str) -> bool:
    """True when any listed concern plausibly names the ground truth.

    Substring match on normalized text in both directions, so "posterior circulation
    stroke" matches a concern of "stroke" and vice versa. Intentionally generous: this
    drives an advisory signal for the author, and under-reporting transparency is the
    more misleading error.
    """
    target = _normalize(primary_diagnosis).strip()
    if not target or not isinstance(concerns, list):
        return False
    for concern in concerns:
        candidate = _normalize(str(concern)).strip()
        if not candidate:
            continue
        if candidate in target or target in candidate:
            return True
    return False


def _excluded_summary(excluded: list[ExcludedCall]) -> str:
    """One sentence naming who dropped out and why, for the flag text."""
    by_status: dict[str, list[str]] = {}
    for call in excluded:
        by_status.setdefault(call.status, []).append(call.persona_id or "unknown seat")
    parts = [
        f"{', '.join(seats)} — {explain_status(status)}"
        for status, seats in sorted(by_status.items())
    ]
    return " ".join(parts).rstrip(".")


def _flags(
    realized_n: int,
    proportions: dict[int, float],
    modal_proportion: float | None,
    modal_rating: int | None,
    entropy: float | None,
    transparency_rate: float | None,
    *,
    requested_n: int = 0,
    excluded: list[ExcludedCall] | None = None,
    answering_models: set[str] | None = None,
) -> list[QualityFlag]:
    flags: list[QualityFlag] = []
    excluded = excluded or []

    # A whole model family producing nothing is a different failure from losing seats,
    # and a worse one. The roster is split across two families precisely because personas
    # sharing a model share its priors (ADR-018) — on 2026-08-12 every dissenting vote in
    # the panel came from the secondary family, so losing it did not cost three of fifteen
    # opinions, it cost all of the measured disagreement and left items that cannot
    # separate learners. Reported before anything else, because a distribution that looks
    # unanimous for this reason looks exactly like genuine consensus.
    silent_families = {c.model for c in excluded if c.model} - (
        answering_models or set()
    )
    if silent_families and answering_models:
        flags.append(
            QualityFlag(
                code="model_family_silent",
                severity="warning",
                message=f"No usable rating from {', '.join(sorted(silent_families))} — "
                "every seat on that model failed, so this distribution comes from one "
                "model family instead of the two the roster splits across. Disagreement "
                "between families is the signal that split exists to capture. Fix the "
                "model before treating this distribution as an instrument.",
            )
        )

    if realized_n == 0:
        return [
            QualityFlag(
                code="no_ratings",
                severity="warning",
                message="No panelist returned a usable rating. Nothing can be said about "
                "this item; re-run the panel."
                + (f" Reason: {_excluded_summary(excluded)}." if excluded else ""),
            )
        ]

    # A short panel is stated whenever it happens, not only below the usability floor. A
    # distribution over 13 of 15 seats next to one over 15 is not the same measurement,
    # and the difference was previously visible only as a raw dict in a caption.
    if excluded and realized_n >= MIN_USEFUL_N:
        flags.append(
            QualityFlag(
                code="incomplete_panel",
                severity="caution",
                message=f"{len(excluded)} of {requested_n or realized_n + len(excluded)} "
                f"panelists returned no usable rating, so this distribution is over "
                f"{realized_n} seats. Reasons: {_excluded_summary(excluded)}.",
            )
        )

    if realized_n < MIN_USEFUL_N:
        flags.append(
            QualityFlag(
                code="small_panel",
                severity="warning",
                message=f"Only {realized_n} of the requested panelists returned a usable "
                f"rating. Treat the distribution as provisional and re-run before using it.",
            )
        )

    extreme_low = proportions.get(-2, 0.0)
    extreme_high = proportions.get(2, 0.0)
    center = proportions.get(0, 0.0)

    if (
        modal_proportion is not None
        and modal_proportion >= LOW_DISCRIMINATION_MODAL_PROPORTION
    ):
        flags.append(
            QualityFlag(
                code="low_discrimination",
                severity="caution",
                message=f"Low discrimination — {modal_proportion:.0%} of panelists chose the "
                "same rating. Learners will mostly agree too, so this item will not "
                "separate them.",
            )
        )
    # Checked before controversy: a distribution diffuse enough to reach this entropy is
    # spread across four or five bins, which is absence of signal rather than a
    # two-camp disagreement, however much mass happens to sit at the ends.
    elif entropy is not None and entropy >= NO_SIGNAL_ENTROPY:
        flags.append(
            QualityFlag(
                code="no_signal",
                severity="warning",
                message=f"Near-uniform disagreement (entropy {entropy:.2f} of a possible "
                f"{MAX_ENTROPY:.2f}). Disagreement without a pattern usually means the "
                "order or the stem is ambiguous rather than that the question is hard.",
            )
        )
    elif (
        extreme_low >= CONTROVERSY_EXTREME_MASS
        and extreme_high >= CONTROVERSY_EXTREME_MASS
        and center <= CONTROVERSY_CENTER_MASS
    ):
        flags.append(
            QualityFlag(
                code="genuine_controversy",
                severity="info",
                message="Mass at both poles with little in the centre — genuine clinical "
                "controversy. Often the most informative item type, but confirm the stem "
                "is not ambiguous.",
            )
        )
    elif (
        modal_proportion is not None
        and GOOD_MODAL_RANGE[0] <= modal_proportion <= GOOD_MODAL_RANGE[1]
    ):
        adjacent = modal_proportion
        if modal_rating is not None:
            for neighbour in (modal_rating - 1, modal_rating + 1):
                adjacent += proportions.get(neighbour, 0.0)
        if adjacent >= GOOD_ADJACENT_MASS:
            flags.append(
                QualityFlag(
                    code="good_discrimination",
                    severity="info",
                    message=f"Good discrimination — a clear mode at {modal_rating:+d} "
                    f"({modal_proportion:.0%}) with the rest of the mass adjacent to it.",
                )
            )

    if transparency_rate is not None and transparency_rate >= TRANSPARENCY_THRESHOLD:
        flags.append(
            QualityFlag(
                code="diagnostically_transparent",
                severity="info",
                message=f"{transparency_rate:.0%} of panelists named the ground-truth "
                "diagnosis among their top concerns despite blinding. Not necessarily a "
                "defect, but the item behaves as an obvious case rather than a hard one.",
            )
        )

    return flags


def aggregate_oracle(
    ratings: list[dict[str, Any]],
    *,
    requested_n: int,
    primary_diagnosis: str = "",
) -> OracleAggregate:
    """Aggregate one Final Order's panel.

    Each element of `ratings` is expected to carry `status`, `value` (with a `rating`
    key) and `top_concerns`. Anything whose status is not "ok", or whose rating is not
    one of the five valid bins, is counted as a null outcome and excluded — a rating
    outside the scale is a parse failure regardless of what the status field claims.
    """
    histogram: dict[int, int] = dict.fromkeys(RATING_BINS, 0)
    null_outcomes: dict[str, int] = {}
    excluded: list[ExcludedCall] = []
    values: list[int] = []
    transparency_hits = 0
    per_model: dict[str, list[int]] = {}

    for row in ratings:
        status = row.get("status") or "ok"
        raw_value = row.get("value") or {}
        rating = raw_value.get("rating") if isinstance(raw_value, dict) else None

        if status != "ok" or not isinstance(rating, int) or rating not in histogram:
            key = status if status != "ok" else "out_of_range"
            null_outcomes[key] = null_outcomes.get(key, 0) + 1
            excluded.append(
                ExcludedCall(
                    panelist_index=row.get("panelist_index"),
                    persona_id=row.get("persona_id"),
                    model=row.get("model"),
                    status=key,
                    explanation=explain_status(key),
                    error=row.get("error"),
                )
            )
            continue

        histogram[rating] += 1
        values.append(rating)
        per_model.setdefault(row.get("model") or "unknown", []).append(rating)
        if _names_ground_truth(row.get("top_concerns"), primary_diagnosis):
            transparency_hits += 1

    by_model = {
        model: {
            "n": len(ratings_for_model),
            "mean": round(sum(ratings_for_model) / len(ratings_for_model), 4),
            "histogram": {
                str(bin_): ratings_for_model.count(bin_) for bin_ in RATING_BINS
            },
        }
        for model, ratings_for_model in sorted(per_model.items())
        if ratings_for_model
    }

    realized_n = len(values)

    if realized_n == 0:
        return OracleAggregate(
            requested_n=requested_n,
            realized_n=0,
            null_outcomes=null_outcomes,
            excluded_calls=excluded,
            histogram={str(k): v for k, v in histogram.items()},
            proportions={str(k): 0.0 for k in histogram},
            modal_rating=None,
            modal_proportion=None,
            mean=None,
            sd=None,
            entropy=None,
            normalized_entropy=None,
            sct_credit={str(k): 0.0 for k in histogram},
            by_model={},
            transparency_rate=None,
            flags=_flags(
                0,
                {},
                None,
                None,
                None,
                None,
                requested_n=requested_n,
                excluded=excluded,
            ),
        )

    proportions = {k: v / realized_n for k, v in histogram.items()}

    modal_count = max(histogram.values())
    # Ties broken toward the rating closest to zero, then toward the lower value, so the
    # reported mode is deterministic across runs rather than dict-order dependent.
    modal_rating = min(
        (k for k, v in histogram.items() if v == modal_count),
        key=lambda k: (abs(k), k),
    )
    modal_proportion = modal_count / realized_n

    mean = sum(values) / realized_n
    variance = sum((v - mean) ** 2 for v in values) / realized_n
    sd = math.sqrt(variance)

    entropy = -sum(p * math.log2(p) for p in proportions.values() if p > 0)
    # Negating a sum of zeros yields -0.0, which renders as "-0.00" and reads as a bug.
    entropy = entropy or 0.0

    sct_credit = {
        str(k): (v / modal_count if modal_count else 0.0) for k, v in histogram.items()
    }

    transparency_rate = transparency_hits / realized_n if primary_diagnosis else None

    return OracleAggregate(
        requested_n=requested_n,
        realized_n=realized_n,
        null_outcomes=null_outcomes,
        excluded_calls=excluded,
        histogram={str(k): v for k, v in histogram.items()},
        proportions={str(k): round(v, 4) for k, v in proportions.items()},
        modal_rating=modal_rating,
        modal_proportion=round(modal_proportion, 4),
        mean=round(mean, 4),
        sd=round(sd, 4),
        entropy=round(entropy, 4),
        normalized_entropy=round(entropy / MAX_ENTROPY, 4),
        sct_credit=sct_credit,
        by_model=by_model,
        transparency_rate=round(transparency_rate, 4)
        if transparency_rate is not None
        else None,
        flags=_flags(
            realized_n,
            proportions,
            modal_proportion,
            modal_rating,
            entropy,
            transparency_rate,
            requested_n=requested_n,
            excluded=excluded,
            answering_models=set(by_model),
        ),
    )
