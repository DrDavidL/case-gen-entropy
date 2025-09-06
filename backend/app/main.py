from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from backend.models.database import get_db, Base, engine, Case, DiagnosticFramework, FeatureLikelihoodRatio
from backend.models.schemas import CaseInput, CaseResponse, CaseOutputFiles
from backend.models.editing_schemas import (
    CasePreviewResponse, CaseEditRequest, CaseSaveRequest, 
    SessionData
)
from backend.utils.llm_service import LLMService
from backend.utils.simulator_export import (
    create_feature_lr_matrix, create_prior_probabilities_file, 
    export_to_csv, export_to_excel, create_case_summary_for_simulator,
    validate_lr_matrix_for_simulator
)
from backend.utils.auth import verify_credentials
import json
import redis
import os
import time
import uuid
from fastapi.responses import Response
from dotenv import load_dotenv

load_dotenv()

# Base.metadata.create_all(bind=engine) # Commented out to prevent startup hangs

app = FastAPI(title="Medical Case Generator API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
llm_service = LLMService()

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
    try:
        # Check environment variables
        env_vars = {
            "OPENAI_API_KEY": "Set" if os.getenv("OPENAI_API_KEY") else "Missing",
            "REDIS_URL": os.getenv("REDIS_URL", "Missing"),
            "POSTGRES_URL": "Set" if os.getenv("POSTGRES_URL") else "Missing",
            "APP_USERNAME": os.getenv("APP_USERNAME", "Missing"),
            "APP_PASSWORD": "Set" if os.getenv("APP_PASSWORD") else "Missing"
        }
        
        # Test Redis connection
        redis_status = "Connected"
        try:
            redis_client.ping()
        except Exception as e:
            redis_status = f"Failed: {str(e)}"
        
        # Test OpenAI API key (without making a full request)
        openai_status = "Set" if os.getenv("OPENAI_API_KEY") else "Missing"
        
        return {
            "status": "healthy",
            "environment_variables": env_vars,
            "redis_connection": redis_status,
            "openai_api_key": openai_status
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

@app.post("/preview-case", response_model=CasePreviewResponse)
async def preview_case(case_input: CaseInput, username: str = Depends(verify_credentials)):
    """Generate case content for preview/editing without saving to database"""
    try:
        # Generate all content using LLM
        case_details = llm_service.generate_case_details(
            case_input.description, 
            case_input.primary_diagnosis
        )
        
        diagnostic_framework = llm_service.generate_diagnostic_framework(
            case_details, 
            case_input.primary_diagnosis
        )
        
        feature_lrs = llm_service.generate_feature_likelihood_ratios(
            case_details, 
            diagnostic_framework
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
        
        redis_client.setex(
            f"session:{session_id}", 
            3600,  # 1 hour expiration
            session_data.model_dump_json()
        )
        
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
        # Retrieve session data
        session_key = f"session:{edit_request.session_id}"
        session_json = redis_client.get(session_key)
        
        if not session_json:
            raise HTTPException(status_code=404, detail="Session not found or expired")
        
        session_data = SessionData.model_validate_json(session_json)
        
        # Update the requested components
        if edit_request.case_details:
            session_data.case_details = edit_request.case_details.model_dump()
        
        if edit_request.diagnostic_framework:
            session_data.diagnostic_framework = [tier.model_dump() for tier in edit_request.diagnostic_framework]
        
        if edit_request.feature_likelihood_ratios:
            session_data.feature_likelihood_ratios = [lr.model_dump() for lr in edit_request.feature_likelihood_ratios]
        
        # Save updated session data
        redis_client.setex(session_key, 3600, session_data.model_dump_json())
        
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
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/finalize-case", response_model=CaseResponse)
async def finalize_case(save_request: CaseSaveRequest, db: Session = Depends(get_db)):
    """Save the edited case to the database"""
    try:
        # Retrieve session data
        session_json = redis_client.get(f"session:{save_request.session_id}")
        
        if not session_json:
            raise HTTPException(status_code=404, detail="Session not found or expired")
        
        session_data = SessionData.model_validate_json(session_json)
        
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
                    likelihood_ratio=lr_data["likelihood_ratio"]
                )
                db.add(lr)
            db.commit()
        
        retry_db_operation(save_feature_lrs)
        
        # Clean up session
        redis_client.delete(f"session:{save_request.session_id}")
        
        return CaseResponse(
            case_id=case.id,
            case_details=session_data.case_details,
            diagnostic_framework=session_data.diagnostic_framework,
            feature_likelihood_ratios=session_data.feature_likelihood_ratios
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-case", response_model=CaseResponse)
async def generate_case(case_input: CaseInput, db: Session = Depends(get_db)):
    try:
        case_details = llm_service.generate_case_details(
            case_input.description, 
            case_input.primary_diagnosis
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
            case_input.primary_diagnosis
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
            diagnostic_framework
        )
        
        def save_feature_lrs():
            for feature in feature_lrs.feature_likelihood_ratios:
                lr = FeatureLikelihoodRatio(
                    case_id=case.id,
                    framework_id=None,  # We could link to specific framework if needed
                    feature_name=feature.feature_name,
                    feature_category=feature.feature_category,
                    diagnostic_bucket=feature.diagnostic_bucket,
                    likelihood_ratio=feature.likelihood_ratio
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
            "likelihood_ratio": lr.likelihood_ratio
        })
    
    # Create the LR matrix
    lr_matrix = create_feature_lr_matrix(
        case.case_details,
        diagnostic_framework,
        feature_likelihood_ratios
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
            "likelihood_ratio": lr.likelihood_ratio
        })
    
    # Create the LR matrix
    lr_matrix = create_feature_lr_matrix(
        case.case_details,
        diagnostic_framework,
        feature_likelihood_ratios
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)