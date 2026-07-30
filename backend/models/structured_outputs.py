from enum import Enum

from pydantic import BaseModel, Field


class HistoryQuestion(BaseModel):
    question: str = Field(description="A specific history question to ask the patient")
    expected_answer: str = Field(description="The expected response from the patient")


class PhysicalExamFinding(BaseModel):
    examination: str = Field(description="The physical exam component or maneuver")
    findings: str = Field(description="The expected findings from this examination")


class DiagnosticTest(BaseModel):
    test: str = Field(description="The diagnostic test (lab, imaging, EKG, etc.)")
    rationale: str = Field(description="Clinical rationale for ordering this test")


class CaseDetailsStructured(BaseModel):
    presentation: str = Field(
        description="Detailed case presentation with patient demographics, chief complaint, and initial presentation"
    )
    patient_personality: str = Field(
        description="Description of patient communication style and personality traits"
    )
    history_questions: list[HistoryQuestion] = Field(
        description="List of history questions with expected patient responses"
    )
    physical_exam_findings: list[PhysicalExamFinding] = Field(
        description="List of physical examination findings"
    )
    diagnostic_workup: list[DiagnosticTest] = Field(
        description="List of diagnostic tests and their rationales"
    )


class DiagnosticBucketStructured(BaseModel):
    name: str = Field(description="Name of the diagnostic category")
    description: str = Field(
        description="Description of what conditions fall into this category"
    )


class ProbabilityEntry(BaseModel):
    bucket_name: str = Field(description="Name of the diagnostic bucket")
    probability: float = Field(description="A priori probability for this bucket")


class DiagnosticTierStructured(BaseModel):
    tier_level: int = Field(
        description="Tier level (1=broad, 2=intermediate, 3=specific)"
    )
    buckets: list[DiagnosticBucketStructured] = Field(
        description="List of diagnostic categories for this tier"
    )
    a_priori_probabilities: list[ProbabilityEntry] = Field(
        description="Probability distribution for each bucket (must sum to 1.0)"
    )


class DiagnosticFrameworkStructured(BaseModel):
    tiers: list[DiagnosticTierStructured] = Field(
        description="Three tiers of diagnostic categories with probabilities"
    )


class FeatureCategoryEnum(str, Enum):
    history = "history"
    physical_exam = "physical_exam"
    diagnostic_workup = "diagnostic_workup"


class FeatureLikelihoodRatioStructured(BaseModel):
    feature_name: str = Field(description="Name of the clinical feature")
    feature_category: FeatureCategoryEnum = Field(description="Category of the feature")
    diagnostic_bucket: str = Field(
        description="The diagnostic category this likelihood ratio applies to"
    )
    tier_level: int = Field(
        description="Which diagnostic tier this applies to (1, 2, or 3)"
    )
    likelihood_ratio: float = Field(
        description="Likelihood ratio value (>1 increases probability, <1 decreases probability)"
    )


class FeatureLikelihoodRatiosStructured(BaseModel):
    feature_likelihood_ratios: list[FeatureLikelihoodRatioStructured] = Field(
        description="List of all feature likelihood ratios"
    )


# ---------------------------------------------------------------------------
# Sim-Ready Case Models (expanded structured output for simulator-ready cases)
# ---------------------------------------------------------------------------


class PatientApproach(BaseModel):
    education_level: str = Field(
        description="Patient's education level, e.g. 'High school diploma', 'College degree'"
    )
    emotional_response: str = Field(
        description="Patient's emotional state and response during the encounter, e.g. 'Anxious and concerned about his health'"
    )
    communication_style: str = Field(
        description="How the patient communicates — verbose, terse, evasive, etc. Include behavioral cues for simulation"
    )


class HPIDetails(BaseModel):
    onset: str = Field(description="When the symptoms started, e.g. '3 hours ago'")
    location: str = Field(
        description="Anatomical location of the primary symptom, e.g. 'Substernal'"
    )
    duration: str = Field(
        description="How long the symptoms have lasted, e.g. 'Continuous since onset'"
    )
    character: str = Field(
        description="Quality or character of the symptom, e.g. 'Sharp and pressure-like'"
    )
    aggravating_alleviating_factors: str = Field(
        description="Factors that worsen or improve the symptom, e.g. 'Aggravated by physical activity, relieved by rest'"
    )
    radiation: str = Field(
        description="Whether and where the symptom radiates, e.g. 'Radiates to his left arm'"
    )
    timing: str = Field(
        description="Pattern or timing of the symptom, e.g. 'Constant since onset'"
    )
    severity: str = Field(description="Severity rating of the symptom, e.g. '7/10'")
    additional_details: str = Field(
        description="Any additional HPI details such as associated symptoms, e.g. 'Accompanied by shortness of breath and sweating'"
    )


