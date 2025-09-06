from pydantic import BaseModel, Field
from typing import List, Dict
from enum import Enum

class HistoryQuestion(BaseModel):
    model_config = {"extra": "forbid"}
    question: str = Field(description="A specific history question to ask the patient")
    expected_answer: str = Field(description="The expected response from the patient")

class PhysicalExamFinding(BaseModel):
    model_config = {"extra": "forbid"}
    examination: str = Field(description="The physical exam component or maneuver")
    findings: str = Field(description="The expected findings from this examination")

class DiagnosticTest(BaseModel):
    model_config = {"extra": "forbid"}
    test: str = Field(description="The diagnostic test (lab, imaging, EKG, etc.)")
    rationale: str = Field(description="Clinical rationale for ordering this test")

class CaseDetailsStructured(BaseModel):
    model_config = {"extra": "forbid"}
    presentation: str = Field(description="Detailed case presentation with patient demographics, chief complaint, and initial presentation")
    patient_personality: str = Field(description="Description of patient communication style and personality traits")
    history_questions: List[HistoryQuestion] = Field(description="List of history questions with expected patient responses")
    physical_exam_findings: List[PhysicalExamFinding] = Field(description="List of physical examination findings")
    diagnostic_workup: List[DiagnosticTest] = Field(description="List of diagnostic tests and their rationales")

class DiagnosticBucketStructured(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(description="Name of the diagnostic category")
    description: str = Field(description="Description of what conditions fall into this category")

class ProbabilityEntry(BaseModel):
    model_config = {"extra": "forbid"}
    bucket_name: str = Field(description="Name of the diagnostic bucket")
    probability: float = Field(description="A priori probability for this bucket")

class DiagnosticTierStructured(BaseModel):
    model_config = {"extra": "forbid"}
    tier_level: int = Field(description="Tier level (1=broad, 2=intermediate, 3=specific)")
    buckets: List[DiagnosticBucketStructured] = Field(description="List of diagnostic categories for this tier")
    a_priori_probabilities: List[ProbabilityEntry] = Field(description="Probability distribution for each bucket (must sum to 1.0)")

class DiagnosticFrameworkStructured(BaseModel):
    model_config = {"extra": "forbid"}
    tiers: List[DiagnosticTierStructured] = Field(description="Three tiers of diagnostic categories with probabilities")

class FeatureLikelihoodRatioStructured(BaseModel):
    model_config = {"extra": "forbid"}
    feature_name: str = Field(description="Name of the clinical feature")
    feature_category: str = Field(description="Category of the feature: history, physical_exam, or diagnostic_workup")
    diagnostic_bucket: str = Field(description="The diagnostic category this likelihood ratio applies to")
    tier_level: int = Field(description="Which diagnostic tier this applies to (1, 2, or 3)")
    likelihood_ratio: float = Field(description="Likelihood ratio value (>1 increases probability, <1 decreases probability)")

class FeatureLikelihoodRatiosStructured(BaseModel):
    model_config = {"extra": "forbid"}
    feature_likelihood_ratios: List[FeatureLikelihoodRatioStructured] = Field(description="List of all feature likelihood ratios")