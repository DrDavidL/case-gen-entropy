"""What counts toward a distribution, and what the author is told about what did not.

The denominator is the whole point of this module. A rating that never arrived must never
be counted as a zero, as an abstention, or as anything else — it must leave the
denominator and say so.
"""

from backend.utils.panel_aggregate import (
    MIN_USEFUL_N,
    aggregate_oracle,
    explain_status,
)


def ok(index: int, rating: int, *, model: str = "openai/gpt-5.6-sol", concerns=None):
    return {
        "panelist_index": index,
        "persona_id": f"seat_{index}",
        "model": model,
        "status": "ok",
        "value": {"rating": rating},
        "top_concerns": concerns or [],
    }


def failed(index: int, status: str, error: str = "boom"):
    return {
        "panelist_index": index,
        "persona_id": f"seat_{index}",
        "model": "openai/gpt-5.6-sol",
        "status": status,
        "value": None,
        "error": error,
    }


def test_failed_calls_leave_the_denominator():
    agg = aggregate_oracle([ok(0, 2), ok(1, 2), failed(2, "truncated")], requested_n=3)

    assert agg.realized_n == 2
    assert agg.requested_n == 3
    # Not 2/3. A call that did not answer is not a rating of anything.
    assert agg.modal_proportion == 1.0
    assert agg.proportions["2"] == 1.0
    assert agg.null_outcomes == {"truncated": 1}


def test_excluded_calls_name_the_seat_and_the_reason():
    """The regression that motivated the suite.

    A short panel used to surface as `{'api_error': 2}`, which reads as a refusal on a
    high-risk item. It was two truncated responses. The aggregate now carries enough to
    tell those apart without opening the database.
    """
    rows = [ok(i, 2) for i in range(13)]
    rows.append(
        failed(13, "truncated", "ValidationError: Invalid JSON: EOF while parsing")
    )
    rows.append(
        failed(14, "empty_response", "TypeError: 'NoneType' object is not iterable")
    )

    agg = aggregate_oracle(rows, requested_n=15)

    assert agg.realized_n == 13
    assert [c.status for c in agg.excluded_calls] == ["truncated", "empty_response"]
    assert [c.persona_id for c in agg.excluded_calls] == ["seat_13", "seat_14"]
    assert all(c.model == "openai/gpt-5.6-sol" for c in agg.excluded_calls)
    assert "EOF while parsing" in (agg.excluded_calls[0].error or "")
    # The explanation must distinguish a transport cut from a judgement about the item.
    assert "not a judgement" in agg.excluded_calls[0].explanation

    codes = {f.code for f in agg.flags}
    assert "incomplete_panel" in codes
    message = next(f.message for f in agg.flags if f.code == "incomplete_panel")
    assert "2 of 15" in message
    assert "13 seats" in message


def test_a_full_panel_raises_no_incompleteness_flag():
    agg = aggregate_oracle([ok(i, 1) for i in range(15)], requested_n=15)

    assert agg.excluded_calls == []
    assert "incomplete_panel" not in {f.code for f in agg.flags}


def test_small_panel_warns_below_the_usable_floor():
    # MIN_USEFUL_N is a research judgement, not a law. If this fails because the group
    # moved the threshold, move the test with it deliberately.
    rows = [ok(i, 1) for i in range(MIN_USEFUL_N - 1)]
    rows += [failed(i, "api_error") for i in range(MIN_USEFUL_N - 1, 15)]

    agg = aggregate_oracle(rows, requested_n=15)

    assert agg.realized_n == MIN_USEFUL_N - 1
    assert "small_panel" in {f.code for f in agg.flags}


def test_no_usable_ratings_says_why():
    agg = aggregate_oracle(
        [failed(i, "content_filter", "blocked") for i in range(3)], requested_n=3
    )

    assert agg.realized_n == 0
    assert agg.modal_rating is None and agg.mean is None and agg.entropy is None
    assert agg.sct_credit == {"-2": 0.0, "-1": 0.0, "0": 0.0, "1": 0.0, "2": 0.0}
    (flag,) = agg.flags
    assert flag.code == "no_ratings"
    assert "moderation" in flag.message


