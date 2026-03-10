"""
Transforms structured LLM output into sim-ready format for the case_details table.
"""


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

## PATIENT DOOR CHART and Learner Instructions

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
    return {
        "Prespecified Results": "",
        "Image Links": []
    }


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
    """Returns default learner tasks markdown."""
    return """### Learner Tasks

1. **Obtain an appropriately focused and detailed history based upon the chief complaint.**
2. **Perform a pertinent physical examination based upon the chief complaint.**
3. **Discuss your diagnostic impressions and next steps with the patient.**
4. **Place appropriate orders for the patient sometimes!**
5. **Review results with the patient and further next steps.**
6. **Answer any questions the patient may have to the best of your ability.**"""


def extract_door_chart_section(content: str) -> str:
    """
    Parses rendered content markdown and extracts the Door Chart section
    (everything after '## PATIENT DOOR CHART and Learner Instructions').
    Returns the section as a string, or empty string if not found.
    """
    marker = "## PATIENT DOOR CHART and Learner Instructions"
    idx = content.find(marker)
    if idx == -1:
        return ""
    return content[idx:]
