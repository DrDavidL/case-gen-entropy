from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional
from backend.models.schemas import CaseInput

class CasePreviewResponse(BaseModel):
    session_id: str = Field(description="Temporary session ID for editing")
    case_details: Dict[str, Any]
    diagnostic_framework: List[Dict[str, Any]]
    feature_likelihood_ratios: List[Dict[str, Any]]
    
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
    # Optional draft metadata updates
    title: Optional[str] = None
    description: Optional[str] = None
    primary_diagnosis: Optional[str] = None

class CaseSaveRequest(BaseModel):
    session_id: str
    title: Optional[str] = None
    description: str
    primary_diagnosis: str
    case_details: Optional[Dict[str, Any]] = None
    diagnostic_framework: Optional[List[Dict[str, Any]]] = None
    feature_likelihood_ratios: Optional[List[Dict[str, Any]]] = None

class SessionData(BaseModel):
    case_details: Dict[str, Any]
    diagnostic_framework: List[Dict[str, Any]]
    feature_likelihood_ratios: List[Dict[str, Any]]
    original_input: CaseInput
    # Optional draft metadata stored during editing
    title: Optional[str] = None
    description: Optional[str] = None
    primary_diagnosis: Optional[str] = None

class RegenerateLRRequest(BaseModel):
    session_id: str
    case_details: Optional[Dict[str, Any]] = None
    diagnostic_framework: Optional[List[Dict[str, Any]]] = None

class RegenerateLRResponse(BaseModel):
    feature_likelihood_ratios: List[Dict[str, Any]]
