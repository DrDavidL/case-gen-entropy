from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional
from backend.models.schemas import CaseInput

class CasePreviewResponse(BaseModel):
    session_id: str = Field(description="Temporary session ID for editing")
    case_details: Dict[str, Any]
    diagnostic_framework: List[Dict[str, Any]]
    feature_likelihood_ratios: List[Dict[str, Any]]

class SimReadyCasePreviewResponse(CasePreviewResponse):
    rendered_content: str = Field(description="Rendered markdown content for preview")
    default_custom_input: Dict[str, Any] = Field(description="Default custom_input JSON")
    default_custom_evaluation: Dict[str, Any] = Field(description="Default custom_evaluation JSON")
    default_learner_tasks: str = Field(description="Default learner tasks markdown")

class EditableCaseDetails(BaseModel):
    presentation: str
    patient_personality: str
    history_questions: List[Dict[str, str]]
    physical_exam_findings: List[Dict[str, str]]
    diagnostic_workup: List[Dict[str, str]]

class EditableDiagnosticBucket(BaseModel):
    name: str
    description: str

class EditableDiagnosticTier(BaseModel):
    tier_level: int
    buckets: List[EditableDiagnosticBucket]
    a_priori_probabilities: Dict[str, float]

class EditableFeatureLR(BaseModel):
    feature_name: str
    feature_category: str
    diagnostic_bucket: str
    tier_level: int
    likelihood_ratio: float

class CaseEditRequest(BaseModel):
    session_id: str
    case_details: Optional[EditableCaseDetails] = None
    diagnostic_framework: Optional[List[EditableDiagnosticTier]] = None
    feature_likelihood_ratios: Optional[List[EditableFeatureLR]] = None

class CaseSaveRequest(BaseModel):
    session_id: str
    title: Optional[str] = None
    description: str
    primary_diagnosis: str
    output_format: str = "sim_ready"  # "sim_ready" (default) or "beta"
    allow_orders: bool = True
    learner_tasks: Optional[str] = None
    custom_input: Optional[Dict[str, Any]] = None
    custom_evaluation: Optional[Dict[str, Any]] = None
    rendered_content: Optional[str] = None  # User-edited content override for sim-ready

class SimReadyCaseUpdateRequest(BaseModel):
    saved_name: Optional[str] = None
    content: Optional[str] = None
    custom_input: Optional[Dict[str, Any]] = None
    custom_evaluation: Optional[Dict[str, Any]] = None
    allow_orders: Optional[bool] = None
    learner_tasks: Optional[str] = None


class SessionData(BaseModel):
    case_details: Dict[str, Any]
    diagnostic_framework: List[Dict[str, Any]]
    feature_likelihood_ratios: List[Dict[str, Any]]
    original_input: CaseInput
    output_format: str = "beta"  # default "beta" for backward compat with existing sessions