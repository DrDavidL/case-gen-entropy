import json
import logging
import os
import time
import uuid

import redis
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.models.database import (
    get_db, Base, engine, Case, DiagnosticFramework, FeatureLikelihoodRatio,
    get_sim_ready_db, SimReadyBase, sim_ready_engine, CaseDetailSimReady,
    authoring_schema_ready,
)
from backend.utils.authoring_store import persist_case_version, load_analysis
from backend.models.schemas import CaseInput, CaseResponse, CaseOutputFiles, SimReadyCaseResponse
from backend.models.editing_schemas import (
    CasePreviewResponse, CaseEditRequest, CaseSaveRequest,
    SessionData, SimReadyCasePreviewResponse, SimReadyCaseUpdateRequest,
)
from backend.utils.llm_service import LLMService
from backend.utils.simulator_export import (
    create_feature_lr_matrix, create_prior_probabilities_file,
    export_to_csv, export_to_excel, create_case_summary_for_simulator,
    validate_lr_matrix_for_simulator
)
from backend.utils.sim_ready_transform import (
    render_sim_ready_content, build_default_custom_input,
    build_default_custom_evaluation, build_default_learner_tasks,
)
from backend.utils.auth import verify_credentials
from backend.utils.build_info import get_build_info

load_dotenv()

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("case_gen")

Base.metadata.create_all(bind=engine)

# The shared database's schema is owned by Alembic in the direct-sim repo. We create only
# `case_details` (historical behavior, harmless via checkfirst) and *detect* the authoring
# tables rather than creating them, so there is exactly one source of truth for the schema
# and no race between the two apps at startup.
AUTHORING_ENABLED = False
if sim_ready_engine is not None:
    SimReadyBase.metadata.create_all(
        bind=sim_ready_engine,
        tables=[CaseDetailSimReady.__table__],
    )
    AUTHORING_ENABLED = authoring_schema_ready(sim_ready_engine)
    logger.info("Authoring persistence: %s", "enabled" if AUTHORING_ENABLED else "DISABLED")

