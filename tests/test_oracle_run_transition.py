"""Which run is the current one after a panel finishes.

`latest_run` returns the newest run that nothing supersedes, so this decision is what
stands between an author and a distribution that silently disappears.
"""

from backend.utils.oracle_service import run_transition


class FakeRun:
    def __init__(self, run_id: int, panel_size_realized: int):
        self.id = run_id
        self.panel_size_realized = panel_size_realized


def test_a_successful_rerun_retires_the_previous_one():
    assert run_transition(FakeRun(1, 15), realized=15) == "supersede_previous"


def test_the_first_successful_run_has_nothing_to_retire():
    assert run_transition(None, realized=15) == "none"


def test_a_failed_rerun_does_not_bury_a_good_distribution():
    """The defect. An OpenRouter outage during a re-run used to retire the good run and
    become the current one, so the author saw "Panel failed" and lost the numbers."""
    assert run_transition(FakeRun(1, 15), realized=0) == "retire_new"


def test_a_partial_rerun_still_supersedes():
    """13 of 15 is a usable distribution, and it is more current than the old one. The
    aggregate flags the short panel; that is a caution, not a reason to discard the run."""
    assert run_transition(FakeRun(1, 15), realized=13) == "supersede_previous"


def test_a_failed_rerun_after_a_failed_run_leaves_the_newest_failure_current():
    """Nothing worth protecting. Resurrecting an older emptiness would hide the most
    recent truth about the item."""
    assert run_transition(FakeRun(1, 0), realized=0) == "none"


def test_a_failed_first_run_has_nothing_to_fall_back_to():
    assert run_transition(None, realized=0) == "none"


def test_a_previous_run_with_no_recorded_size_is_not_treated_as_usable():
    """`panel_size_realized` is nullable for runs that never completed."""
    assert run_transition(FakeRun(1, None), realized=0) == "none"
