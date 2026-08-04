from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from backend.utils.final_orders_text import DEFAULT_SUPPRESSION_MESSAGE
from backend.models.schemas import CaseInput
from backend.models.structured_outputs import SimReadyCaseDetailsStructured


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


class FinalizeCaseResponse(BaseModel):
    """`POST /finalize-case`, sim-ready branch.

    Declared so the SPA's generate flow can read `case_id` off the response and send the
    author straight to the new case. Without a `response_model` the schema documents this
    as an empty object and the generated client cannot see any field on it — the same gap
    that made all four original SPA endpoints emit `{}` (ToDos failure 3).

    The beta branch of this endpoint returns a different shape. It is not typed here
    because no client the SPA owns calls it, and `ADR-001` is retiring that path.
    """

    case_id: int
    saved_name: str
    output_format: str = "sim_ready"
    case_version_id: int | None = None
    final_orders_saved: int = 0
    oracle_started: bool = False


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
    suppression_message: str = DEFAULT_SUPPRESSION_MESSAGE
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
    """Save an edited case that was loaded from the database.

    `save_mode` exists because the previous behaviour — always overwrite the row —
    bypassed versioning entirely (ADR-003). An edit left no record that it happened, so
    learner runs from before and after an edit were silently pooled, and the Oracle's
    structured record was left describing the pre-edit case.
    """

    saved_name: str | None = None
    content: str | None = None
    custom_input: dict[str, Any] | None = None
    custom_evaluation: dict[str, Any] | None = None
    allow_orders: bool | None = None
    learner_tasks: str | None = None

    # Default is the safe one. `in_place` is for corrections an author does not want
    # recorded as a version, and it deliberately leaves the structured record behind —
    # the Oracle's parity check then blocks until the content is re-read.
    save_mode: Literal["new_version", "in_place"] = "new_version"

    # Re-read the edited markdown into the structured record. Null means "decide from
    # whether the content actually changed", which is right almost always: an edit to
    # only the learner tasks or the Final Orders needs no model call, and an edit to the
    # case document does. True/False force it either way.
    resync_structured: bool | None = None


class CaseListItemResponse(BaseModel):
    """One row of `GET /sim-ready/cases`."""

    id: int
    saved_name: str | None = None
    allow_orders: bool | None = None


class SimReadyCaseDetailResponse(BaseModel):
    """`GET /sim-ready/case/{id}` — the row the simulator serves.

    Note this uses `id`, while the save endpoints return the same row keyed as `case_id`
    via `_sim_case_payload`. The difference is pre-existing and left alone here: the
    simulator reads this shape, so renaming the field is a cross-repo change, not a
    tidy-up. Declaring it at least makes the inconsistency visible in the schema.
    """

    id: int
    saved_name: str | None = None
    content: str | None = None
    custom_input: dict[str, Any] = Field(default_factory=dict)
    custom_evaluation: dict[str, Any] = Field(default_factory=dict)
    allow_orders: bool | None = None
    learner_tasks: str | None = None


class StructuredRecordResponse(BaseModel):
    """`GET /sim-ready/case/{id}/structured`.

    Declared rather than returned as a bare dict because these responses are the contract
    the frontend types are generated from (ADR-020). An endpoint with no `response_model`
    documents itself as an empty object, so the generator emits `{}` and every field
    access on the client is an error — the generation looks like it is protecting you
    while describing nothing.
    """

    case_id: int
    case_version_id: int
    case_family_id: int
    version: int
    title: str | None = None
    description: str = ""
    primary_diagnosis: str = ""
    oracle_specialty: str | None = None
    content_structured: dict[str, Any] = Field(default_factory=dict)
    content_rendered: str | None = None
    render_detached: bool = False
    parity_broken: bool = False
    parity_reason: str | None = None
    parity_message: str | None = None


class DiagnosticBucketOut(BaseModel):
    name: str
    description: str = ""


class DiagnosticTierOut(BaseModel):
    """One tier of the framework, with its prior over buckets.

    Priors must sum to 1.0. Validation happens at export time rather than generation, so
    a drifted tier reaches a reader intact — worth surfacing rather than silently
    normalising here.
    """

    tier_level: int
    buckets: list[DiagnosticBucketOut] = Field(default_factory=list)
    a_priori_probabilities: dict[str, float] = Field(default_factory=dict)