def test_rating_outside_the_scale_is_a_null_outcome_whatever_the_status_claims():
    """A row can claim `ok` and still be unusable. The scale is the authority."""
    agg = aggregate_oracle([ok(0, 2), ok(1, 7)], requested_n=2)

    assert agg.realized_n == 1
    assert agg.null_outcomes == {"out_of_range": 1}
    assert agg.excluded_calls[0].status == "out_of_range"


def test_unanimous_distribution_has_zero_entropy_and_no_signed_zero():
    agg = aggregate_oracle([ok(i, 2) for i in range(15)], requested_n=15)

    # Not -0.0, which renders as "-0.00" and reads as a bug.
    assert agg.entropy == 0.0
    assert str(agg.entropy)[0] != "-"
    assert agg.normalized_entropy == 0.0
    assert "low_discrimination" in {f.code for f in agg.flags}


def test_mode_ties_break_deterministically_toward_zero():
    """Two bins with equal mass must not resolve by dict ordering."""
    rows = [ok(0, -2), ok(1, -2), ok(2, 1), ok(3, 1)]

    assert aggregate_oracle(rows, requested_n=4).modal_rating == 1
    # Same data, different arrival order, same answer.
    assert aggregate_oracle(list(reversed(rows)), requested_n=4).modal_rating == 1


def test_sct_credit_is_relative_to_the_mode():
    rows = [ok(0, 2), ok(1, 2), ok(2, 2), ok(3, 2), ok(4, 1), ok(5, 1), ok(6, 0)]

    credit = aggregate_oracle(rows, requested_n=7).sct_credit

    assert credit["2"] == 1.0
    assert credit["1"] == 0.5
    assert credit["0"] == 0.25
    assert credit["-2"] == 0.0


def test_transparency_matches_ground_truth_in_both_directions():
    """ "stroke" must match "posterior circulation stroke", and the reverse."""
    rows = [
        ok(0, 2, concerns=["Stroke", "seizure"]),
        ok(1, 2, concerns=["posterior circulation stroke, left"]),
        ok(2, 2, concerns=["migraine"]),
        ok(3, 2, concerns=[]),
    ]

    agg = aggregate_oracle(
        rows, requested_n=4, primary_diagnosis="posterior circulation stroke"
    )

    assert agg.transparency_rate == 0.5


def test_transparency_is_none_without_a_ground_truth():
    agg = aggregate_oracle([ok(0, 2, concerns=["stroke"])], requested_n=1)

    assert agg.transparency_rate is None


def test_ratings_are_separable_by_model():
    """ADR-018 split the roster across two families to ask where disagreement comes
    from. That question is only answerable if the split survives aggregation."""
    rows = [ok(0, 2, model="a"), ok(1, 0, model="a"), ok(2, 2, model="b")]

    by_model = aggregate_oracle(rows, requested_n=3).by_model

    assert by_model["a"]["n"] == 2
    assert by_model["a"]["mean"] == 1.0
    assert by_model["b"]["histogram"]["2"] == 1
    # A model that only produced failures contributes no bucket rather than an empty one.
    assert set(by_model) == {"a", "b"}


def test_every_status_the_runner_emits_has_a_plain_language_explanation():
    """A status with no gloss falls back to "the call did not return a usable rating",
    which is exactly the uninformative message this whole change existed to remove. Any
    new status added to the runner has to be added to STATUS_EXPLANATIONS too."""
    for status in (
        "parse_error",
        "truncated",
        "empty_response",
        "refusal",
        "content_filter",
        "api_error",
        "out_of_range",
    ):
        explanation = explain_status(status)
        assert explanation and not explanation.startswith("the call did not return")
