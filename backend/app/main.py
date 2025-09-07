import logging
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from backend.models.database import get_db, Base, engine, Case, DiagnosticFramework, FeatureLikelihoodRatio
from backend.models.schemas import CaseInput, CaseResponse, CaseOutputFiles
from backend.models.editing_schemas import (
    CasePreviewResponse, CaseEditRequest, CaseSaveRequest,
    SessionData, RegenerateLRRequest, RegenerateLRResponse
)
from backend.utils.llm_service import LLMService
from backend.utils.simulator_export import (
    create_feature_lr_matrix, create_prior_probabilities_file,
    export_to_csv, export_to_excel, create_case_summary_for_simulator,
    validate_lr_matrix_for_simulator
)
from backend.models.structured_outputs import (
    CaseDetailsStructured, DiagnosticFrameworkStructured, DiagnosticTierStructured,
    DiagnosticBucketStructured, ProbabilityEntry
)
from backend.utils.auth import verify_credentials
import json
import redis
import os
import time
import uuid
from fastapi.responses import Response
from dotenv import load_dotenv
import difflib

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

logger.info("Starting Medical Case Generator API")
logger.info(f"Environment: {os.getenv('ENVIRONMENT', 'Not set')}")
logger.info(f"Database URL set: {'Yes' if os.getenv('POSTGRES_URL') else 'No'}")
logger.info(f"Redis URL: {os.getenv('REDIS_URL', 'Not set')}")
logger.info(f"OpenAI API Key set: {'Yes' if os.getenv('OPENAI_API_KEY') else 'No'}")