class FeatureLROut(BaseModel):
    """One feature's likelihood ratio for one diagnostic bucket (ADR-007).

    `provenance` is the point of showing these at all: an author needs to know whether a
    number came from the generator, a re-assessment panel, a literature anchor, or a human
    override before trusting it.
    """

    # Row id, so an in-place edit can address exactly one LR. Feature name plus bucket is
    # not a key: the same feature legitimately carries a different LR per bucket.
    id: int | None = None
    feature_name: str
    feature_category: str
    diagnostic_bucket: str
    # Nullable: the legacy beta table dropped tier_level, so older rows may not carry one.
    tier_level: int | None = None
    likelihood_ratio: float
    provenance: str = "llm_generated"


class LikelihoodRatioEdit(BaseModel):
    """One in-place LR change, addressed by row id."""

    id: int
    likelihood_ratio: float = Field(
        gt=0,
        description="A likelihood ratio is a ratio of probabilities, so it is strictly "
        "positive. 1.0 means the feature does not discriminate.",
    )


class TierPriorEdit(BaseModel):
    """Replacement priors for one tier, keyed by bucket name."""

    tier_level: int
    a_priori_probabilities: dict[str, float] = Field(default_factory=dict)


class AnalysisUpdateRequest(BaseModel):
    """`PUT /sim-ready/case/{id}/analysis` — edit LRs and priors in place (ADR-007).

    Deliberately not versioned. LRs are authoring analysis, not learner-facing content,
    so a new version per tweak would add lineage noise without protecting a learner run.
    Changed rows are stamped `author_overridden` so the edit stays visible in the data.

    Bucket names and tier structure are not editable here. Renaming a bucket orphans every
    LR pointing at the old name, and the supported repair for that is `/regenerate-lrs`,
    which re-runs generation against the current framework with exact bucket names.
    """

    feature_likelihood_ratios: list[LikelihoodRatioEdit] = Field(default_factory=list)
    diagnostic_framework: list[TierPriorEdit] = Field(default_factory=list)


class CaseAnalysisResponse(BaseModel):
    """`GET /sim-ready/case/{id}/analysis` — framework and LR data for the latest version.

    Typed element-wise rather than as `list[dict[str, Any]]`, which the generator renders
    as `Record<string, never>` — a type that describes nothing and blocks every field
    access on the client. Same failure as the endpoints that had no `response_model` at
    all (ToDos failure 3), just one level down.
    """

    case_version_id: int
    case_family_id: int
    version: int
    primary_diagnosis: str | None = None
    render_detached: bool = False
    diagnostic_framework: list[DiagnosticTierOut] = Field(default_factory=list)
    feature_likelihood_ratios: list[FeatureLROut] = Field(default_factory=list)


class OracleItemPreviewRequest(BaseModel):
    """Render the learner-facing items for a set of Final Orders.

    Exists so no client reimplements the stem. Batched over `orders` because the authoring
    UI previews every order on the case together, and a per-order round trip is exactly
    the friction that makes reimplementing it look reasonable.
    """

    orders: list[FinalOrderInput] = Field(
        default_factory=list, max_length=MAX_FINAL_ORDERS
    )
    stem_version: str | None = Field(
        default=None,
        description="Null uses the backend's active stem. Pass a version only to preview "
        "an alternative; the panel always runs the configured one.",
    )


class SimReadyStructuredUpdateRequest(BaseModel):
    """Save structured field edits. The renderer produces the markdown (ADR-002).

    The inverse of `SimReadyCaseUpdateRequest`, which takes markdown in and spends a model
    call re-reading it back into the record. Here the record *is* the input, so there is
    nothing to re-read: rendering is deterministic, the projection cannot drift from its
    source, and the save costs no LLM call at all.

    `content_structured` is typed rather than a free dict on purpose. It is the contract
    the generated frontend types are built from (ADR-020), so a field renamed in
    `SimReadyCaseDetailsStructured` has to surface as a validation error here and a type
    error there, instead of as a key that silently stops arriving.
    """

    content_structured: SimReadyCaseDetailsStructured

    # Editable alongside the clinical record because they live on the same screen. Null
    # means "leave as is" for each, so a caller can save the case document without
    # restating the simulator fields.
    saved_name: str | None = None
    custom_input: dict[str, Any] | None = None
    custom_evaluation: dict[str, Any] | None = None
    allow_orders: bool | None = None
    learner_tasks: str | None = None

    # Metadata carried on the version rather than the simulator row.
    description: str | None = None
    primary_diagnosis: str | None = None
    oracle_specialty: str | None = None


