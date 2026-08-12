"""When a stored distribution stops describing what would be asked today.

`_stale_reasons` is the guard on the invariant the whole Oracle rests on: the panel must
have rated what the learner sees. It used to compare the blinded-context hash and nothing
else, which left a real hole — see `test_editing_the_item_wording_makes_the_run_stale`.
"""

import pytest

from backend.utils import oracle_service, oracle_stems, panel_runner

CONTEXT_HASH = "ctx0000000000000"


class FakeRun:
    def __init__(
        self, *, claim_hash, stem_version="v2_revised", context_hash=CONTEXT_HASH
    ):
        self.claim_hash = claim_hash
        self.stem_version = stem_version
        self.blinded_context_hash = context_hash


class FakeOrder:
    def __init__(
        self,
        *,
        order_text="Stroke team activation",
        stem_action=None,
        stem_template=None,
    ):
        self.order_text = order_text
        self.stem_action = stem_action
        self.stem_template = stem_template


def hash_for(order, stem_version="v2_revised"):
    """The claim hash a run would have recorded for this order."""
    return panel_runner.claim_hash(
        oracle_stems.render_item(
            oracle_service._action_for(order),
            audience="oracle",
            stem_version=stem_version,
            stem_template_override=order.stem_template,
        )
    )


@pytest.fixture(autouse=True)
def pin_the_active_stem(monkeypatch):
    """The active stem is environment-configured. Pin it so these tests describe the
    staleness rule rather than whatever ORACLE_STEM_VERSION happens to be set to."""
    monkeypatch.setattr(oracle_stems, "DEFAULT_STEM_VERSION", "v2_revised")


def test_an_untouched_run_is_not_stale():
    order = FakeOrder(stem_action="activating the stroke team")

    assert (
        oracle_service._stale_reasons(
            FakeRun(claim_hash=hash_for(order)), order, CONTEXT_HASH
        )
        == []
    )


def test_editing_the_item_wording_makes_the_run_stale():
    """The defect this test exists for.

    `_identity_key` keys a Final Order on `order_text` alone, so editing `stem_action`
    keeps the order id and keeps this run attached — deliberately. But direct-sim renders
    learner items live from the current order, so the learner answers the NEW wording and
    was being scored against the OLD panel's distribution, with nothing marked stale.
    """
    order = FakeOrder(stem_action="activating the stroke team")
    run = FakeRun(claim_hash=hash_for(order))

    order.stem_action = "administering intravenous thrombolysis"

    assert oracle_service._stale_reasons(run, order, CONTEXT_HASH) == ["item_changed"]


def test_editing_a_per_order_stem_template_makes_the_run_stale():
    order = FakeOrder(stem_action="activating the stroke team")
    run = FakeRun(claim_hash=hash_for(order))

    order.stem_template = "Given the door chart alone, {action} would be:"

    assert "item_changed" in oracle_service._stale_reasons(run, order, CONTEXT_HASH)


def test_changed_case_content_still_makes_the_run_stale():
    """The original check. It must survive the new ones."""
    order = FakeOrder(stem_action="activating the stroke team")
    run = FakeRun(claim_hash=hash_for(order))

    assert oracle_service._stale_reasons(run, order, "a-different-hash") == [
        "content_drift"
    ]


def test_a_changed_stem_makes_every_run_stale():
    """The stem is the instrument (ADR-005). Ratings collected under different wording
    are not comparable, however unchanged the order is."""
    order = FakeOrder(stem_action="activating the stroke team")
    run = FakeRun(claim_hash=hash_for(order, "v1_original"), stem_version="v1_original")

    reasons = oracle_service._stale_reasons(run, order, CONTEXT_HASH)

    assert reasons == ["stem_changed"]


def test_a_stem_change_does_not_mask_a_wording_change():
    """Both are reported. The item is re-rendered with the stem the *run* used, so one
    cause cannot hide inside the other."""
    order = FakeOrder(stem_action="activating the stroke team")
    run = FakeRun(claim_hash=hash_for(order, "v1_original"), stem_version="v1_original")

    order.stem_action = "administering intravenous thrombolysis"

    assert oracle_service._stale_reasons(run, order, CONTEXT_HASH) == [
        "stem_changed",
        "item_changed",
    ]


def test_all_three_causes_are_reported_together():
    order = FakeOrder(stem_action="activating the stroke team")
    run = FakeRun(claim_hash=hash_for(order, "v1_original"), stem_version="v1_original")

    order.stem_action = "administering intravenous thrombolysis"

    assert set(oracle_service._stale_reasons(run, order, "moved")) == {
        "content_drift",
        "stem_changed",
        "item_changed",
    }


def test_a_run_that_did_not_record_what_it_asked_is_never_reported_as_current():
    """Unverifiable must not render as fresh. This number goes into an assessment."""
    order = FakeOrder(stem_action="activating the stroke team")

    reasons = oracle_service._stale_reasons(
        FakeRun(claim_hash=None), order, CONTEXT_HASH
    )

    assert "item_unverifiable" in reasons


def test_a_run_labelled_with_an_unknown_stem_is_unverifiable_rather_than_a_crash():
    """`get_stem` raises on an unknown version by design. Reading results must not."""
    order = FakeOrder(stem_action="activating the stroke team")
    run = FakeRun(claim_hash="whatever", stem_version="v0_retired")

    reasons = oracle_service._stale_reasons(run, order, CONTEXT_HASH)

    assert "item_unverifiable" in reasons
    assert "stem_changed" in reasons


def test_a_cosmetic_retype_of_the_order_text_does_not_change_the_item():
    """The rendered item is what is hashed, and `default_action_phrase` normalises the
    label. An author fixing capitalisation must not invalidate a panel."""
    order = FakeOrder(order_text="Brain MRI")
    run = FakeRun(claim_hash=hash_for(order))

    order.order_text = "Brain MRI "

    assert oracle_service._stale_reasons(run, order, CONTEXT_HASH) == []
