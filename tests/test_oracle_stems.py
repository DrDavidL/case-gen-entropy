"""The rating stem is the measurement instrument (ADR-005, ADR-014).

Changing this text invalidates every distribution generated under the old wording, so
these tests are less about catching bugs than about making a change to the instrument
impossible to make by accident. A failure here should prompt "did we mean to change the
instrument, and did we bump the version?" — not a quick edit to the assertion.
"""

import pytest

from backend.utils import oracle_stems


def test_both_stem_versions_stay_registered():
    """v1 is kept so runs recorded under it remain interpretable, not for use."""
    assert set(oracle_stems.STEMS) == {"v1_original", "v2_revised"}


def test_the_approved_default_is_the_revised_stem():
    # Approved by the research group 2026-08-04 (ADR-014). Overridable per environment
    # via ORACLE_STEM_VERSION, which is why this asserts the registry default.
    assert oracle_stems.STEMS["v2_revised"].version == "v2_revised"
    assert oracle_stems.get_stem("v2_revised") is oracle_stems.STEMS["v2_revised"]


def test_an_unknown_version_raises_rather_than_substituting():
    """A run labelled with a stem it did not use is worse than a failed run."""
    with pytest.raises(ValueError, match="Unknown stem version"):
        oracle_stems.get_stem("v3_speculative")


def test_the_five_anchors_are_the_scale_the_aggregate_expects():
    from backend.utils.panel_aggregate import RATING_BINS

    for version, stem in oracle_stems.STEMS.items():
        values = [value for value, _label in stem.anchors]
        assert tuple(values) == RATING_BINS, version


def test_the_rendered_item_carries_the_action_and_the_whole_scale():
    item = oracle_stems.render_item(
        "ordering a brain MRI", audience="oracle", stem_version="v2_revised"
    )

    assert "ordering a brain MRI" in item
    for anchor in ("+2", "+1", "-1", "-2"):
        assert anchor in item
    # Zero renders unsigned; "+0" reads as a typo to a clinician filling the item in.
    assert " 0 =" in item and "+0" not in item


def test_the_learner_and_oracle_leads_differ_on_the_revised_stem():
    """v2 states the information state differently per rater type on purpose."""
    action = "activating the stroke team"
    learner = oracle_stems.render_item(
        action, audience="learner", stem_version="v2_revised"
    )
    oracle = oracle_stems.render_item(
        action, audience="oracle", stem_version="v2_revised"
    )

    assert learner != oracle
    assert action in learner and action in oracle


def test_only_the_learner_sees_the_information_deficit_checkbox():
    """It exists to keep information deficit out of the midpoint. The Oracle rates the
    full record, so the checkbox would mean something different there."""
    learner = oracle_stems.render_item("ordering a CT head", audience="learner")
    oracle = oracle_stems.render_item("ordering a CT head", audience="oracle")

    assert "[ ]" in learner
    assert "[ ]" not in oracle


def test_an_unknown_audience_is_rejected():
    with pytest.raises(ValueError, match="audience"):
        oracle_stems.render_item("ordering a CT head", audience="panelist")


def test_a_per_order_lead_override_cannot_change_the_scale():
    """An author may reword the question. An author may not move the anchors, because
    that would silently make one item's distribution incomparable to the others."""
    item = oracle_stems.render_item(
        "activating the stroke team",
        audience="oracle",
        stem_version="v2_revised",
        stem_template_override="Given the door chart alone, {action} would be:",
    )

    assert item.startswith(
        "Given the door chart alone, activating the stroke team would be:"
    )
    for value, label in oracle_stems.STEMS["v2_revised"].anchors:
        assert label in item
        assert (f"{value:+d}" if value else " 0") in item


@pytest.mark.parametrize(
    ("order_text", "expected"),
    [
        # Acronyms must survive both the article choice and the lowercasing. Both of
        # these reached production as "a mRI of the brain" and "a EKG".
        ("MRI of the brain", "ordering an MRI of the brain"),
        ("EKG", "ordering an EKG"),
        (
            "CT angiography of the head and neck",
            "ordering a CT angiography of the head and neck",
        ),
        ("BNP", "ordering a BNP"),
        # Sound, not spelling: "a urinalysis", but "an anti-centromere antibody".
        ("Urinalysis", "ordering a urinalysis"),
        ("Anti-centromere antibody", "ordering an anti-centromere antibody"),
        ("Ultrasound of the abdomen", "ordering an ultrasound of the abdomen"),
        # Already a gerund, or already carrying an article: do not double up.
        ("Activating the stroke team", "activating the stroke team"),
        ("The lumbar puncture", "ordering the lumbar puncture"),
    ],
)
def test_the_action_phrase_reads_as_english(order_text, expected):
    """A visible grammatical error in an assessment item costs credibility with exactly
    the clinicians whose ratings the instrument depends on."""
    assert oracle_stems.default_action_phrase(order_text) == expected


def test_an_empty_order_still_renders_a_sentence():
    assert oracle_stems.default_action_phrase("") == "taking this action"
    assert oracle_stems.default_action_phrase("   ") == "taking this action"
