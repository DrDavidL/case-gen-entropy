"""What the panel is allowed to see, and what it must never be asked to rate.

The blinding is built rather than redacted (ADR-005), so the interesting failures are not
"a field leaked" but "no field arrived" — construction that fails closed can close all
the way down to nothing.
"""

import pytest

from backend.utils.blinded_context import audit_leak, build_oracle_context

CASE = {
    "door_chart": {
        "age": "58",
        "legal_sex": "female",
        "chief_complaint": "Sudden dizziness and unsteady gait",
        "clinical_setting": "Community emergency department",
        "vital_signs": {"blood_pressure": "168/92", "heart_rate": "78"},
    },
    "hpi": {"onset": "Abrupt, 90 minutes before arrival"},
    "physical_exam": {"neurologic": "Left-beating nystagmus, truncal ataxia"},
    # Every one of these routinely names the diagnosis outright.
    "case_title": "Posterior circulation stroke in a 58-year-old",
    "paragraph_summary": "A 58-year-old with a posterior circulation stroke.",
    "diagnostic_reasoning": "The differential favours a cerebellar infarct.",
    "teaching_points": "Recognise posterior circulation stroke.",
}


def test_a_populated_case_produces_a_usable_context():
    context = build_oracle_context(CASE)

    assert not context.is_empty
    assert context.included_sections
    assert "Sudden dizziness" in context.text


def test_the_diagnosis_bearing_sections_never_reach_the_panel():
    context = build_oracle_context(CASE)

    for leaked in (
        "posterior circulation stroke",
        "cerebellar infarct",
        "differential",
    ):
        assert leaked.lower() not in context.text.lower()


def test_a_structured_record_with_no_recognised_fields_yields_an_empty_context():
    """The reachable failure: a resync that rebuilt the record badly, or fields written
    under names this code no longer recognises. Neither raises."""
    assert build_oracle_context({}).is_empty
    assert build_oracle_context({"unexpected_key": "some value"}).is_empty


def test_an_empty_context_would_pass_the_leak_audit():
    """Why `is_empty` has to be checked separately, and why it is not a nice-to-have.

    `audit_leak` searches for the diagnosis in the text it is given. Given nothing, it
    finds nothing and reports success — a green tick that means the audit had nothing to
    read, not that the context is safe. Without a separate empty check, that green tick
    is the last gate before 75 model calls rate no clinical information.
    """
    audit = audit_leak("", "posterior circulation stroke")

    assert audit.passed is True
    assert audit.hits == []


def test_the_leak_audit_still_catches_a_diagnosis_in_a_real_context():
    audit = audit_leak(
        "### Assessment\nFindings are consistent with a posterior circulation stroke.",
        "posterior circulation stroke",
    )

    assert audit.passed is False
    assert any("stroke" in hit.term.lower() for hit in audit.hits)


def test_a_final_order_is_withheld_from_the_available_tests_list():
    """Telling the panel the author specified a brain MRI is itself a hint about whether
    ordering one is appropriate."""
    case = {
        **CASE,
        "diagnostic_workup": [{"test": "MRI of the brain with contrast"}],
    }

    context = build_oracle_context(case, suppression_terms=["brain MRI"])

    assert context.suppressed_tests
    assert "mri" not in context.text.lower()


@pytest.mark.parametrize(
    ("workup_entry", "order_text"),
    [
        # The docstring's own example, which the original substring matching failed:
        # "brain mri" is not a substring of "mri of the brain with contrast".
        ("MRI of the brain with contrast", "brain MRI"),
        ("MRI of the brain", "brain MRI"),
        ("brain MRI", "MRI of the brain"),
        ("head CT", "CT head"),
        ("CT angiography of the head and neck", "CT angiography head and neck"),
    ],
)
def test_word_order_does_not_defeat_suppression(workup_entry, order_text):
    """Found in production: seven case versions had their rated order visible to the
    panel as a case-specified diagnostic, because the wording differed only in order.
    The panel was told the author called for a brain MRI and then asked whether ordering
    a brain MRI was appropriate."""
    case = {**CASE, "diagnostic_workup": [{"test": workup_entry}]}

    context = build_oracle_context(case, suppression_terms=[order_text])

    assert context.suppressed_tests == [workup_entry]
    assert workup_entry.lower() not in context.text.lower()


@pytest.mark.parametrize(
    ("workup_entry", "order_text"),
    [
        # Same region, different modality — the panel should still see this one.
        ("CT of the head", "MRI of the head"),
        ("Complete blood count", "Basic metabolic panel"),
        ("Chest x-ray", "brain MRI"),
        ("Troponin", "D-dimer"),
    ],
)
def test_an_unrelated_test_is_still_shown_to_the_panel(workup_entry, order_text):
    """Over-suppression is its own failure: it thins the context the panel reasons from."""
    case = {**CASE, "diagnostic_workup": [{"test": workup_entry}]}

    context = build_oracle_context(case, suppression_terms=[order_text])

    assert context.suppressed_tests == []
    assert workup_entry.lower() in context.text.lower()


