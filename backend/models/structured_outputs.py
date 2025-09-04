from pydantic import BaseModel, Field
from typing import List, Dict
from enum import Enum

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
    presentation: str = Field(description="Detailed case presentation with patient demographics, chief complaint, and initial presentation")
    patient_personality: str = Field(description="Description of patient communication style and personality traits")
    history_questions: List[HistoryQuestion] = Field(description="List of history questions with expected patient responses")
    physical_exam_findings: List[PhysicalExamFinding] = Field(description="List of physical examination findings")
    diagnostic_workup: List[DiagnosticTest] = Field(description="List of diagnostic tests and their rationales")

class DiagnosticBucketStructured(BaseModel):
    name: str = Field(description="Name of the diagnostic category")
    description: str = Field(description="Description of what conditions fall into this category")

class ProbabilityEntry(BaseModel):
    bucket_name: str = Field(description="Name of the diagnostic bucket")
    probability: float = Field(description="A priori probability for this bucket")

class DiagnosticTierStructured(BaseModel):
    tier_level: int = Field(description="Tier level (1=broad, 2=intermediate, 3=specific)")
    buckets: List[DiagnosticBucketStructured] = Field(description="List of diagnostic categories for this tier")
    a_priori_probabilities: List[ProbabilityEntry] = Field(description="Probability distribution for each bucket (must sum to 1.0)")

class DiagnosticFrameworkStructured(BaseModel):
    tiers: List[DiagnosticTierStructured] = Field(description="Three tiers of diagnostic categories with probabilities")

class FeatureCategoryEnum(str, Enum):
    history = "history"
    physical_exam = "physical_exam"
    diagnostic_workup = "diagnostic_workup"

class FeatureLikelihoodRatioStructured(BaseModel):
    feature_name: str = Field(description="Name of the clinical feature")
    feature_category: FeatureCategoryEnum = Field(description="Category of the feature")
    diagnostic_bucket: str = Field(description="The diagnostic category this likelihood ratio applies to")
    tier_level: int = Field(description="Which diagnostic tier this applies to (1, 2, or 3)")
    likelihood_ratio: float = Field(description="Likelihood ratio value (>1 increases probability, <1 decreases probability)")

class FeatureLikelihoodRatiosStructured(BaseModel):
    feature_likelihood_ratios: List[FeatureLikelihoodRatioStructured] = Field(description="List of all feature likelihood ratios")