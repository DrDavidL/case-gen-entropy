from sqlalchemy import create_engine, Column, Integer, String, Text, JSON, DateTime, Float, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.pool import QueuePool
from datetime import datetime
import os
from dotenv import load_dotenv

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
    connect_args={"sslmode": "require"} if "sslmode" not in DATABASE_URL else {}
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
        connect_args={"sslmode": "require"} if "sslmode" not in SIM_READY_DATABASE_URL else {}
    )
    SimReadySessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sim_ready_engine)


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(Text)
    primary_diagnosis = Column(String)
    case_details = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    frameworks = relationship("DiagnosticFramework", back_populates="case", lazy="selectin")
    feature_lrs = relationship("FeatureLikelihoodRatio", back_populates="case", lazy="selectin")


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
