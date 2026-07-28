from typing import Any

from pydantic import BaseModel


class CaseInput(BaseModel):
    description: str
    primary_diagnosis: str
    output_format: str = "sim_ready"  # "sim_ready" (default) or "beta"


class CaseDetails(BaseModel):
    presentation: str
    patient_personality: str
    history_questions: list[dict[str, str]]
    physical_exam_findings: list[dict[str, str]]
    diagnostic_workup: list[dict[str, str]]


class DiagnosticBucket(BaseModel):
    name: str
    description: str


class DiagnosticTier(BaseModel):
    tier_level: int
    buckets: list[DiagnosticBucket]
    a_priori_probabilities: dict[str, float]


class FeatureLR(BaseModel):
    feature_name: str
    feature_category: str
    diagnostic_bucket: str
    likelihood_ratio: float


class CaseResponse(BaseModel):
    case_id: int
    case_details: CaseDetails
    diagnostic_framework: list[DiagnosticTier]
    feature_likelihood_ratios: list[FeatureLR]


class CaseOutputFiles(BaseModel):
    case_details_json: dict[str, Any]
    a_priori_probabilities_json: dict[str, Any]
    feature_likelihood_ratios_json: dict[str, Any]


class SimReadyCaseResponse(BaseModel):
    case_id: int
    saved_name: str
    output_format: str = "sim_ready"