class PastMedicalHistory(BaseModel):
    active_problems: str = Field(
        description="Current active medical conditions, e.g. 'Hypertension, Hyperlipidemia'"
    )
    inactive_problems: str = Field(
        description="Resolved or inactive medical conditions, e.g. 'None'"
    )
    hospitalizations: str = Field(
        description="Prior hospitalizations with reasons and timing, e.g. '1 for pneumonia 2 years ago'"
    )
    surgical_history: str = Field(
        description="Prior surgeries, e.g. 'Appendectomy at age 25'"
    )
    immunizations: str = Field(
        description="Immunization status, e.g. 'Up to date with vaccines; flu shot last year'"
    )


class SocialHistory(BaseModel):
    tobacco: str = Field(
        description="Tobacco use history, e.g. 'Former smoker, quit 10 years ago'"
    )
    alcohol: str = Field(
        description="Alcohol use history, e.g. 'Occasional social drinker'"
    )
    substances: str = Field(
        description="Illicit substance use history, e.g. 'Denies illicit drug use'"
    )
    diet: str = Field(
        description="Dietary habits relevant to the case, e.g. 'High in cholesterol'"
    )
    exercise: str = Field(description="Exercise habits, e.g. 'Sedentary lifestyle'")
    sexual_activity: str = Field(
        description="Sexual activity status, e.g. 'Active, no issues'"
    )
    home_life_safety: str = Field(
        description="Living situation and safety concerns, e.g. 'Lives alone, no apparent safety concerns'"
    )
    mood: str = Field(
        description="Patient's baseline mood and recent changes, e.g. 'Generally low; feeling worried'"
    )
    contextual_details: str = Field(
        description="Additional social context relevant to the case, e.g. 'Recent job stress'"
    )


class FamilyHistory(BaseModel):
    parents: str = Field(
        description="Parental medical history, e.g. 'Father had MI at age 65, mother alive with hypertension'"
    )
    siblings: str = Field(
        description="Sibling medical history, e.g. 'Two siblings, one with hypertension'"
    )


class MedicationsAllergies(BaseModel):
    medications: str = Field(
        description="Current medications with dosages, e.g. 'Lisinopril 10 mg daily, Atorvastatin 20 mg daily'"
    )
    allergies: str = Field(
        description="Known drug or other allergies, e.g. 'No known drug allergies'"
    )


class DiagnosticReasoning(BaseModel):
    essential_hpi_details: str = Field(
        description="Critical HPI elements the learner should elicit to reach the diagnosis, e.g. 'Specific characteristics of pain, onset, and associated symptoms'"
    )
    differential_diagnoses: str = Field(
        description="Comma-separated list of differential diagnoses, e.g. 'STEMI, Unstable Angina, Aortic Dissection, PE'"
    )
    rationale: str = Field(
        description="Clinical reasoning linking the presentation to the differential, e.g. 'The presence of substernal chest pain with radiation suggests ACS'"
    )


class TeachingPoints(BaseModel):
    key_learning_objectives: str = Field(
        description="Primary learning objectives for this case, e.g. 'Recognize symptoms of ACS'"
    )
    educational_content: str = Field(
        description="Educational discussion points and clinical pearls, e.g. 'Discussion on STEMI presentation and management'"
    )


class VitalSigns(BaseModel):
    blood_pressure: str = Field(
        description="Blood pressure reading, e.g. '150/90 mmHg'"
    )
    pulse_rate: str = Field(description="Pulse rate, e.g. '95 bpm'")
    respiratory_rate: str = Field(
        description="Respiratory rate, e.g. '22 breaths per minute'"
    )
    temperature_celsius: str = Field(
        description="Temperature in Celsius, e.g. '37.2 °C'"
    )
    spo2: str = Field(description="Oxygen saturation, e.g. '96%'")


class DoorChart(BaseModel):
    patient_name: str = Field(description="Simulated patient name, e.g. 'John Doe'")
    age: str = Field(description="Patient age, e.g. '60'")
    legal_sex: str = Field(description="Patient legal sex, e.g. 'Male'")
    chief_complaint: str = Field(
        description="Brief chief complaint for the door chart, e.g. 'Substernal chest pain'"
    )
    clinical_setting: str = Field(
        description="Setting where the encounter takes place, e.g. 'Emergency Department'"
    )
    vital_signs: VitalSigns = Field(
        description="Initial vital signs displayed on the door chart"
    )


