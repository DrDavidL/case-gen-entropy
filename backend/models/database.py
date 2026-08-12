import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import QueuePool

from backend.utils.final_orders_text import DEFAULT_SUPPRESSION_MESSAGE


logger = logging.getLogger(__name__)

load_dotenv()

DATABASE_URL = os.getenv("POSTGRES_URL")

if not DATABASE_URL:
    raise ValueError("POSTGRES_URL environment variable is required")

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"sslmode": "require"} if "sslmode" not in DATABASE_URL else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Sim-Ready Database (optional, for simulator-compatible case output) ---
SIM_READY_DATABASE_URL = os.getenv("POSTGRES_URL_SIM_READY")

sim_ready_engine = None
SimReadySessionLocal = None
SimReadyBase = declarative_base()

if SIM_READY_DATABASE_URL:
    sim_ready_engine = create_engine(
        SIM_READY_DATABASE_URL,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={"sslmode": "require"}
        if "sslmode" not in SIM_READY_DATABASE_URL
        else {},
    )
    SimReadySessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=sim_ready_engine
    )


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(Text)
    primary_diagnosis = Column(String)
    case_details = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    frameworks = relationship(
        "DiagnosticFramework", back_populates="case", lazy="selectin"
    )
    feature_lrs = relationship(
        "FeatureLikelihoodRatio", back_populates="case", lazy="selectin"
    )


class DiagnosticFramework(Base):
    __tablename__ = "diagnostic_frameworks"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"), index=True)
    tier_level = Column(Integer)
    diagnostic_buckets = Column(JSON)
    a_priori_probabilities = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    case = relationship("Case", back_populates="frameworks")


class FeatureLikelihoodRatio(Base):
    __tablename__ = "feature_likelihood_ratios"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"), index=True)
    framework_id = Column(Integer, index=True)
    feature_name = Column(String)
    feature_category = Column(String)
    diagnostic_bucket = Column(String)
    likelihood_ratio = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    case = relationship("Case", back_populates="feature_lrs")


class CaseDetailSimReady(SimReadyBase):
    __tablename__ = "case_details"

    id = Column(Integer, primary_key=True, autoincrement=True)
    saved_name = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    custom_input = Column(JSON, nullable=True)
    custom_evaluation = Column(JSON, nullable=True)
    allow_orders = Column(Boolean, nullable=False, default=True)
    learner_tasks = Column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Authoring schema — the canonical case record (Decisions.md ADR-001, ADR-003)
#
# These tables live in the SHARED database alongside `case_details`, so clinical
# content and LR/entropy data can finally be joined. They are the destination the
# beta tables above will migrate into; new writes land here directly rather than
# building a cross-database bridge we would then demolish.
# ---------------------------------------------------------------------------

AUTHORING_SCHEMA = "authoring"


def _tables_present(bind, required: set[str], feature: str, consequence: str) -> bool:
    """Report whether every table in `required` exists under the authoring schema.

    These tables are owned by Alembic in the direct-sim repo, because that repo owns
    the shared database's migration history. This app detects and adapts rather than
    running DDL, so the two never race and the schema has exactly one source of truth.

    Returns False on any error — case generation must keep working without it.
    """
    if bind is None:
        return False
    try:
        names = set(inspect(bind).get_table_names(schema=AUTHORING_SCHEMA))
        missing = required - names
        if missing:
            logger.warning(
                "%s schema incomplete (missing: %s). Run 'alembic upgrade head' in the "
                "direct-sim repo. %s",
                feature,
                ", ".join(sorted(missing)),
                consequence,
            )
            return False
        return True
    except Exception as e:
        logger.error(
            "Could not inspect '%s' schema; %s disabled: %s",
            AUTHORING_SCHEMA,
            feature,
            str(e)[:200],
        )
        return False


def authoring_schema_ready(bind) -> bool:
    """Report whether the core authoring tables exist (revision 0002)."""
    return _tables_present(
        bind,
        {
            "case_families",
            "case_versions",
            "diagnostic_frameworks",
            "feature_likelihood_ratios",
        },
        "Authoring",
        "Framework/LR data will not be persisted.",
    )


