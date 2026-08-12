"""The scoring rule has an identity, and a run remembers which one scored it.

`sct_credit` converts a learner's answer into partial credit. Aggregates are recomputed
on read, so without this a later change to the rule would silently restate the credit for
answers already given, with no record of what was awarded at the time.
"""

from backend.utils.oracle_service import scoring_rule_changed
from backend.utils.panel_aggregate import SCORING_RULE_VERSION, aggregate_oracle


def rows(*ratings):
    return [
        {
            "panelist_index": i,
            "persona_id": f"seat_{i}",
            "model": "m",
            "status": "ok",
            "value": {"rating": r},
        }
        for i, r in enumerate(ratings)
    ]


def test_an_aggregate_names_the_rule_that_produced_it():
    agg = aggregate_oracle(rows(2, 2, 1), requested_n=3)

    assert agg.scoring_rule_version == SCORING_RULE_VERSION


def test_the_version_survives_the_dump_that_gets_stored_on_the_run():
    """`complete_run` persists `aggregate.model_dump()` into `panel_runs.aggregates`.
    If the field did not survive that, the snapshot could not say what scored it."""
    stored = aggregate_oracle(rows(2, 2, 1), requested_n=3).model_dump()

    assert stored["scoring_rule_version"] == SCORING_RULE_VERSION
    assert "sct_credit" in stored


def test_an_unchanged_rule_is_not_reported_as_changed():
    stored = aggregate_oracle(rows(2, 1), requested_n=2).model_dump()

    assert scoring_rule_changed(stored) is False


def test_a_moved_rule_is_reported():
    stored = aggregate_oracle(rows(2, 1), requested_n=2).model_dump()
    stored["scoring_rule_version"] = "sct-credit-v0-experimental"

    assert scoring_rule_changed(stored) is True


def test_a_snapshot_written_before_versioning_does_not_raise_a_false_alarm():
    """Every run written before 2026-08-12 lacks the label. `sct-credit-v1` is the first
    and only rule, so those snapshots were demonstrably produced by it — flagging all 36
    would teach everyone to ignore the flag."""
    assert scoring_rule_changed({"sct_credit": {"2": 1.0}}) is False


def test_a_run_with_no_snapshot_at_all_is_not_reported_as_changed():
    assert scoring_rule_changed(None) is False
    assert scoring_rule_changed({}) is False