class SimReadyCaseDetailsStructured(BaseModel):
    """Top-level structured output model for simulator-ready case generation.
    Combines a rich clinical dashboard with the original list-based fields
    needed for the likelihood-ratio pipeline."""

    # Clinical Dashboard fields
    case_title: str = Field(
        description="Descriptive title for the case, e.g. 'Chest Pain with ECG Changes'"
    )
    paragraph_summary: str = Field(
        description="Narrative paragraph summarizing the full case presentation"
    )
    patient_approach: PatientApproach = Field(
        description="Patient education, emotional state, and communication style for simulation"
    )
    hpi: HPIDetails = Field(
        description="Structured History of Present Illness using OLDCARTS framework"
    )
    past_medical_history: PastMedicalHistory = Field(
        description="Patient's past medical history including surgeries and immunizations"
    )
    social_history: SocialHistory = Field(
        description="Comprehensive social history relevant to the case"
    )
    family_history: FamilyHistory = Field(
        description="Family medical history for parents and siblings"
    )
    medications_allergies: MedicationsAllergies = Field(
        description="Current medications and known allergies"
    )
    ros_pertinent_findings: str = Field(
        description="Pertinent positive and negative findings from review of systems"
    )
    physical_exam_findings_text: str = Field(
        description="Full narrative physical examination findings paragraph"
    )
    diagnostic_reasoning: DiagnosticReasoning = Field(
        description="Essential HPI details to elicit, differential diagnoses, and clinical rationale"
    )
    teaching_points: TeachingPoints = Field(
        description="Key learning objectives and educational content for the case"
    )
    door_chart: DoorChart = Field(
        description="Patient door chart with demographics, chief complaint, setting, and vital signs"
    )

    # Legacy list-based fields for LR pipeline compatibility
    history_questions: list[HistoryQuestion] = Field(
        description="List of history questions with expected patient responses for LR matrix generation"
    )
    physical_exam_findings: list[PhysicalExamFinding] = Field(
        description="List of physical examination findings for LR matrix generation"
    )
    diagnostic_workup: list[DiagnosticTest] = Field(
        description="List of diagnostic tests and rationales for LR matrix generation"
    )


# ---------------------------------------------------------------------------
# Final Orders (SCT) and the Oracle panel
# ---------------------------------------------------------------------------


class FinalOrderCandidateStructured(BaseModel):
    """One proposed Final Order. A suggestion only — nothing is written until the author
    explicitly accepts it (ADR-004)."""

    order_text: str = Field(
        description=(
            "The clinical action as a short label an author would recognise, e.g. "
            "'Brain MRI', 'Echocardiogram', 'Stroke team activation'. No leading verb."
        )
    )
    stem_action: str = Field(
        description=(
            "The same action as a gerund phrase that reads naturally inside the rating "
            "stem, e.g. 'ordering a brain MRI', 'activating the stroke team'. It is "
            "inserted into the sentence '... , <stem_action> now would be:'"
        )
    )
    debatability: str = Field(
        description=(
            "Why reasonable clinicians would disagree about the appropriateness of this "
            "action in THIS case. An action everyone agrees on makes a useless item."
        )
    )
    suggested_synonyms: list[str] = Field(
        description=(
            "Alternate phrasings a learner might type when ordering this, used by the "
            "simulator to suppress the result. Be specific: for a brain MRI, 'MRI brain' "
            "and 'MR brain' but NOT bare 'imaging', which would suppress unrelated orders."
        )
    )


class FinalOrderCandidatesStructured(BaseModel):
    candidates: list[FinalOrderCandidateStructured] = Field(
        description="Three to five candidate Final Orders, most debatable first"
    )


class OracleRatingStructured(BaseModel):
    """One panelist's response.

    `rating` is a plain int rather than a constrained one on purpose: OpenAI strict
    structured outputs reject `minimum`/`maximum`, and a numeric enum is not reliably
    supported across model versions. The scale is stated in the item, and the runner
    records anything outside -2..+2 as a parse error rather than coercing it.
    """

    rating: int = Field(
        description="Appropriateness rating. Exactly one of: -2, -1, 0, 1, 2"
    )
    reasoning: str = Field(
        description="Two to three sentences justifying the rating from your perspective"
    )
    top_diagnostic_concerns: list[str] = Field(
        description=(
            "The two or three diagnoses you are most concerned about in this patient, "
            "most concerning first"
        )
    )
