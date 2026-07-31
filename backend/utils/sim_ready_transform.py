"""
Transforms structured LLM output into sim-ready format for the case_details table.
"""

# The boundary between the Clinical Dashboard and the Door Chart. Load-bearing in three
# places that do not import each other: this renderer writes it, the Streamlit editor
# splits the document on it, and the simulator parses by it. Changing the text silently
# breaks the other two, so it lives here as the single source and is interpolated into
# the template below rather than repeated as a literal.
DOOR_CHART_DELIMITER = "## PATIENT DOOR CHART and Learner Instructions"


def render_sim_ready_content(case_data: dict) -> str:
    """
    Takes a dict (from SimReadyCaseDetailsStructured.model_dump()) and renders
    the full markdown content string for the case_details table.
    """
    pa = case_data["patient_approach"]
    hpi = case_data["hpi"]
    pmh = case_data["past_medical_history"]
    sh = case_data["social_history"]
    fh = case_data["family_history"]
    ma = case_data["medications_allergies"]
    dr = case_data["diagnostic_reasoning"]
    tp = case_data["teaching_points"]
    dc = case_data["door_chart"]
    vs = dc["vital_signs"]

    return f"""# Case Study: {case_data["case_title"]}
## Clinical Dashboard - Pertinent History and Physical

### Paragraph Summary of Case:
- **Paragraph Summary**: {case_data["paragraph_summary"]}

### Patient Approach:
- **Education Level**: `{pa["education_level"]}`
- **Emotional Response**: `{pa["emotional_response"]}`
- **Communication Style**: `{pa["communication_style"]}`

### History of Present Illness (HPI):
- **Onset**: `{hpi["onset"]}`
- **Location**: `{hpi["location"]}`
- **Duration**: `{hpi["duration"]}`
- **Character**: `{hpi["character"]}`
- **Aggravating/Alleviating Factors**: `{hpi["aggravating_alleviating_factors"]}`
- **Radiation**: `{hpi["radiation"]}`
- **Timing**: `{hpi["timing"]}`
- **Severity**: `{hpi["severity"]}`
- **Additional Details**: `{hpi["additional_details"]}`

### Past Medical History (PMHx):
- **Active Problems**: `{pmh["active_problems"]}`
- **Inactive Problems**: `{pmh["inactive_problems"]}`
- **Hospitalizations**: `{pmh["hospitalizations"]}`
- **Surgical History**: `{pmh["surgical_history"]}`
- **Immunizations**: `{pmh["immunizations"]}`

### Social History (SHx):
- **Tobacco**: `{sh["tobacco"]}`
- **Alcohol**: `{sh["alcohol"]}`
- **Substances**: `{sh["substances"]}`
- **Diet**: `{sh["diet"]}`
- **Exercise**: `{sh["exercise"]}`
- **Sexual Activity**: `{sh["sexual_activity"]}`
- **Home Life/Safety**: `{sh["home_life_safety"]}`
- **Mood**: `{sh["mood"]}`
- **Contextual Details**: `{sh["contextual_details"]}`

### Family History (FHx):
- **Parents**: `{fh["parents"]}`
- **Siblings**: `{fh["siblings"]}`

### Medications and Allergies:
- **Medications**: `{ma["medications"]}`
- **Allergies**: `{ma["allergies"]}`

### Review of Systems (ROS):
- **Pertinent Findings**: `{case_data["ros_pertinent_findings"]}`

### Physical Examination:
- **Findings**: `{case_data["physical_exam_findings_text"]}`

### Diagnostic Reasoning:
- **Essential HPI Details User Should Elicit**: `{dr["essential_hpi_details"]}`
- **Differential Diagnoses**: `{dr["differential_diagnoses"]}`
- **Rationale**: `{dr["rationale"]}`

### Teaching Points:
- **Key Learning Objectives**: `{tp["key_learning_objectives"]}`
- **Educational Content**: `{tp["educational_content"]}`

---

{DOOR_CHART_DELIMITER}

- **Patient Name**: `{dc["patient_name"]}`
- **Age**: `{dc["age"]}`
- **Legal Sex**: `{dc["legal_sex"]}`
- **Chief Complaint**: `{dc["chief_complaint"]}`
- **Clinical Setting**: `{dc["clinical_setting"]}`

### Vital Signs:
- **Blood Pressure Reading**: `{vs["blood_pressure"]}`
- **Pulse Rate**: `{vs["pulse_rate"]}`
- **Respiratory Rate**: `{vs["respiratory_rate"]}`
- **Temperature(Celsius)**: `{vs["temperature_celsius"]}`
- **SpO2**: `{vs["spo2"]}`"""


