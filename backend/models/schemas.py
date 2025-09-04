from pydantic import BaseModel
from typing import Dict, List, Any, Optional
from datetime import datetime

class CaseInput(BaseModel):
    description: str
    primary_diagnosis: str

class CaseDetails(BaseModel):
    presentation: str
    patient_personality: str
    history_questions: List[Dict[str, str]]
    physical_exam_findings: List[Dict[str, str]]
    diagnostic_workup: List[Dict[str, str]]

class DiagnosticBucket(BaseModel):
    name: str
    description: str

class DiagnosticTier(BaseModel):
    tier_level: int
    buckets: List[DiagnosticBucket]
    a_priori_probabilities: Dict[str, float]

class FeatureLR(BaseModel):
    feature_name: str
    feature_category: str
    diagnostic_bucket: str
    likelihood_ratio: float

class CaseResponse(BaseModel):
    case_id: int
    case_details: CaseDetails
    diagnostic_framework: List[DiagnosticTier]
    feature_likelihood_ratios: List[FeatureLR]
    
class CaseOutputFiles(BaseModel):
    case_details_json: Dict[str, Any]
    a_priori_probabilities_json: Dict[str, Any]
    feature_likelihood_ratios_json: Dict[str, Any]