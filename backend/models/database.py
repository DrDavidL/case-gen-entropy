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


def authoring_schema_ready(bind) -> bool:
    """Report whether the authoring tables exist. Never creates them.

    These tables are owned by Alembic in the direct-sim repo (revision
    0002_authoring_schema), because that repo owns the shared database's migration
    history. This app detects and adapts rather than running DDL, so the two never race
    and the schema has exactly one source of truth.

    Returns False on any error — case generation must keep working without it.
    """
    if bind is None:
        return False
    try:
        names = set(inspect(bind).get_table_names(schema=AUTHORING_SCHEMA))
        required = {
            "case_families",
            "case_versions",
            "diagnostic_frameworks",
            "feature_likelihood_ratios",
        }
        missing = required - names
        if missing:
            logger.warning(
                "Authoring schema incomplete (missing: %s). Run 'alembic upgrade head' "
                "in the direct-sim repo. Framework/LR data will not be persisted.",
                ", ".join(sorted(missing)),
            )
            return False
        return True
    except Exception as e:
        logger.error(
            "Could not inspect '%s' schema; authoring persistence disabled: %s",
            AUTHORING_SCHEMA,
            str(e)[:200],
        )
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
