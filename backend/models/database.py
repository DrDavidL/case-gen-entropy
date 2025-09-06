from sqlalchemy import create_engine, Column, Integer, String, Text, JSON, DateTime, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("POSTGRES_URL")

if not DATABASE_URL:
    raise ValueError("POSTGRES_URL environment variable is required")

# Create engine optimized for serverless databases (Neon) with robust connection handling
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=3,           # Smaller pool for serverless
    max_overflow=7,        # Allow burst connections
    pool_pre_ping=True,    # Verify connections before use
    pool_recycle=1800,     # Recycle connections after 30 minutes (serverless timeout)
    pool_timeout=30,       # Wait up to 30s for connection
    echo=False,            # Set to True for debugging
    connect_args={
        "sslmode": "require",
        "connect_timeout": 10,      # Connection timeout
        "options": "-c statement_timeout=300000"  # 5 minute statement timeout
    } if "sslmode" not in DATABASE_URL else {
        "connect_timeout": 10,
        "options": "-c statement_timeout=300000"
    }
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Case(Base):
    __tablename__ = "cases"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(Text)
    primary_diagnosis = Column(String)
    case_details = Column(JSON)  # Generated case presentation, patient style, etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class DiagnosticFramework(Base):
    __tablename__ = "diagnostic_frameworks"
    
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, index=True)
    tier_level = Column(Integer)  # 1, 2, 3 for different tiers
    diagnostic_buckets = Column(JSON)  # List of diagnostic categories for this tier
    a_priori_probabilities = Column(JSON)  # Probability distribution for each bucket
    created_at = Column(DateTime, default=datetime.utcnow)

class FeatureLikelihoodRatio(Base):
    __tablename__ = "feature_likelihood_ratios"
    
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, index=True)
    framework_id = Column(Integer, index=True)
    feature_name = Column(String)
    feature_category = Column(String)  # history, physical_exam, diagnostic_workup
    diagnostic_bucket = Column(String)
    likelihood_ratio = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()