def build_default_custom_input() -> dict:
    """Returns default custom_input JSON."""
    return {"Prespecified Results": "", "Image Links": []}


def build_default_custom_evaluation() -> dict:
    """Returns default custom_evaluation JSON."""
    return {
        "Additional Instructions": (
            "Review all answers for brevity; do not give away any clues unless "
            "explicitly asked. For example, don't add other symptoms unless asked "
            "explicitly. If the question is open-ended, just respond: I don't "
            "really know what to say."
        )
    }


def build_default_learner_tasks() -> str:
    """Returns default learner tasks markdown.

    The "Dr. X" instruction is a privacy control, not stylistic guidance — it keeps student
    identifiers out of persisted transcripts. Keep it in sync with `learner_tasks` in
    direct-sim/sim_prompts.py. See Decisions.md ADR-009.
    """
    return """### Learner Tasks

> **Introduce yourself as "Dr. X"** — the letter X, not your own name. Please do not use your real
> name at any point in this encounter. This keeps your transcript free of anything that identifies
> you.

1. **Introduce yourself to the patient as "Dr. X".**
2. **Obtain an appropriately focused and detailed history based upon the chief complaint.**
3. **Perform a pertinent physical examination based upon the chief complaint.**
4. **Discuss your diagnostic impressions and next steps with the patient.**
5. **Place appropriate orders for the patient.**
6. **Review results with the patient and further next steps.**
7. **Answer any questions the patient may have to the best of your ability.**"""


def coerce_json_field(value, default=None):
    """Return a dict for a case_details JSON column, whatever shape it holds.

    `case_details.custom_input` and `custom_evaluation` are declared JSON, but the
    table is shared with the simulator and predates this generator. As of
    2026-07-28, 64 of 103 rows hold a JSON *string* (double-encoded by whatever
    wrote them) and 38 hold a dict, with a null and a non-dict for good measure.
    Reading one straight from the ORM and calling .get() on it raises
    AttributeError, which is how the editor for existing cases broke.

    Anything that will not decode to a dict yields the default rather than
    propagating a shape the caller cannot use.
    """
    import json

    if value is None:
        return dict(default) if default else {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return dict(default) if default else {}
    if not isinstance(value, dict):
        return dict(default) if default else {}
    return value


def normalize_image_links(links) -> list[dict]:
    """Normalize `Image Links` to [{"Test Name": str, "Test Link": str}, ...].

    Three shapes exist in production: 33 rows use dicts carrying a test name
    alongside the URL, 3 use bare URL strings, and the rest are empty or absent.

    The dict form is the useful one -- the simulator's orders prompt renders "the
    test name with a clickable link" -- so it is the canonical form here. Editing
    a case through a URL-only UI would have silently dropped the name from all 33.
    """
    if not isinstance(links, list):
        return []
    out = []
    for item in links:
        if isinstance(item, dict):
            name = str(item.get("Test Name") or item.get("test_name") or "").strip()
            url = str(item.get("Test Link") or item.get("test_link") or "").strip()
        else:
            name, url = "", str(item or "").strip()
        if name or url:
            out.append({"Test Name": name, "Test Link": url})
    return out