class DetachedPanelRun(BaseModel):
    """A Final Order that was removed while carrying completed panel runs."""

    final_order_id: int
    order_text: str
    panel_runs_detached: int


class FinalOrdersUpdateResponse(BaseModel):
    """`PUT /sim-ready/case/{id}/final-orders`.

    `detached_panel_runs` is the reason this model exists. Removing a rated order leaves
    its completed runs with no live item — permitted, since the author's list is
    authoritative, but never silent. Without a declared `response_model` the field would
    be invisible to the generated TypeScript, so the editor could not surface it and the
    warning would exist only in the server log.
    """

    case_id: int
    case_version_id: int
    oracle_specialty: str | None = None
    final_orders: list[dict[str, Any]] = Field(default_factory=list)
    oracle_started: bool = False
    detached_panel_runs: list[DetachedPanelRun] = Field(default_factory=list)


class SuppressionTermGroup(BaseModel):
    """Everything the simulator should treat as one Final Order."""

    final_order_id: int
    terms: list[str] = Field(default_factory=list)
    message: str | None = None


class FinalOrdersResponse(BaseModel):
    """`GET /sim-ready/case/{id}/final-orders`.

    Unauthenticated, and stays that way: this is the shape the simulator reads
    (direct-sim/FINAL_ORDERS_TODO.md). A case with no authoring record returns an empty
    list rather than 404 — the simulator must treat that as "behave exactly as before".
    """

    case_id: int
    case_version_id: int | None = None
    oracle_specialty: str | None = None
    final_orders: list[dict[str, Any]] = Field(default_factory=list)
    suppression_terms: list[SuppressionTermGroup] = Field(default_factory=list)


class SuggestSynonymsRequest(BaseModel):
    """`POST /final-orders/suggest-synonyms` — suggestions only, writes nothing.

    Takes the author's current buffer rather than a case id, so the suggestions apply to
    what is on screen including unsaved edits.
    """

    orders: list[FinalOrderInput] = Field(
        default_factory=list, max_length=MAX_FINAL_ORDERS
    )


class SuggestedSynonymsResponse(BaseModel):
    """Alternate phrasings per order, for the author to accept or edit.

    The synonym list is what the simulator's pre-model interception matches on, so an
    empty list means the result reaches the learner and the rating for that order is
    worthless. This endpoint exists because orders authored before that was enforced have
    no synonyms at all.
    """

    suggestions: list[dict[str, Any]] = Field(default_factory=list)


class ProposedFinalOrdersResponse(BaseModel):
    """`POST /final-orders/propose` — candidates only. Writes nothing.

    The author accepts explicitly; provenance records which orders came from the model
    so a self-fulfilling distribution stays testable (ADR-004).
    """

    candidates: list[dict[str, Any]] = Field(default_factory=list)


class OracleRenderedItem(BaseModel):
    """One Final Order rendered into the rating stem the panel will actually see."""

    final_order_id: int | None = None
    order_text: str | None = None
    provenance: str | None = None
    oracle_item: str | None = None
    learner_item: str | None = None


class OraclePreflightResponse(BaseModel):
    """`GET /sim-ready/case/{id}/oracle/preflight` — everything checkable before spending
    a model call.

    `ready: false` with reason `diagnosis_leak` is overridable with a recorded reason;
    `content_drift` and `render_detached` are not, because the panel would be rating a
    case the learner will not see (ADR-017).
    """

    case_id: int | None = None
    case_version_id: int | None = None
    ready: bool = False
    reason: str | None = None
    message: str | None = None
    estimated_calls: int = 0
    content_parity: dict[str, Any] | None = None
    leak_audit: dict[str, Any] | None = None
    blinded_context: str | None = None
    blinded_context_hash: str | None = None
    included_sections: list[str] = Field(default_factory=list)
    excluded_sections: list[str] = Field(default_factory=list)
    suppressed_tests: list[str] = Field(default_factory=list)
    primary_diagnosis_withheld: bool = False
    stem_version: str | None = None
    stem_label: str | None = None
    panel_roster_version: str | None = None
    roster_specialty: str | None = None
    roster: list[dict[str, Any]] = Field(default_factory=list)
    settings: dict[str, Any] | None = None
    items: list[OracleRenderedItem] = Field(default_factory=list)