_build = get_build_info()
logger.info("Build: sha=%s built=%s tag=%s",
            _build["git_sha"], _build["build_time"], _build["image_tag"])

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
    """Retry database operations with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return operation()
        except OperationalError as e:
            if "SSL connection has been closed" in str(e) and attempt < max_retries - 1:
                wait = delay * (2 ** attempt)
                logger.warning("DB SSL error (attempt %d/%d), retrying in %.1fs: %s",
                               attempt + 1, max_retries, wait, str(e)[:100])
                time.sleep(wait)
                continue
            logger.error("DB operation failed after %d attempts: %s", attempt + 1, str(e)[:200])
            raise


@app.get("/")
async def root():
    """Health check and build identity.

    The build fields are load-bearing: a stale container is otherwise
    indistinguishable from a current one. Doubles as the Docker HEALTHCHECK
    target, so keep it cheap and dependency-free.
    """
    return {
        "message": "Medical Case Generator API",
        "build": get_build_info(),
        "authoring_persistence": AUTHORING_ENABLED,
    }


@app.post("/preview-case")
async def preview_case(case_input: CaseInput, username: str = Depends(verify_credentials)):
    """Generate case content for preview/editing without saving to database."""
    logger.info("Preview case requested by %s: diagnosis=%s, format=%s",
                username, case_input.primary_diagnosis, case_input.output_format)
    try:
        is_sim_ready = case_input.output_format == "sim_ready"

        # Step 1: generate case details
        if is_sim_ready:
            sim_ready_details = await llm_service.generate_sim_ready_case_details_async(
                case_input.description,
                case_input.primary_diagnosis
            )
            # Adapt for downstream LR pipeline
            case_details = llm_service._sim_ready_to_case_details(sim_ready_details)
            logger.info("Sim-ready case details generated")
        else:
            case_details = await llm_service.generate_case_details_async(
                case_input.description,
                case_input.primary_diagnosis
            )
            logger.info("Case details generated")

        # Step 2: diagnostic framework depends on case_details
        diagnostic_framework = await llm_service.generate_diagnostic_framework_async(
            case_details,
            case_input.primary_diagnosis
        )
        logger.info("Diagnostic framework generated")

        # Step 3: feature LRs depend on both
        feature_lrs = await llm_service.generate_feature_likelihood_ratios_async(
            case_details,
            diagnostic_framework
        )
        logger.info("Feature likelihood ratios generated")

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

        # For sim-ready, store the full sim-ready data; for beta, store the original
        case_details_dump = (sim_ready_details.model_dump() if is_sim_ready
                            else case_details.model_dump())

        # Store in Redis for editing session
        session_data = SessionData(
            case_details=case_details_dump,
            diagnostic_framework=diagnostic_tiers,
            feature_likelihood_ratios=[lr.model_dump() for lr in feature_lrs.feature_likelihood_ratios],
            original_input=case_input,
            output_format=case_input.output_format,
        )

        redis_client.setex(
            f"session:{session_id}",
            3600,  # 1 hour expiration
            session_data.model_dump_json()
        )
        logger.info("Session created: %s (format=%s)", session_id, case_input.output_format)

        if is_sim_ready:
            rendered_content = render_sim_ready_content(case_details_dump)
            return SimReadyCasePreviewResponse(
                session_id=session_id,
                case_details=case_details_dump,
                diagnostic_framework=diagnostic_tiers,
                feature_likelihood_ratios=[lr.model_dump() for lr in feature_lrs.feature_likelihood_ratios],
                rendered_content=rendered_content,
                default_custom_input=build_default_custom_input(),
                default_custom_evaluation=build_default_custom_evaluation(),
                default_learner_tasks=build_default_learner_tasks(),
            )
        else:
            return CasePreviewResponse(
                session_id=session_id,
                case_details=case_details_dump,
                diagnostic_framework=diagnostic_tiers,
                feature_likelihood_ratios=[lr.model_dump() for lr in feature_lrs.feature_likelihood_ratios]
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to generate case preview")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/edit-case")
async def edit_case(edit_request: CaseEditRequest, username: str = Depends(verify_credentials)):
    """Update case data in editing session."""
    logger.info("Edit case requested: session=%s", edit_request.session_id)
    try:
        session_key = f"session:{edit_request.session_id}"
        session_json = redis_client.get(session_key)

        if not session_json:
            raise HTTPException(status_code=404, detail="Session not found or expired")

        session_data = SessionData.model_validate_json(session_json)

        if edit_request.case_details:
            session_data.case_details = edit_request.case_details.model_dump()
        if edit_request.diagnostic_framework:
            session_data.diagnostic_framework = [tier.model_dump() for tier in edit_request.diagnostic_framework]
        if edit_request.feature_likelihood_ratios:
            session_data.feature_likelihood_ratios = [lr.model_dump() for lr in edit_request.feature_likelihood_ratios]

        redis_client.setex(session_key, 3600, session_data.model_dump_json())
        logger.info("Session updated: %s", edit_request.session_id)

        return {
            "status": "success",
            "message": "Case updated successfully",
            "session_id": edit_request.session_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to edit case")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/session/{session_id}")
async def get_session_data(session_id: str, username: str = Depends(verify_credentials)):
    """Retrieve current session data for editing."""
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

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to retrieve session %s", session_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/finalize-case")
async def finalize_case(save_request: CaseSaveRequest, db: Session = Depends(get_db), username: str = Depends(verify_credentials)):
    """Save the edited case to the database."""
    logger.info("Finalize case requested: session=%s, diagnosis=%s, format=%s",
                save_request.session_id, save_request.primary_diagnosis, save_request.output_format)
    try:
        session_json = redis_client.get(f"session:{save_request.session_id}")

        if not session_json:
            raise HTTPException(status_code=404, detail="Session not found or expired")

        session_data = SessionData.model_validate_json(session_json)
        is_sim_ready = save_request.output_format == "sim_ready"

        if is_sim_ready:
            # --- Sim-Ready path: save to case_details table in sim-ready DB ---
            rendered_content = save_request.rendered_content or render_sim_ready_content(session_data.case_details)
            saved_name = save_request.title or session_data.case_details.get(
                "case_title", f"Case: {save_request.primary_diagnosis}"
            )
            custom_input = save_request.custom_input or build_default_custom_input()
            custom_evaluation = save_request.custom_evaluation or build_default_custom_evaluation()
            learner_tasks = save_request.learner_tasks or build_default_learner_tasks()

            sim_db = next(get_sim_ready_db())
            try:
                def save_sim_ready():
                    record = CaseDetailSimReady(
                        saved_name=saved_name,
                        content=rendered_content,
                        custom_input=custom_input,
                        custom_evaluation=custom_evaluation,
                        allow_orders=save_request.allow_orders,
                        learner_tasks=learner_tasks,
                    )
                    sim_db.add(record)
                    sim_db.commit()
                    sim_db.refresh(record)
                    return record

                record = retry_db_operation(save_sim_ready)
                logger.info("Sim-ready case saved: id=%d", record.id)

                # Persist the canonical record: clinical content + framework + LRs.
                # Previously this analysis was generated and thrown away on this path
                # (ADR-001). Failures here must not fail the case save, which is
                # already committed — log loudly and continue.
                if AUTHORING_ENABLED:
                    try:
                        version = persist_case_version(
                            sim_db,
                            title=saved_name,
                            description=save_request.description,
                            primary_diagnosis=save_request.primary_diagnosis,
                            case_details=session_data.case_details,
                            diagnostic_framework=session_data.diagnostic_framework,
                            feature_likelihood_ratios=session_data.feature_likelihood_ratios,
                            output_format="sim_ready",
                            rendered_content=rendered_content,
                            render_detached=save_request.rendered_content is not None,
                            case_detail_id=record.id,
                        )
                        logger.info(
                            "Authoring record saved: case_version=%d (family=%d v%d), "
                            "%d tiers, %d LRs",
                            version.id, version.case_family_id, version.version,
                            len(session_data.diagnostic_framework or []),
                            len(session_data.feature_likelihood_ratios or []),
                        )
                    except Exception:
                        sim_db.rollback()
                        logger.exception(
                            "Failed to persist authoring record for case_detail_id=%d; "
                            "the case itself was saved", record.id
                        )
            finally:
                sim_db.close()

            redis_client.delete(f"session:{save_request.session_id}")
            logger.info("Sim-ready case finalized: id=%d, session cleaned up", record.id)

            return SimReadyCaseResponse(
                case_id=record.id,
                saved_name=record.saved_name,
                output_format="sim_ready",
            )
        else:
            # --- Beta path: save to cases/frameworks/LRs tables (original behavior) ---
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
            logger.info("Case saved to DB: id=%d", case.id)

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

            redis_client.delete(f"session:{save_request.session_id}")
            logger.info("Case finalized: id=%d, session cleaned up", case.id)

            return CaseResponse(
                case_id=case.id,
                case_details=session_data.case_details,
                diagnostic_framework=session_data.diagnostic_framework,
                feature_likelihood_ratios=session_data.feature_likelihood_ratios
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to finalize case")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-case", response_model=CaseResponse)
async def generate_case(case_input: CaseInput, db: Session = Depends(get_db), username: str = Depends(verify_credentials)):
    """Generate and save case in one step (legacy flow)."""
    logger.info("Generate case requested: diagnosis=%s", case_input.primary_diagnosis)
    try:
        case_details = await llm_service.generate_case_details_async(
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

        diagnostic_framework = await llm_service.generate_diagnostic_framework_async(
            case_details,
            case_input.primary_diagnosis
        )

        def save_frameworks():
            for tier in diagnostic_framework.tiers:
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

        feature_lrs = await llm_service.generate_feature_likelihood_ratios_async(
            case_details,
            diagnostic_framework
        )

        def save_feature_lrs():
            for feature in feature_lrs.feature_likelihood_ratios:
                lr = FeatureLikelihoodRatio(
                    case_id=case.id,
                    framework_id=None,
                    feature_name=feature.feature_name,
                    feature_category=feature.feature_category.value,
                    diagnostic_bucket=feature.diagnostic_bucket,
                    likelihood_ratio=feature.likelihood_ratio
                )
                db.add(lr)
            db.commit()

        retry_db_operation(save_feature_lrs)
        logger.info("Case generated and saved: id=%d", case.id)

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

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to generate case")
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
    """Get information about available simulator exports for a case."""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    frameworks = db.query(DiagnosticFramework).filter(DiagnosticFramework.case_id == case_id).all()
    feature_lrs = db.query(FeatureLikelihoodRatio).filter(FeatureLikelihoodRatio.case_id == case_id).all()

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
    """Debug endpoint to see raw LR data before matrix creation."""
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
    """Export feature likelihood ratio matrix as CSV for simulator app."""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    frameworks = db.query(DiagnosticFramework).filter(DiagnosticFramework.case_id == case_id).all()
    feature_lrs = db.query(FeatureLikelihoodRatio).filter(FeatureLikelihoodRatio.case_id == case_id).all()

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

    lr_matrix = create_feature_lr_matrix(
        case.case_details,
        diagnostic_framework,
        feature_likelihood_ratios
    )

    validation = validate_lr_matrix_for_simulator(lr_matrix)
    if not validation["valid"]:
        raise HTTPException(status_code=400, detail=f"Invalid LR matrix: {validation['errors']}")

    csv_content = export_to_csv(lr_matrix)

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=case_{case_id}_lr_matrix.csv"}
    )


@app.get("/case/{case_id}/simulator-export/lr-matrix-excel")
async def export_lr_matrix_excel(case_id: int, tier_level: int = 1, db: Session = Depends(get_db)):
    """Export feature likelihood ratio matrix as Excel for simulator app."""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    frameworks = db.query(DiagnosticFramework).filter(DiagnosticFramework.case_id == case_id).all()
    feature_lrs = db.query(FeatureLikelihoodRatio).filter(FeatureLikelihoodRatio.case_id == case_id).all()

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

    lr_matrix = create_feature_lr_matrix(
        case.case_details,
        diagnostic_framework,
        feature_likelihood_ratios
    )

    validation = validate_lr_matrix_for_simulator(lr_matrix)
    if not validation["valid"]:
        raise HTTPException(status_code=400, detail=f"Invalid LR matrix: {validation['errors']}")

    excel_content = export_to_excel(lr_matrix)

    return Response(
        content=excel_content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=case_{case_id}_lr_matrix.xlsx"}
    )


@app.get("/case/{case_id}/simulator-export/prior-probabilities")
async def export_prior_probabilities(case_id: int, tier_level: int = 1, db: Session = Depends(get_db)):
    """Export prior probabilities for specific tier as JSON for simulator app."""
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
    """Export case summary as text file for simulator app transcript input."""
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


# --- Sim-Ready Endpoints ---

@app.get("/sim-ready/cases")
async def list_sim_ready_cases():
    """List all sim-ready cases from the simulator database."""
    sim_db = next(get_sim_ready_db())
    try:
        cases = sim_db.query(CaseDetailSimReady).all()
        return [
            {"id": c.id, "saved_name": c.saved_name, "allow_orders": c.allow_orders}
            for c in cases
        ]
    finally:
        sim_db.close()


@app.get("/sim-ready/case/{case_id}/analysis")
async def get_sim_ready_case_analysis(case_id: int):
    """Diagnostic framework + likelihood ratios for a sim-ready case.

    Before ADR-001 this data existed only in Streamlit session state and was lost on
    refresh. The Export tab should prefer this endpoint over in-memory state.
    """
    if not AUTHORING_ENABLED:
        raise HTTPException(status_code=503, detail="Authoring schema is not available")

    sim_db = next(get_sim_ready_db())
    try:
        analysis = load_analysis(sim_db, case_id)
        if analysis is None:
            raise HTTPException(
                status_code=404,
                detail="No stored analysis for this case. Cases finalized before the "
                       "authoring record existed have no persisted framework/LR data.",
            )
        return analysis
    finally:
        sim_db.close()


@app.get("/sim-ready/case/{case_id}")
async def get_sim_ready_case(case_id: int):
    """Retrieve a single sim-ready case."""
    sim_db = next(get_sim_ready_db())
    try:
        case = sim_db.query(CaseDetailSimReady).filter(CaseDetailSimReady.id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="Sim-ready case not found")
        return {
            "id": case.id,
            "saved_name": case.saved_name,
            "content": case.content,
            "custom_input": case.custom_input,
            "custom_evaluation": case.custom_evaluation,
            "allow_orders": case.allow_orders,
            "learner_tasks": case.learner_tasks,
        }
    finally:
        sim_db.close()


@app.put("/sim-ready/case/{case_id}")
async def update_sim_ready_case(
    case_id: int,
    update: SimReadyCaseUpdateRequest,
    credentials: str = Depends(verify_credentials),
):
    """Update an existing sim-ready case in-place."""
    sim_db = next(get_sim_ready_db())
    try:
        case = sim_db.query(CaseDetailSimReady).filter(CaseDetailSimReady.id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="Sim-ready case not found")

        if update.saved_name is not None:
            case.saved_name = update.saved_name
        if update.content is not None:
            case.content = update.content
        if update.custom_input is not None:
            case.custom_input = update.custom_input
        if update.custom_evaluation is not None:
            case.custom_evaluation = update.custom_evaluation
        if update.allow_orders is not None:
            case.allow_orders = update.allow_orders
        if update.learner_tasks is not None:
            case.learner_tasks = update.learner_tasks

        sim_db.commit()
        sim_db.refresh(case)

        return {
            "case_id": case.id,
            "saved_name": case.saved_name,
            "content": case.content,
            "custom_input": case.custom_input,
            "custom_evaluation": case.custom_evaluation,
            "allow_orders": case.allow_orders,
            "learner_tasks": case.learner_tasks,
        }
    finally:
        sim_db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
