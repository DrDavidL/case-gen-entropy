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