class OracleItemResult(BaseModel):
    """One Final Order and its current panel run, if any."""

    final_order: dict[str, Any]
    run: dict[str, Any] | None = None
    # Recomputed from the stored per-rating rows on read, so the scoring rule can change
    # without regenerating data (ADR-006).
    aggregate: dict[str, Any] | None = None
    # True when the run predates the current version's content hash (ADR-003).
    stale: bool = False


class OracleResultsResponse(BaseModel):
    """`GET /sim-ready/case/{id}/oracle` — distributions and item-quality flags."""

    case_id: int | None = None
    case_version_id: int | None = None
    primary_diagnosis: str | None = None
    items: list[OracleItemResult] = Field(default_factory=list)


class AuthCheckResponse(BaseModel):
    """Result of validating a credential, for the SPA's login form (ADR-021)."""

    authenticated: bool
    username: str


class SimReadyRenderPreviewRequest(BaseModel):
    """Render a structured record to markdown without saving anything."""

    content_structured: SimReadyCaseDetailsStructured


class SimReadyRenderPreviewResponse(BaseModel):
    """The markdown a save would write, produced by the same renderer the save uses.

    The editor needs a preview, and the only correct preview is one the server produces:
    a client-side reimplementation is a second renderer that drifts. That is not
    hypothetical here — `POST /oracle/render-items` exists precisely because the SPA's
    client-side mirror of the stem renderer had already drifted, and authors were
    reviewing a sentence learners would never see (ADR-020).
    """

    content_rendered: str
    # Cheap structural assertions the editor can surface without parsing the markdown.
    # The delimiter is load-bearing: the simulator splits on it, so a record that fails
    # to produce one would save a document the simulator cannot read.
    door_chart_delimiter_present: bool
    character_count: int


class SimReadyCaseCopyRequest(BaseModel):
    """Fork an edited case into a new simulator row and a new case family.

    For a genuine variant of a case, not for an edit — an edit is a new version of the
    same family, so learner performance stays attributable to one case concept.
    """

    saved_name: str = Field(
        min_length=1,
        description="Name for the new case. Required: the point is that it "
        "is distinguishable from the case it was forked from.",
    )
    content: str | None = None
    custom_input: dict[str, Any] | None = None
    custom_evaluation: dict[str, Any] | None = None
    allow_orders: bool | None = None
    learner_tasks: str | None = None
    resync_structured: bool | None = None

    @field_validator("saved_name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("saved_name cannot be blank")
        return value.strip()


class AdoptCaseRequest(BaseModel):
    """Give a pre-authoring-record case its first `case_version`, read from its markdown.

    Cases finalized before `authoring.case_versions` existed have no structured record,
    which blocks Final Orders and the Oracle: both resolve through the latest version.
    Re-sync cannot help — it starts from a version there is none of.

    `primary_diagnosis` is required, and required for a safety reason rather than a
    bookkeeping one. The Oracle's leak audit builds its search terms from this field, and
    an empty string produces an empty term list, so the audit would pass without checking
    anything (`blinded_context.audit_leak`). Adopting without it would turn the panel's
    main safety control into a silent no-op on exactly the cases nobody has reviewed.
    """

    primary_diagnosis: str = Field(
        min_length=1,
        description="The case's actual diagnosis. Withheld from the panel; used to audit "
        "the blinded context for leaks.",
    )
    title: str | None = None
    description: str | None = None
    oracle_specialty: str | None = None

    @field_validator("primary_diagnosis")
    @classmethod
    def _diagnosis_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "primary_diagnosis cannot be blank: the Oracle's leak audit has nothing "
                "to check without it"
            )
        return value.strip()


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