def final_orders_schema_ready(bind) -> bool:
    """Report whether the Final Orders / panel tables exist (revision 0003).

    Probed separately from `authoring_schema_ready` on purpose: a deploy that has 0002
    but not 0003 must keep persisting framework and LR data. Folding these names into
    the core check would disable all authoring persistence over a missing Phase 2/3
    migration, which is a strictly worse failure than losing the new features alone.
    """
    return _tables_present(
        bind,
        {"case_final_orders", "panel_runs", "panel_ratings"},
        "Final Orders / panel",
        "Final Orders and the Oracle panel will be unavailable.",
    )


def panel_run_snapshot_ready(bind) -> bool:
    """Report whether `panel_runs` has the claim-snapshot columns (revision 0004).

    Probed at column level rather than table level because 0004 is additive to a table
    0003 already created. Without this, a deploy that ran ahead of the migration would
    fail every Oracle run outright on an unknown column, turning a missing nice-to-have
    into a total outage of the feature. Degrading to "runs work, snapshots are null"
    keeps the same failure proportionate to what is actually missing, and matches how
    `authoring_schema_ready` and `final_orders_schema_ready` already behave.
    """
    if bind is None:
        return False
    try:
        columns = {
            c["name"]
            for c in inspect(bind).get_columns("panel_runs", schema=AUTHORING_SCHEMA)
        }
        missing = {"item_label", "item_snapshot"} - columns
        if missing:
            logger.warning(
                "panel_runs is missing %s. Run 'alembic upgrade head' in the direct-sim "
                "repo. Panel runs will not record what they rated, so an edited or "
                "deleted Final Order will leave them uninterpretable.",
                ", ".join(sorted(missing)),
            )
            return False
        return True
    except Exception as e:
        logger.error("Could not inspect panel_runs columns: %s", str(e)[:200])
        return False


class CaseFamily(SimReadyBase):
    """A stable case concept, e.g. 'Dizziness — posterior circulation stroke'.

    Versions belong to a family; learner runs and LLM panels reference a version,
    never a family. See ADR-003.
    """

    __tablename__ = "case_families"
    __table_args__ = {"schema": AUTHORING_SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String, nullable=False, unique=True, index=True)
    title = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    versions = relationship("CaseVersion", back_populates="family", lazy="selectin")