# Initialize database tables in production environments
if os.getenv("ENVIRONMENT") != "development":
    try:
        logger.info("Attempting to initialize database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.error(f"Warning: Could not initialize database tables: {e}")
        logger.exception("Database initialization error details:")

app = FastAPI(title="Medical Case Generator API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    logger.info("Initializing Redis client...")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    logger.info(f"Attempting to connect to Redis at: {redis_url}")
    
    def create_redis_client():
        return redis.from_url(
            redis_url,
            socket_connect_timeout=30,
            socket_timeout=30,
            retry_on_timeout=True,
            health_check_interval=30,
            single_connection_client=True
        )
    
    max_retries = 30
    retry_delay = 10
    redis_client = None
    for attempt in range(max_retries):
        try:
            logger.info(f"Redis connection attempt {attempt + 1}/{max_retries} to {redis_url}")
            redis_client = create_redis_client()
            redis_client.ping()
            logger.info("Redis client initialized successfully")
            break
        except Exception as e:
            logger.warning(f"Redis connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 60)  # Exponential backoff, max 60s
            else:
                raise e
    
except Exception as e:
    logger.error(f"Failed to initialize Redis client after {max_retries} attempts: {e}")
    logger.exception("Redis initialization error details:")
    # Initialize with None to prevent startup crash
    redis_client = None

try:
    logger.info("Initializing LLM service...")
    llm_service = LLMService()
    logger.info("LLM service initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize LLM service: {e}")
    logger.exception("LLM service initialization error details:")
    # Continue without LLM service to prevent startup crash
    llm_service = None

def retry_db_operation(operation, max_retries=3, delay=1):
    """Retry database operations with exponential backoff"""
    for attempt in range(max_retries):
        try:
            return operation()
        except OperationalError as e:
            if "SSL connection has been closed" in str(e) and attempt < max_retries - 1:
                time.sleep(delay * (2 ** attempt))  # Exponential backoff
                continue
            raise e
        except Exception as e:
            raise e

@app.get("/")
async def root():
    return {"message": "Medical Case Generator API"}

@app.get("/health")
async def health_check():
    """Health check endpoint to verify environment variables and services"""
    logger.info("Health check endpoint called")
    try:
        # Check environment variables
        env_vars = {
            "OPENAI_API_KEY": "Set" if os.getenv("OPENAI_API_KEY") else "Missing",
            "REDIS_URL": os.getenv("REDIS_URL", "Missing"),
            "POSTGRES_URL": "Set" if os.getenv("POSTGRES_URL") else "Missing",
            "APP_USERNAME": os.getenv("APP_USERNAME", "Missing"),
            "APP_PASSWORD": "Set" if os.getenv("APP_PASSWORD") else "Missing"
        }
        
        logger.info(f"Environment variables check: {env_vars}")
        
        # Test Redis connection
        redis_status = "Connected"
        try:
            if redis_client:
                redis_client.ping()
                logger.info("Redis connection successful")
            else:
                redis_status = "Redis client not initialized"
                logger.warning("Redis client not initialized")
        except Exception as e:
            redis_status = f"Failed: {str(e)}"
            logger.error(f"Redis connection failed: {e}")
            logger.error(f"Redis URL being used: {os.getenv('REDIS_URL', 'redis://localhost:6379/0')}")
        
        # Test OpenAI API key (without making a full request)
        openai_status = "Set" if os.getenv("OPENAI_API_KEY") else "Missing"
        logger.info(f"OpenAI API key status: {openai_status}")
        
        # Test database connection
        db_status = "Unknown"
        try:
            # Try to get a database session
            db = next(get_db())
            db_status = "Connected"
            db.close()
            logger.info("Database connection successful")
        except Exception as e:
            db_status = f"Failed: {str(e)}"
            logger.error(f"Database connection failed: {e}")
        
        health_result = {
            "status": "healthy",
            "environment_variables": env_vars,
            "redis_connection": redis_status,
            "openai_api_key": openai_status,
            "database_connection": db_status
        }
        
        logger.info(f"Health check result: {health_result}")
        return health_result
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        logger.exception("Health check error details:")
        return {"status": "unhealthy", "error": str(e)}

@app.post("/preview-case", response_model=CasePreviewResponse)
async def preview_case(case_input: CaseInput, model: str = "gpt-4o-mini", temperature: float = 0.7, username: str = Depends(verify_credentials)):
    """Generate case content for preview/editing without saving to database"""
    try:
        # Generate all content using LLM
        case_details = llm_service.generate_case_details(
            case_input.description,
            case_input.primary_diagnosis,
            model=model,
            temperature=temperature
        )
        
        diagnostic_framework = llm_service.generate_diagnostic_framework(
            case_details,
            case_input.primary_diagnosis,
            model=model,
            temperature=temperature
        )
        
        feature_lrs = llm_service.generate_feature_likelihood_ratios(
            case_details,
            diagnostic_framework,
            model=model,
            temperature=temperature
        )
        
        # Create session for editing
        session_id = str(uuid.uuid4())
        
        # Convert structured outputs to editable format
        diagnostic_tiers = []
        for tier in diagnostic_framework.tiers:
            prob_dict = {prob.bucket_name: prob.probability for prob in tier.a_priori_probabilities}
            diagnostic_tiers.append({
                "tier_level": tier.tier_level,
                "buckets": [bucket.model_dump() for bucket in tier.buckets],
                "a_priori_probabilities": prob_dict
            })
        
        # Store in Redis for editing session
        session_data = SessionData(
            case_details=case_details.model_dump(),
            diagnostic_framework=diagnostic_tiers,
            feature_likelihood_ratios=[lr.model_dump() for lr in feature_lrs.feature_likelihood_ratios],
            original_input=case_input
        )
        
        # Store in Redis for editing session with proper error handling
        try:
            if redis_client:
                redis_client.setex(
                    f"session:{session_id}",
                    3600,  # 1 hour expiration
                    session_data.model_dump_json()
                )
            else:
                logger.warning("Redis client not available - session storage disabled")
                session_id = "redis-unavailable"
        except Exception as e:
            logger.error(f"Failed to store session in Redis: {e}")
            logger.exception("Redis storage error details:")
            # Continue without Redis storage - this will affect editing functionality
            session_id = "redis-unavailable"
        
        return CasePreviewResponse(
            session_id=session_id,
            case_details=case_details.model_dump(),
            diagnostic_framework=diagnostic_tiers,
            feature_likelihood_ratios=[lr.model_dump() for lr in feature_lrs.feature_likelihood_ratios]
        )
        
    except Exception as e:
        import traceback
        error_details = {
            "error": str(e),
            "type": type(e).__name__,
            "traceback": traceback.format_exc()
        }
        print(f"Error in preview_case: {error_details}")
        raise HTTPException(status_code=500, detail=error_details)

@app.put("/edit-case")
async def edit_case(edit_request: CaseEditRequest):
    """Update case data in editing session"""
    try:
        # Check if Redis client is available
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis service unavailable")
        
        try:
            # Retrieve session data
            session_key = f"session:{edit_request.session_id}"
            session_json = redis_client.get(session_key)
            
            if not session_json:
                raise HTTPException(status_code=404, detail="Session not found or expired")
            
            session_data = SessionData.model_validate_json(session_json)
        except Exception as e:
            logger.error(f"Failed to retrieve session from Redis: {e}")
            raise HTTPException(status_code=503, detail="Session service unavailable")
        
        # Update the requested components
        if edit_request.case_details:
            session_data.case_details = edit_request.case_details.model_dump()
        
        if edit_request.diagnostic_framework:
            session_data.diagnostic_framework = [tier.model_dump() for tier in edit_request.diagnostic_framework]
        
        if edit_request.feature_likelihood_ratios:
            session_data.feature_likelihood_ratios = [lr.model_dump() for lr in edit_request.feature_likelihood_ratios]

        # Update optional metadata if provided
        if edit_request.title is not None:
            session_data.title = edit_request.title
        if edit_request.description is not None:
            session_data.description = edit_request.description
        if edit_request.primary_diagnosis is not None:
            session_data.primary_diagnosis = edit_request.primary_diagnosis
        
        # Save updated session data
        try:
            redis_client.setex(session_key, 3600, session_data.model_dump_json())
        except Exception as e:
            logger.error(f"Failed to save session to Redis: {e}")
            raise HTTPException(status_code=503, detail="Failed to save session data")
        
        return {
            "status": "success",
            "message": "Case updated successfully",
            "session_id": edit_request.session_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/session/{session_id}")
async def get_session_data(session_id: str):
    """Retrieve current session data for editing"""
    # Check if Redis client is available
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis service unavailable")
    
    try:
        session_json = redis_client.get(f"session:{session_id}")
        
        if not session_json:
            raise HTTPException(status_code=404, detail="Session not found or expired")
        
        session_data = SessionData.model_validate_json(session_json)
        
        return {
            "case_details": session_data.case_details,
            "diagnostic_framework": session_data.diagnostic_framework,
            "feature_likelihood_ratios": session_data.feature_likelihood_ratios
        }
    except Exception as e:
        logger.error(f"Failed to retrieve session from Redis: {e}")
        raise HTTPException(status_code=503, detail="Session service unavailable")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/finalize-case", response_model=CaseResponse)
async def finalize_case(save_request: CaseSaveRequest, db: Session = Depends(get_db)):
    """Save the edited case to the database. Falls back to request payload if Redis is unavailable."""
    session_data = None

    # Attempt to load from Redis first (if available)
    if redis_client:
        try:
            session_json = redis_client.get(f"session:{save_request.session_id}")
            if session_json:
                session_data = SessionData.model_validate_json(session_json)
            else:
                logger.warning("Session not found in Redis; will try request payload fallback")
        except Exception as e:
            logger.error(f"Failed to retrieve session from Redis: {e}")
            # proceed to fallback below
    else:
        logger.warning("Redis client not available during finalize; using request payload if provided")

    # Fallback: accept payload content if provided
    if session_data is None:
        if not (save_request.case_details and save_request.diagnostic_framework and save_request.feature_likelihood_ratios):
            # If no Redis session and no payload, return explicit error
            raise HTTPException(status_code=503, detail="Session unavailable and no case data provided. Please retry from Edit flow.")
        session_data = SessionData(
            case_details=save_request.case_details,
            diagnostic_framework=save_request.diagnostic_framework,
            feature_likelihood_ratios=save_request.feature_likelihood_ratios,
            original_input=CaseInput(description=save_request.description, primary_diagnosis=save_request.primary_diagnosis)
        )

    try:
        # Save case to database with retry logic
        def save_case():
            case = Case(
                title=save_request.title or f"Case: {save_request.primary_diagnosis}",
                description=save_request.description,
                primary_diagnosis=save_request.primary_diagnosis,
                case_details=session_data.case_details
            )
            db.add(case)
            db.commit()
            db.refresh(case)
            return case

        case = retry_db_operation(save_case)

        # Save diagnostic frameworks
        def save_frameworks():
            for tier_data in session_data.diagnostic_framework:
                framework = DiagnosticFramework(
                    case_id=case.id,
                    tier_level=tier_data["tier_level"],
                    diagnostic_buckets=tier_data["buckets"],
                    a_priori_probabilities=tier_data["a_priori_probabilities"]
                )
                db.add(framework)
            db.commit()

        retry_db_operation(save_frameworks)

        # Save feature likelihood ratios
        def save_feature_lrs():
            for lr_data in session_data.feature_likelihood_ratios:
                lr = FeatureLikelihoodRatio(
                    case_id=case.id,
                    framework_id=None,
                    feature_name=lr_data["feature_name"],
                    feature_category=lr_data["feature_category"],
                    diagnostic_bucket=lr_data["diagnostic_bucket"],
                    likelihood_ratio=lr_data["likelihood_ratio"],
                    tier_level=lr_data.get("tier_level")
                )
                db.add(lr)
            db.commit()

        retry_db_operation(save_feature_lrs)

        # Clean up session
        try:
            redis_client.delete(f"session:{save_request.session_id}")
        except Exception as e:
            logger.error(f"Failed to clean up session from Redis: {e}")
            # Continue without cleanup - this is not critical

        return CaseResponse(
            case_id=case.id,
            case_details=session_data.case_details,
            diagnostic_framework=session_data.diagnostic_framework,
            feature_likelihood_ratios=session_data.feature_likelihood_ratios
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-case", response_model=CaseResponse)
async def generate_case(case_input: CaseInput, model: str = "gpt-4o-mini", temperature: float = 0.7, db: Session = Depends(get_db)):
    try:
        case_details = llm_service.generate_case_details(
            case_input.description,
            case_input.primary_diagnosis,
            model=model,
            temperature=temperature
        )
        
        def save_case():
            case = Case(
                title=f"Case: {case_input.primary_diagnosis}",
                description=case_input.description,
                primary_diagnosis=case_input.primary_diagnosis,
                case_details=case_details.model_dump()
            )
            db.add(case)
            db.commit()
            db.refresh(case)
            return case
        
        case = retry_db_operation(save_case)
        
        diagnostic_framework = llm_service.generate_diagnostic_framework(
            case_details,
            case_input.primary_diagnosis,
            model=model,
            temperature=temperature
        )
        
        def save_frameworks():
            for tier in diagnostic_framework.tiers:
                # Convert probability list back to dict for storage
                prob_dict = {prob.bucket_name: prob.probability for prob in tier.a_priori_probabilities}
                
                framework = DiagnosticFramework(
                    case_id=case.id,
                    tier_level=tier.tier_level,
                    diagnostic_buckets=[bucket.model_dump() for bucket in tier.buckets],
                    a_priori_probabilities=prob_dict
                )
                db.add(framework)
            db.commit()
        
        retry_db_operation(save_frameworks)
        
        feature_lrs = llm_service.generate_feature_likelihood_ratios(
            case_details,
            diagnostic_framework,
            model=model,
            temperature=temperature
        )
        
        def save_feature_lrs():
            for feature in feature_lrs.feature_likelihood_ratios:
                lr = FeatureLikelihoodRatio(
                    case_id=case.id,
                    framework_id=None,  # We could link to specific framework if needed
                    feature_name=feature.feature_name,
                    feature_category=feature.feature_category,
                    diagnostic_bucket=feature.diagnostic_bucket,
                    likelihood_ratio=feature.likelihood_ratio,
                    tier_level=getattr(feature, 'tier_level', None)
                )
                db.add(lr)
            db.commit()
        
        retry_db_operation(save_feature_lrs)
        
        # Convert structured outputs to response format
        diagnostic_tiers = []
        for tier in diagnostic_framework.tiers:
            prob_dict = {prob.bucket_name: prob.probability for prob in tier.a_priori_probabilities}
            diagnostic_tiers.append({
                "tier_level": tier.tier_level,
                "buckets": [bucket.model_dump() for bucket in tier.buckets],
                "a_priori_probabilities": prob_dict
            })
        
        return CaseResponse(
            case_id=case.id,
            case_details=case_details.model_dump(),
            diagnostic_framework=diagnostic_tiers,
            feature_likelihood_ratios=[lr.model_dump() for lr in feature_lrs.feature_likelihood_ratios]
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/case/{case_id}/output-files", response_model=CaseOutputFiles)
async def get_case_output_files(case_id: int, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    frameworks = db.query(DiagnosticFramework).filter(DiagnosticFramework.case_id == case_id).all()
    feature_lrs = db.query(FeatureLikelihoodRatio).filter(FeatureLikelihoodRatio.case_id == case_id).all()
    
    case_details_json = {
        "case_id": case.id,
        "title": case.title,
        "description": case.description,
        "primary_diagnosis": case.primary_diagnosis,
        "presentation": case.case_details.get("presentation"),
        "patient_personality": case.case_details.get("patient_personality"),
        "history_questions": case.case_details.get("history_questions", []),
        "physical_exam_findings": case.case_details.get("physical_exam_findings", []),
        "diagnostic_workup": case.case_details.get("diagnostic_workup", [])
    }
    
    a_priori_probabilities_json = {}
    for framework in frameworks:
        tier_key = f"tier_{framework.tier_level}"
        a_priori_probabilities_json[tier_key] = {
            "buckets": framework.diagnostic_buckets,
            "probabilities": framework.a_priori_probabilities
        }
    
    feature_likelihood_ratios_json = {
        "history": {},
        "physical_exam": {},
        "diagnostic_workup": {}
    }
    
    for lr in feature_lrs:
        category = lr.feature_category
        if category not in feature_likelihood_ratios_json:
            feature_likelihood_ratios_json[category] = {}
        
        feature_name = lr.feature_name
        if feature_name not in feature_likelihood_ratios_json[category]:
            feature_likelihood_ratios_json[category][feature_name] = {}
        
        feature_likelihood_ratios_json[category][feature_name][lr.diagnostic_bucket] = lr.likelihood_ratio
    
    return CaseOutputFiles(
        case_details_json=case_details_json,
        a_priori_probabilities_json=a_priori_probabilities_json,
        feature_likelihood_ratios_json=feature_likelihood_ratios_json
    )

@app.get("/cases")
async def list_cases(db: Session = Depends(get_db)):
    cases = db.query(Case).all()
    return [{"id": case.id, "title": case.title, "primary_diagnosis": case.primary_diagnosis} for case in cases]

@app.get("/case/{case_id}/simulator-exports")
async def get_simulator_export_info(case_id: int, db: Session = Depends(get_db)):
    """Get information about available simulator exports for a case"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    frameworks = db.query(DiagnosticFramework).filter(DiagnosticFramework.case_id == case_id).all()
    feature_lrs = db.query(FeatureLikelihoodRatio).filter(FeatureLikelihoodRatio.case_id == case_id).all()
    
    # Get available tiers
    available_tiers = sorted(set(f.tier_level for f in frameworks))
    
    return {
        "case_id": case_id,
        "case_title": case.title,
        "available_tiers": available_tiers,
        "total_features": len(set(lr.feature_name for lr in feature_lrs)),
        "total_diagnostic_buckets": len(set(lr.diagnostic_bucket for lr in feature_lrs)),
        "available_exports": [
            "feature_lr_matrix_csv",
            "feature_lr_matrix_excel", 
            "prior_probabilities_json",
            "case_summary_txt"
        ]
    }

@app.get("/case/{case_id}/debug-lr-data")
async def debug_lr_data(case_id: int, db: Session = Depends(get_db)):
    """Debug endpoint to see raw LR data before matrix creation"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    feature_lrs = db.query(FeatureLikelihoodRatio).filter(FeatureLikelihoodRatio.case_id == case_id).all()
    
    debug_data = {
        "total_feature_lrs": len(feature_lrs),
        "feature_lrs": [
            {
                "feature_name": lr.feature_name,
                "feature_category": lr.feature_category,
                "diagnostic_bucket": lr.diagnostic_bucket,
                "likelihood_ratio": lr.likelihood_ratio
            }
            for lr in feature_lrs
        ],
        "case_details_features": {
            "history_questions": [hq.get('question', '') for hq in case.case_details.get('history_questions', [])],
            "physical_exam": [pe.get('examination', '') for pe in case.case_details.get('physical_exam_findings', [])],
            "diagnostic_workup": [dw.get('test', '') for dw in case.case_details.get('diagnostic_workup', [])]
        }
    }
    
    return debug_data

@app.get("/case/{case_id}/simulator-export/lr-matrix-csv")
async def export_lr_matrix_csv(case_id: int, tier_level: int = 1, db: Session = Depends(get_db)):
    """Export feature likelihood ratio matrix as CSV for simulator app"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    frameworks = db.query(DiagnosticFramework).filter(DiagnosticFramework.case_id == case_id).all()
    feature_lrs = db.query(FeatureLikelihoodRatio).filter(FeatureLikelihoodRatio.case_id == case_id).all()
    
    # Convert to format expected by simulator export function
    diagnostic_framework = []
    for framework in frameworks:
        diagnostic_framework.append({
            "tier_level": framework.tier_level,
            "buckets": framework.diagnostic_buckets,
            "a_priori_probabilities": framework.a_priori_probabilities
        })
    
    feature_likelihood_ratios = []
    for lr in feature_lrs:
        feature_likelihood_ratios.append({
            "feature_name": lr.feature_name,
            "feature_category": lr.feature_category,
            "diagnostic_bucket": lr.diagnostic_bucket,
            "likelihood_ratio": lr.likelihood_ratio,
            "tier_level": getattr(lr, "tier_level", None),
        })
    
    # Create the LR matrix (respect selected tier)
    lr_matrix = create_feature_lr_matrix(
        case.case_details,
        diagnostic_framework,
        feature_likelihood_ratios,
        tier_level=tier_level,
        strict=True,
    )
    
    # Validate the matrix
    validation = validate_lr_matrix_for_simulator(lr_matrix)
    
    if not validation["valid"]:
        raise HTTPException(status_code=400, detail=f"Invalid LR matrix: {validation['errors']}")
    
    # Export to CSV
    csv_content = export_to_csv(lr_matrix)
    
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=case_{case_id}_lr_matrix.csv"}
    )

@app.get("/case/{case_id}/simulator-export/lr-matrix-excel")
async def export_lr_matrix_excel(case_id: int, tier_level: int = 1, db: Session = Depends(get_db)):
    """Export feature likelihood ratio matrix as Excel for simulator app"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    frameworks = db.query(DiagnosticFramework).filter(DiagnosticFramework.case_id == case_id).all()
    feature_lrs = db.query(FeatureLikelihoodRatio).filter(FeatureLikelihoodRatio.case_id == case_id).all()
    
    # Convert to format expected by simulator export function
    diagnostic_framework = []
    for framework in frameworks:
        diagnostic_framework.append({
            "tier_level": framework.tier_level,
            "buckets": framework.diagnostic_buckets,
            "a_priori_probabilities": framework.a_priori_probabilities
        })
    
    feature_likelihood_ratios = []
    for lr in feature_lrs:
        feature_likelihood_ratios.append({
            "feature_name": lr.feature_name,
            "feature_category": lr.feature_category,
            "diagnostic_bucket": lr.diagnostic_bucket,
            "likelihood_ratio": lr.likelihood_ratio,
            "tier_level": getattr(lr, "tier_level", None),
        })
    
    # Create the LR matrix (respect selected tier)
    lr_matrix = create_feature_lr_matrix(
        case.case_details,
        diagnostic_framework,
        feature_likelihood_ratios,
        tier_level=tier_level,
        strict=True,
    )
    
    # Validate the matrix
    validation = validate_lr_matrix_for_simulator(lr_matrix)
    
    if not validation["valid"]:
        raise HTTPException(status_code=400, detail=f"Invalid LR matrix: {validation['errors']}")
    
    # Export to Excel
    excel_content = export_to_excel(lr_matrix)
    
    return Response(
        content=excel_content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=case_{case_id}_lr_matrix.xlsx"}
    )

@app.get("/case/{case_id}/simulator-export/prior-probabilities")
async def export_prior_probabilities(case_id: int, tier_level: int = 1, db: Session = Depends(get_db)):
    """Export prior probabilities for specific tier as JSON for simulator app"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    frameworks = db.query(DiagnosticFramework).filter(DiagnosticFramework.case_id == case_id).all()
    
    diagnostic_framework = []
    for framework in frameworks:
        diagnostic_framework.append({
            "tier_level": framework.tier_level,
            "buckets": framework.diagnostic_buckets,
            "a_priori_probabilities": framework.a_priori_probabilities
        })
    
    prior_probs = create_prior_probabilities_file(diagnostic_framework, tier_level)
    
    if not prior_probs:
        raise HTTPException(status_code=404, detail=f"No prior probabilities found for tier {tier_level}")
    
    # Validate probabilities sum to 1.0
    total_prob = sum(prior_probs.values())
    if abs(total_prob - 1.0) > 0.01:
        raise HTTPException(status_code=400, detail=f"Prior probabilities sum to {total_prob:.3f}, must sum to 1.0")
    
    return Response(
        content=json.dumps(prior_probs, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=case_{case_id}_tier_{tier_level}_priors.json"}
    )

@app.get("/case/{case_id}/simulator-export/case-summary")
async def export_case_summary(case_id: int, db: Session = Depends(get_db)):
    """Export case summary as text file for simulator app transcript input"""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    summary_text = create_case_summary_for_simulator(
        case.case_details,
        case.primary_diagnosis,
        case_id
    )
    
    return Response(
        content=summary_text,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=case_{case_id}_summary.txt"}
    )

# -------------------- SESSION-BASED EXPORTS (DRAFTS) --------------------

def _load_session(session_id: str) -> SessionData:
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis service unavailable")
    raw = redis_client.get(f"session:{session_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return SessionData.model_validate_json(raw)

@app.get("/session/{session_id}/simulator-exports")
async def get_session_simulator_export_info(session_id: str):
    sd = _load_session(session_id)
    # Available tiers from session diagnostic framework
    available_tiers = sorted(set(int(t.get("tier_level", 1)) for t in sd.diagnostic_framework))
    primary = sd.primary_diagnosis or sd.original_input.primary_diagnosis
    title = sd.title or (f"Case: {primary}" if primary else "Case: Draft")
    return {
        "session_id": session_id,
        "case_title": title,
        "available_tiers": available_tiers,
        "total_features": len(set(lr.get('feature_name') for lr in sd.feature_likelihood_ratios)),
        "total_diagnostic_buckets": len(set(b.get('name') for t in sd.diagnostic_framework for b in t.get('buckets', []))),
        "available_exports": [
            "feature_lr_matrix_csv",
            "feature_lr_matrix_excel",
            "prior_probabilities_json",
            "case_summary_txt",
        ],
    }

@app.get("/session/{session_id}/simulator-export/lr-matrix-csv")
async def export_session_lr_matrix_csv(session_id: str, tier_level: int = 1):
    sd = _load_session(session_id)
    lr_matrix = create_feature_lr_matrix(
        sd.case_details,
        sd.diagnostic_framework,
        sd.feature_likelihood_ratios,
        tier_level=tier_level,
        strict=True,
    )
    csv_content = export_to_csv(lr_matrix)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=session_{session_id}_lr_matrix.csv"}
    )

@app.get("/session/{session_id}/simulator-export/lr-matrix-excel")
async def export_session_lr_matrix_excel(session_id: str, tier_level: int = 1):
    sd = _load_session(session_id)
    lr_matrix = create_feature_lr_matrix(
        sd.case_details,
        sd.diagnostic_framework,
        sd.feature_likelihood_ratios,
        tier_level=tier_level,
        strict=True,
    )
    excel_content = export_to_excel(lr_matrix)
    return Response(
        content=excel_content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=session_{session_id}_lr_matrix.xlsx"}
    )

@app.get("/session/{session_id}/simulator-export/prior-probabilities")
async def export_session_prior_probabilities(session_id: str, tier_level: int = 1):
    sd = _load_session(session_id)
    prior_probs = create_prior_probabilities_file(sd.diagnostic_framework, tier_level)
    if not prior_probs:
        raise HTTPException(status_code=404, detail=f"No prior probabilities found for tier {tier_level}")
    total_prob = sum(prior_probs.values())
    if abs(total_prob - 1.0) > 0.01:
        raise HTTPException(status_code=400, detail=f"Prior probabilities sum to {total_prob:.3f}, must sum to 1.0")
    return Response(
        content=json.dumps(prior_probs, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=session_{session_id}_tier_{tier_level}_priors.json"}
    )

@app.get("/session/{session_id}/simulator-export/case-summary")
async def export_session_case_summary(session_id: str):
    sd = _load_session(session_id)
    primary = sd.primary_diagnosis or sd.original_input.primary_diagnosis or ""
    summary_text = create_case_summary_for_simulator(sd.case_details, primary)
    return Response(
        content=summary_text,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=session_{session_id}_summary.txt"}
    )

@app.post("/regenerate-lrs", response_model=RegenerateLRResponse)
async def regenerate_lrs(request: RegenerateLRRequest):
    """Regenerate feature likelihood ratios from current session or provided payload using strict bucket names."""
    # Load session data
    session_data: SessionData | None = None
    if redis_client:
        try:
            raw = redis_client.get(f"session:{request.session_id}")
            if raw:
                session_data = SessionData.model_validate_json(raw)
        except Exception as e:
            logger.warning(f"Could not load session from Redis: {e}")
    # Fallback to payload if provided
    if session_data is None:
        if not (request.case_details and request.diagnostic_framework):
            raise HTTPException(status_code=400, detail="Session not found and no case/framework provided")
        session_data = SessionData(
            case_details=request.case_details,
            diagnostic_framework=request.diagnostic_framework,
            feature_likelihood_ratios=[],
            original_input=CaseInput(description="", primary_diagnosis="")
        )

    # Build structured inputs for LLM
    try:
        case_struct = CaseDetailsStructured.model_validate(session_data.case_details)

        tiers_struct: list[DiagnosticTierStructured] = []
        for tier in session_data.diagnostic_framework:
            # probabilities dict -> list
            probs_dict = tier.get("a_priori_probabilities", {})
            probs_list = [
                ProbabilityEntry(bucket_name=k, probability=float(v))
                for k, v in probs_dict.items()
            ]
            buckets_list = [
                DiagnosticBucketStructured(name=b.get("name", ""), description=b.get("description", ""))
                for b in tier.get("buckets", [])
            ]
            tiers_struct.append(
                DiagnosticTierStructured(
                    tier_level=int(tier.get("tier_level", 1)),
                    buckets=buckets_list,
                    a_priori_probabilities=probs_list,
                )
            )
        framework_struct = DiagnosticFrameworkStructured(tiers=tiers_struct)
    except Exception as e:
        logger.exception("Failed to build structured inputs for LLM")
        raise HTTPException(status_code=400, detail=f"Invalid case/framework structure: {e}")

    # Call LLM to regenerate LRs (already strict inside service)
    try:
        flr_struct = llm_service.generate_feature_likelihood_ratios(case_struct, framework_struct)
        flr_list = [lr.model_dump() for lr in flr_struct.feature_likelihood_ratios]
    except Exception as e:
        logger.exception("LLM LR regeneration failed")
        raise HTTPException(status_code=500, detail=f"Failed to regenerate likelihood ratios: {e}")

    # Save back to session if available
    try:
        if redis_client:
            session_data.feature_likelihood_ratios = flr_list
            redis_client.setex(f"session:{request.session_id}", 3600, session_data.model_dump_json())
    except Exception as e:
        logger.warning(f"Failed to persist regenerated LRs to Redis: {e}")

    return RegenerateLRResponse(feature_likelihood_ratios=flr_list)

@app.get("/case/{case_id}/simulator-export/debug-matching")
async def debug_simulator_bucket_matching(case_id: int, tier_level: int | None = 1, db: Session = Depends(get_db)):
    """Debug endpoint to show how LR diagnostic_buckets map to framework buckets for a given tier.

    Returns which LR entries matched a bucket column and which did not, with suggestions.
    """
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Load framework and LRs
    frameworks = db.query(DiagnosticFramework).filter(DiagnosticFramework.case_id == case_id).all()
    feature_lrs = db.query(FeatureLikelihoodRatio).filter(FeatureLikelihoodRatio.case_id == case_id).all()

    # Determine buckets to use (respect tier if possible)
    def _norm(s: str) -> str:
        import re
        s = (s or "").strip().lower()
        s = re.sub(r"^tier\s*\d+\s*:\s*", "", s)
        s = re.sub(r"\s+", " ", s)
        return s

    selected_names: list[str] = []
    if tier_level is not None:
        sel = next((t for t in frameworks if t.tier_level == tier_level), None)
        if sel:
            selected_names = [b.get('name') for b in sel.diagnostic_buckets if isinstance(b, dict) and b.get('name')]
    if not selected_names:
        seen = set()
        for fw in frameworks:
            for b in fw.diagnostic_buckets:
                if isinstance(b, dict):
                    name = b.get('name')
                    if name and name not in seen:
                        seen.add(name)
                        selected_names.append(name)

    norm_map = {_norm(n): n for n in selected_names}
    norm_list = list(norm_map.keys())

    matched = []
    unmatched = []
    bucket_counts = {name: 0 for name in selected_names}

    for lr in feature_lrs:
        lr_bucket = lr.diagnostic_bucket
        nb = _norm(lr_bucket)
        if nb in norm_map:
            display = norm_map[nb]
            bucket_counts[display] = bucket_counts.get(display, 0) + 1
            matched.append({
                "feature_name": lr.feature_name,
                "feature_category": lr.feature_category,
                "diagnostic_bucket": lr_bucket,
                "matched_bucket": display,
                "likelihood_ratio": lr.likelihood_ratio,
            })
        else:
            # Suggest closest match
            suggestion = None
            if norm_list:
                closest = difflib.get_close_matches(nb, norm_list, n=1, cutoff=0.6)
                if closest:
                    suggestion = norm_map[closest[0]]
            unmatched.append({
                "feature_name": lr.feature_name,
                "feature_category": lr.feature_category,
                "diagnostic_bucket": lr_bucket,
                "normalized_bucket": nb,
                "suggested_bucket": suggestion,
                "likelihood_ratio": lr.likelihood_ratio,
            })

    return {
        "case_id": case_id,
        "tier_level": tier_level,
        "selected_buckets": selected_names,
        "total_lrs": len(feature_lrs),
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "bucket_match_counts": bucket_counts,
        "sample_unmatched": unmatched[:20],
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