# --- Author-designated withholding (research group decision, 2026-08-19) ------------
#
# Cory's vote, seconded by Alex: Dix-Hallpike and HINTS stay away from the Oracle panel on
# the dizziness cases, so the panel has to commit to a threshold for further testing rather
# than reason from a near-definitive result. Until this existed the withholding held only
# because those findings happened to be stored somewhere `build_oracle_context` does not
# read, which is a property of one author's data entry rather than of the system.

VESTIBULAR_CASE = {
    "door_chart": {"age": "61", "chief_complaint": "Room-spinning dizziness"},
    "physical_exam_findings": [
        {"examination": "Gait", "findings": "Unsteady, wide-based"},
        {"examination": "Dix-Hallpike", "findings": "Positive, right posterior canal"},
        {"examination": "HINTS exam", "findings": "Peripheral pattern"},
    ],
    "diagnostic_workup": [
        {"test": "Brain MRI", "rationale": "rule out infarct"},
        {"test": "Dix-Hallpike maneuver", "rationale": "confirm canalithiasis"},
    ],
    "oracle_withheld_findings": ["Dix-Hallpike", "HINTS"],
}


def test_a_withheld_maneuver_is_dropped_from_the_itemised_exam():
    context = build_oracle_context(VESTIBULAR_CASE)

    assert "Dix-Hallpike" not in context.text
    assert "HINTS" not in context.text
    # The findings that were not withheld still reach the panel.
    assert "Unsteady, wide-based" in context.text


def test_the_finding_goes_with_the_maneuver_name():
    """Dropping the result but keeping the label would still give the game away.

    "Dix-Hallpike: (withheld)" tells the panel the case turns on a Dix-Hallpike, which is
    most of what withholding it was meant to prevent.
    """
    context = build_oracle_context(VESTIBULAR_CASE)

    assert "posterior canal" not in context.text
    assert "Peripheral pattern" not in context.text


def test_withholding_also_covers_the_available_tests_list():
    context = build_oracle_context(VESTIBULAR_CASE)

    assert "Dix-Hallpike maneuver" not in context.text
    assert "Brain MRI" in context.text


def test_what_was_withheld_is_reported_rather_than_asserted():
    context = build_oracle_context(VESTIBULAR_CASE)

    withheld = " ".join(context.withheld_findings)
    assert "Dix-Hallpike" in withheld
    assert "HINTS" in withheld


def test_no_withheld_list_changes_nothing():
    case = {k: v for k, v in VESTIBULAR_CASE.items() if k != "oracle_withheld_findings"}
    context = build_oracle_context(case)

    assert "Dix-Hallpike" in context.text
    assert context.withheld_findings == []


def test_withholding_changes_the_context_hash():
    """Existing panel runs must go stale, not silently carry over.

    A run made against a context that included Dix-Hallpike is a different measurement
    from one made without it. `blinded_context_hash` is what the staleness check compares,
    so this is the mechanism that forces the re-run.
    """
    without = build_oracle_context(
        {k: v for k, v in VESTIBULAR_CASE.items() if k != "oracle_withheld_findings"}
    )
    with_ = build_oracle_context(VESTIBULAR_CASE)

    assert without.content_hash != with_.content_hash


def test_a_withheld_term_surviving_in_free_text_fails_the_audit():
    """The construction step cannot filter prose, so the audit has to catch it.

    `physical_exam_findings_text` is one narrative paragraph. A maneuver named inside it
    is not removable without editing the author's words, so the run refuses instead.
    """
    case = {
        **VESTIBULAR_CASE,
        "physical_exam_findings_text": (
            "Alert and oriented. Dix-Hallpike reproduces upbeating torsional nystagmus."
        ),
    }
    context = build_oracle_context(case)
    audit = audit_leak(
        context.text,
        "benign paroxysmal positional vertigo",
        withheld_terms=case["oracle_withheld_findings"],
    )

    assert not audit.passed
    assert any(hit.kind == "withheld_finding" for hit in audit.hits)


def test_a_clean_context_passes_the_withheld_audit():
    context = build_oracle_context(VESTIBULAR_CASE)
    audit = audit_leak(
        context.text,
        "benign paroxysmal positional vertigo",
        withheld_terms=VESTIBULAR_CASE["oracle_withheld_findings"],
    )

    assert not [h for h in audit.hits if h.kind == "withheld_finding"]
