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


MAX_FINAL_ORDERS = 5


class FinalOrderInput(BaseModel):
    """One Final Order as submitted by the author.

    `provenance` distinguishes an order the author wrote from one the generator proposed
    and the author accepted. It matters because if the same model family both proposes an
    order and rates its appropriateness, the resulting distribution is partly
    self-fulfilling — recording provenance makes that testable (ADR-004).
    """

    order_text: str = Field(
        min_length=1,
        description="Short label, e.g. 'Brain MRI'. Used for display and for the "
        "simulator's suppression match.",
    )
    display_order: int | None = None
    stem_action: str | None = Field(
        default=None,
        description="The action as a gerund phrase for the stem, e.g. 'ordering a brain "
        "MRI' or 'activating the stroke team'. Null derives one from order_text, which "
        "is correct for tests and treatments but not for activations.",
    )
    stem_template: str | None = Field(
        default=None,
        description="Optional per-order override of the stem lead. Must contain the "
        "{action} placeholder. The scale anchors are never overridable.",
    )
    provenance: str = Field(
        default="author_entered",
        pattern="^(author_entered|llm_suggested_accepted)$",
    )
    suppress_results: bool = True
    suppression_message: str = "Result pending"
    suppression_synonyms: list[str] = Field(
        default_factory=list,
        description="Alternate phrasings the simulator also suppresses. Author-supplied "
        "because conservative explicit matching beats fuzzy similarity here: a false "
        "positive degrades the simulation, a false negative destroys the measurement.",
    )


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

    # An empty list means "this case has no Final Orders", which is the supported and
    # expected case: no Final Orders means no script concordance item and no Oracle
    # panel (ADR-014). The cap is enforced here rather than only in the UI because it
    # bounds Oracle cost at 5 x 15 = 75 calls per case.
    final_orders: list[FinalOrderInput] = Field(
        default_factory=list, max_length=MAX_FINAL_ORDERS
    )
    # Which specialty fills the applicable-subspecialist seat on the Oracle roster.
    oracle_specialty: str | None = None
    # Start the Oracle panel in the background after saving. Ignored when the case has
    # no Final Orders.
    run_oracle: bool = False


class SimReadyCaseUpdateRequest(BaseModel):
    saved_name: str | None = None
    content: str | None = None
    custom_input: dict[str, Any] | None = None
    custom_evaluation: dict[str, Any] | None = None
    allow_orders: bool | None = None
    learner_tasks: str | None = None


class FinalOrdersUpdateRequest(BaseModel):
    """Replace the Final Orders on a case version.

    Replace rather than patch: the submitted list is the author's authoritative statement
    of what the case has, so a deleted order actually disappears.
    """

    final_orders: list[FinalOrderInput] = Field(
        default_factory=list, max_length=MAX_FINAL_ORDERS
    )
    oracle_specialty: str | None = None
    run_oracle: bool = False


class OracleRunRequest(BaseModel):
    """Optional body for starting an Oracle panel.

    `leak_override_reason` is the only way past a failing diagnosis-leak audit, and it is
    stored on every run it produces. The audit stays blocking by default; this makes the
    exception visible in the data instead of tempting anyone to weaken the check.
    """

    leak_override_reason: str | None = None


class ProposeFinalOrdersRequest(BaseModel):
    """Ask the generator for candidate Final Orders. Writes nothing."""

    session_id: str | None = None
    case_details: dict[str, Any] | None = None
    primary_diagnosis: str | None = None
    max_candidates: int = Field(default=MAX_FINAL_ORDERS, ge=1, le=MAX_FINAL_ORDERS)


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