class CaseVersion(SimReadyBase):
    """One immutable published snapshot of a case."""

    __tablename__ = "case_versions"
    __table_args__ = (
        UniqueConstraint("case_family_id", "version", name="uq_case_version"),
        {"schema": AUTHORING_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_family_id = Column(
        Integer,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_families.id"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False, default=1)
    status = Column(
        String, nullable=False, default="published"
    )  # draft|published|retired

    title = Column(String)
    description = Column(Text)
    primary_diagnosis = Column(String)

    # Canonical clinical record. `content_rendered` is a projection of this (ADR-002).
    content_structured = Column(JSONB)
    content_rendered = Column(Text)
    render_detached = Column(Boolean, nullable=False, default=False)

    # Lineage for "save as new with variables changed".
    parent_version_id = Column(
        Integer, ForeignKey(f"{AUTHORING_SCHEMA}.case_versions.id"), nullable=True
    )

    # Deliberately NOT a ForeignKey: `case_details` is the simulator's table and we
    # will not constrain it from here.
    case_detail_id = Column(Integer, nullable=True, index=True)

    output_format = Column(String, nullable=False, default="sim_ready")

    # Which specialty fills the "applicable specialty surgeon or subspecialist" seat on
    # the Oracle roster for this case — otolaryngology for dizziness, vascular surgery
    # for a limb-ischemia case, and so on (ADR-014). Null falls back to the roster
    # default. Authored here; recorded per run on `panel_runs.roster_specialty`.
    oracle_specialty = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)

    family = relationship("CaseFamily", back_populates="versions")
    frameworks = relationship(
        "AuthoringDiagnosticFramework", back_populates="case_version", lazy="selectin"
    )
    feature_lrs = relationship(
        "AuthoringFeatureLikelihoodRatio",
        back_populates="case_version",
        lazy="selectin",
    )
    final_orders = relationship(
        "CaseFinalOrder",
        back_populates="case_version",
        lazy="selectin",
        order_by="CaseFinalOrder.display_order",
    )


class AuthoringDiagnosticFramework(SimReadyBase):
    __tablename__ = "diagnostic_frameworks"
    __table_args__ = {"schema": AUTHORING_SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_version_id = Column(
        Integer,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_versions.id"),
        nullable=False,
        index=True,
    )
    tier_level = Column(Integer)
    diagnostic_buckets = Column(JSONB)
    a_priori_probabilities = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow)

    case_version = relationship("CaseVersion", back_populates="frameworks")


class AuthoringFeatureLikelihoodRatio(SimReadyBase):
    __tablename__ = "feature_likelihood_ratios"
    __table_args__ = {"schema": AUTHORING_SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_version_id = Column(
        Integer,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_versions.id"),
        nullable=False,
        index=True,
    )
    feature_name = Column(String)
    feature_category = Column(String)
    diagnostic_bucket = Column(String)
    # The legacy beta table drops tier_level even though the LLM produces it; keep it here.
    tier_level = Column(Integer)
    likelihood_ratio = Column(Float)
    # llm_generated | llm_reassessed | author_overridden | literature_anchored (ADR-007)
    provenance = Column(String, nullable=False, default="llm_generated")
    created_at = Column(DateTime, default=datetime.utcnow)

    case_version = relationship("CaseVersion", back_populates="feature_lrs")


# ---------------------------------------------------------------------------
# Final Orders + the LLM panel subsystem (ADR-004, ADR-005, ADR-006, ADR-014)
#
# Alembic revision 0003_final_orders_and_panels in the direct-sim repo owns this
# DDL. Probe with `final_orders_schema_ready()` before touching these tables.
# ---------------------------------------------------------------------------


class CaseFinalOrder(SimReadyBase):
    """One author-chosen clinical action whose appropriateness a learner rates.

    Version-pinned rather than pointing at `case_details`: a case's Final Orders are
    part of the snapshot a learner saw, and a later edit must not retroactively change
    what was measured (ADR-003). The simulator resolves them through the latest
    `case_versions` row for a `case_detail_id` — see `load_final_orders_for_case_detail`.

    Zero rows means the case has no Final Orders, and therefore no script concordance
    item and no Oracle panel (ADR-014). That is the whole opt-out mechanism; there is
    deliberately no global toggle.
    """

    __tablename__ = "case_final_orders"
    __table_args__ = {"schema": AUTHORING_SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_version_id = Column(
        Integer,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_versions.id"),
        nullable=False,
        index=True,
    )
    display_order = Column(Integer, nullable=False, default=1)
    order_text = Column(Text, nullable=False)

    # The action as a gerund phrase, inserted into the stem: "ordering a brain MRI",
    # "activating the stroke team". Stored rather than derived because deriving it from
    # `order_text` gets activations and consults wrong — "Stroke team activation" would
    # render as "ordering a stroke team activation". Null falls back to the derivation,
    # which is correct for the tests and treatments that make up most Final Orders.
    stem_action = Column(Text, nullable=True)

    # Optional per-order override of the default stem lead. Null means "use the registry
    # default for `stem_version`" — see backend/utils/oracle_stems.py.
    stem_template = Column(Text, nullable=True)

    # author_entered | llm_suggested_accepted. Recorded because if the same model family
    # both proposes an order and rates its appropriateness, the distribution is partly
    # self-fulfilling; provenance lets that be tested rather than argued about.
    provenance = Column(String, nullable=False, default="author_entered")

    suppress_results = Column(Boolean, nullable=False, default=True)
    # What the learner reads when they order this during the encounter. Phrased as a
    # resource constraint rather than "pending": it must not promise a result that never
    # comes, and it must not announce which orders are being measured. See
    # `direct-sim/backend/final_orders.py` `DEFAULT_SUPPRESSION_MESSAGE`, which resolves
    # the older "Result pending" rows to this same wording.
    suppression_message = Column(
        Text,
        nullable=False,
        default=DEFAULT_SUPPRESSION_MESSAGE,
    )
    # Author-supplied alternate phrasings the simulator also suppresses. Explicit and
    # conservative on purpose: a false positive degrades the simulation, a false
    # negative destroys the measurement.
    suppression_synonyms = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case_version = relationship("CaseVersion", back_populates="final_orders")


class PanelRun(SimReadyBase):
    """One fan-out of a claim to N independent blinded raters.

    Serves both the Oracle and LR re-assessment through `item_type` (ADR-006).
    Append-only: a re-run creates a new row and points the old one at it via
    `superseded_by`. For a research dataset, what was generated and when is part of
    the data, so nothing is overwritten.
    """

    __tablename__ = "panel_runs"
    __table_args__ = {"schema": AUTHORING_SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 'final_order_appropriateness' | 'lr_reassessment'
    item_type = Column(String, nullable=False, index=True)
    # Deliberately not a ForeignKey: it points into a different table per item_type.
    item_ref_id = Column(Integer, nullable=False, index=True)

    case_version_id = Column(
        Integer,
        ForeignKey(f"{AUTHORING_SCHEMA}.case_versions.id"),
        nullable=False,
        index=True,
    )

    panel_size_requested = Column(Integer, nullable=False)
    # Realized N is stored separately and is what every denominator uses. We will not
    # rate fourteen panelists and report fifteen.
    panel_size_realized = Column(Integer, nullable=False, default=0)

    model = Column(String)
    reasoning_effort = Column(String)
    provider = Column(String, default="openai")
    api_surface = Column(String, default="responses")
    prompt_template_version = Column(String)
    stem_version = Column(String)
    panel_roster_version = Column(String)
    # Which specialty filled the applicable-subspecialist seat for this run (ADR-014).
    roster_specialty = Column(String, nullable=True)

    blinded_context_hash = Column(String)
    claim_hash = Column(String)

    # What this run rated, snapshotted at creation (revision 0004). `item_ref_id` is not
    # a foreign key -- it points into a different table per `item_type` -- so the row it
    # names can be deleted out from under a completed run. Without these, the run's
    # meaning would go with it: `claim_hash` survives but cannot tell a reader what the
    # panel was asked. A run must stay interpretable forever, whatever happens to the item.
    item_label = Column(Text, nullable=True)
    item_snapshot = Column(JSONB, nullable=True)

    # Set when an author ran the panel despite a failing leak audit, with their stated
    # reason. The audit blocks by default and stays blocking; this records the exception
    # in the research data rather than letting it happen invisibly. Expect legitimate
    # uses — "CVA appears only as the father's history" — and expect a reviewer to ask.
    leak_override_reason = Column(Text, nullable=True)

    status = Column(
        String, nullable=False, default="pending"
    )  # pending|running|complete|failed
    error = Column(Text, nullable=True)
    superseded_by = Column(
        Integer, ForeignKey(f"{AUTHORING_SCHEMA}.panel_runs.id"), nullable=True
    )

    # Convenience copy. `panel_ratings` rows are authoritative; aggregates are
    # recomputed on read so the scoring rule can change without regenerating data.
    aggregates = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    ratings = relationship(
        "PanelRating",
        back_populates="run",
        lazy="selectin",
        order_by="PanelRating.panelist_index",
    )


class PanelRating(SimReadyBase):
    """One panelist's rating within a run. The source of truth for any aggregate."""

    __tablename__ = "panel_ratings"
    __table_args__ = {"schema": AUTHORING_SCHEMA}

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        Integer,
        ForeignKey(f"{AUTHORING_SCHEMA}.panel_runs.id"),
        nullable=False,
        index=True,
    )

    panelist_index = Column(Integer, nullable=False)
    persona_id = Column(String)
    persona_hash = Column(String)
    # The model that actually produced this rating. Authoritative: a run spans more than
    # one family (ADR-018), so `panel_runs.model` names only the primary.
    model = Column(String)

    # Shape varies by item_type: {"rating": int} for the Oracle,
    # {"lr_low": float, "lr_high": float, ...} for LR re-assessment.
    value = Column(JSONB, nullable=True)
    rationale = Column(Text, nullable=True)
    # Top 2-3 diagnostic concerns. Drives the transparency signal: if >80% of panelists
    # name the ground truth, the case is diagnostically transparent.
    top_concerns = Column(JSONB, nullable=True)

    # ok | parse_error | truncated | empty_response | refusal | content_filter |
    # api_error. Anything but 'ok' is a null-outcome row,
    # excluded from the denominator rather than dropped.
    status = Column(String, nullable=False, default="ok")
    error = Column(Text, nullable=True)

    raw_response_id = Column(String, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    run = relationship("PanelRun", back_populates="ratings")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_sim_ready_db():
    if SimReadySessionLocal is None:
        raise ValueError("POSTGRES_URL_SIM_READY is not configured")
    db = SimReadySessionLocal()
    try:
        yield db
    finally:
        db.close()
