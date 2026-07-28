from typing import Any

from pydantic import BaseModel, Field

from backend.models.schemas import CaseInput


class CasePreviewResponse(BaseModel):
    session_id: str = Field(description="Temporary session ID for editing")
    case_details: dict[str, Any]
    diagnostic_framework: list[dict[str, Any]]
    feature_likelihood_ratios: list[dict[str, Any]]


class SimReadyCasePreviewResponse(CasePreviewResponse):
    rendered_content: str = Field(description="Rendered markdown content for preview")
    default_custom_input: dict[str, Any] = Field(
        description="Default custom_input JSON"
    )
    default_custom_evaluation: dict[str, Any] = Field(
        description="Default custom_evaluation JSON"
    )
    default_learner_tasks: str = Field(description="Default learner tasks markdown")


class EditableCaseDetails(BaseModel):
    presentation: str
    patient_personality: str
    history_questions: list[dict[str, str]]
    physical_exam_findings: list[dict[str, str]]
    diagnostic_workup: list[dict[str, str]]


class EditableDiagnosticBucket(BaseModel):
    name: str
    description: str


class EditableDiagnosticTier(BaseModel):
    tier_level: int
    buckets: list[EditableDiagnosticBucket]
    a_priori_probabilities: dict[str, float]


class EditableFeatureLR(BaseModel):
    feature_name: str
    feature_category: str
    diagnostic_bucket: str
    tier_level: int
    likelihood_ratio: float


class CaseEditRequest(BaseModel):
    session_id: str
    case_details: EditableCaseDetails | None = None
    diagnostic_framework: list[EditableDiagnosticTier] | None = None
    feature_likelihood_ratios: list[EditableFeatureLR] | None = None


class CaseSaveRequest(BaseModel):
    session_id: str
    title: str | None = None
    description: str
    primary_diagnosis: str
    output_format: str = "sim_ready"  # "sim_ready" (default) or "beta"
    allow_orders: bool = True
    learner_tasks: str | None = None
    custom_input: dict[str, Any] | None = None
    custom_evaluation: dict[str, Any] | None = None
    rendered_content: str | None = None  # User-edited content override for sim-ready


class SimReadyCaseUpdateRequest(BaseModel):
    saved_name: str | None = None
    content: str | None = None
    custom_input: dict[str, Any] | None = None
    custom_evaluation: dict[str, Any] | None = None
    allow_orders: bool | None = None
    learner_tasks: str | None = None


class SessionData(BaseModel):
    case_details: dict[str, Any]
    diagnostic_framework: list[dict[str, Any]]
    feature_likelihood_ratios: list[dict[str, Any]]
    original_input: CaseInput
    output_format: str = (
        "beta"  # default "beta" for backward compat with existing sessions
    )


class RegenerateLRRequest(BaseModel):
    """Re-run likelihood-ratio generation against the current session.

    Ported from the pre-divergence `main` lineage. Falls back to an explicit
    case/framework payload when the Redis session has expired.
    """

    session_id: str
    case_details: dict[str, Any] | None = None
    diagnostic_framework: list[dict[str, Any]] | None = None


class RegenerateLRResponse(BaseModel):
    feature_likelihood_ratios: list[dict[str, Any]]
