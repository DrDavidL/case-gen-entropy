import json
import logging
import os
import time
import uuid

import redis
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.models.database import (
    Base,
    Case,
    CaseDetailSimReady,
    DiagnosticFramework,
    FeatureLikelihoodRatio,
    SimReadyBase,
    authoring_schema_ready,
    engine,
    final_orders_schema_ready,
    get_db,
    get_sim_ready_db,
    sim_ready_engine,
)
from backend.models.editing_schemas import (
    CaseEditRequest,
    CasePreviewResponse,
    CaseSaveRequest,
    FinalOrdersUpdateRequest,
    OracleRunRequest,
    ProposeFinalOrdersRequest,
    RegenerateLRRequest,
    RegenerateLRResponse,
    SessionData,
    SimReadyCasePreviewResponse,
    SimReadyCaseUpdateRequest,
)
from backend.models.schemas import (
    CaseInput,
    CaseOutputFiles,
    CaseResponse,
    SimReadyCaseResponse,
)
from backend.models.structured_outputs import (
    CaseDetailsStructured,
    DiagnosticBucketStructured,
    DiagnosticFrameworkStructured,
    DiagnosticTierStructured,
    ProbabilityEntry,
)
from backend.utils import final_orders_store, oracle_service, oracle_stems, panel_roster
from backend.utils.auth import verify_credentials
from backend.utils.authoring_store import load_analysis, persist_case_version
from backend.utils.build_info import get_build_info
from backend.utils.llm_service import LLMService
from backend.utils.panel_runner import describe_settings
from backend.utils.sim_ready_transform import (
    build_default_custom_evaluation,
    build_default_custom_input,
    build_default_learner_tasks,
    coerce_json_field,
    normalize_image_links,
    render_sim_ready_content,
)
from backend.utils.simulator_export import (
    create_case_summary_for_simulator,
    create_feature_lr_matrix,
    create_prior_probabilities_file,
    export_to_csv,
    export_to_excel,
    validate_lr_matrix_for_simulator,
)

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
# Probed separately from AUTHORING_ENABLED: a deploy carrying migration 0002 but not 0003
# must keep persisting framework and LR data, and only lose Final Orders / Oracle.
FINAL_ORDERS_ENABLED = False
if sim_ready_engine is not None:
    SimReadyBase.metadata.create_all(
        bind=sim_ready_engine,
        tables=[CaseDetailSimReady.__table__],
    )
    AUTHORING_ENABLED = authoring_schema_ready(sim_ready_engine)
    FINAL_ORDERS_ENABLED = AUTHORING_ENABLED and final_orders_schema_ready(
        sim_ready_engine
    )
    logger.info(
        "Authoring persistence: %s | Final Orders + Oracle: %s",
        "enabled" if AUTHORING_ENABLED else "DISABLED",
        "enabled" if FINAL_ORDERS_ENABLED else "DISABLED",
    )

_build = get_build_info()
logger.info(
    "Build: sha=%s built=%s tag=%s",
    _build["git_sha"],
    _build["build_time"],
    _build["image_tag"],
)

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
                wait = delay * (2**attempt)
                logger.warning(
                    "DB SSL error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    max_retries,
                    wait,
                    str(e)[:100],
                )
                time.sleep(wait)
                continue
            logger.error(
                "DB operation failed after %d attempts: %s", attempt + 1, str(e)[:200]
            )
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
        "final_orders": FINAL_ORDERS_ENABLED,
    }


@app.post("/preview-case")
async def preview_case(
    case_input: CaseInput, username: str = Depends(verify_credentials)
):
    """Generate case content for preview/editing without saving to database."""
    logger.info(
        "Preview case requested by %s: diagnosis=%s, format=%s",
        username,
        case_input.primary_diagnosis,
        case_input.output_format,
    )
    try:
        is_sim_ready = case_input.output_format == "sim_ready"

        # Step 1: generate case details
        if is_sim_ready:
            sim_ready_details = await llm_service.generate_sim_ready_case_details_async(
                case_input.description, case_input.primary_diagnosis
            )
            # Adapt for downstream LR pipeline
            case_details = llm_service._sim_ready_to_case_details(sim_ready_details)
            logger.info("Sim-ready case details generated")
        else:
            case_details = await llm_service.generate_case_details_async(
                case_input.description, case_input.primary_diagnosis
            )
            logger.info("Case details generated")

        # Step 2: diagnostic framework depends on case_details
        diagnostic_framework = await llm_service.generate_diagnostic_framework_async(
            case_details, case_input.primary_diagnosis
        )
        logger.info("Diagnostic framework generated")

        # Step 3: feature LRs depend on both
        feature_lrs = await llm_service.generate_feature_likelihood_ratios_async(
            case_details, diagnostic_framework
        )
        logger.info("Feature likelihood ratios generated")

        # Create session for editing
        session_id = str(uuid.uuid4())

        # Convert structured outputs to editable format
        diagnostic_tiers = []
        for tier in diagnostic_framework.tiers:
            prob_dict = {
                prob.bucket_name: prob.probability
                for prob in tier.a_priori_probabilities
            }
            diagnostic_tiers.append(
                {
                    "tier_level": tier.tier_level,
                    "buckets": [bucket.model_dump() for bucket in tier.buckets],
                    "a_priori_probabilities": prob_dict,
                }
            )

        # For sim-ready, store the full sim-ready data; for beta, store the original
        case_details_dump = (
            sim_ready_details.model_dump()
            if is_sim_ready
            else case_details.model_dump()
        )

        # Store in Redis for editing session
        session_data = SessionData(
            case_details=case_details_dump,
            diagnostic_framework=diagnostic_tiers,
            feature_likelihood_ratios=[
                lr.model_dump() for lr in feature_lrs.feature_likelihood_ratios
            ],
            original_input=case_input,
            output_format=case_input.output_format,
        )

        redis_client.setex(
            f"session:{session_id}",
            3600,  # 1 hour expiration
            session_data.model_dump_json(),
        )
        logger.info(
            "Session created: %s (format=%s)", session_id, case_input.output_format
        )

        if is_sim_ready:
            rendered_content = render_sim_ready_content(case_details_dump)
            return SimReadyCasePreviewResponse(
                session_id=session_id,
                case_details=case_details_dump,
                diagnostic_framework=diagnostic_tiers,
                feature_likelihood_ratios=[
                    lr.model_dump() for lr in feature_lrs.feature_likelihood_ratios
                ],
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
                feature_likelihood_ratios=[
                    lr.model_dump() for lr in feature_lrs.feature_likelihood_ratios
                ],
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to generate case preview")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Deployment diagnostics: which services are reachable and which env vars are set.

    Ported from the pre-divergence `main` lineage, with two secret leaks removed --
    it returned the raw REDIS_URL (which carries a password) and the raw
    APP_USERNAME (half of the basic-auth credential), and logged the Redis URL on
    error. This endpoint is unauthenticated, so it reports only presence.
    """
    env_vars = {
        "OPENAI_API_KEY": "Set" if os.getenv("OPENAI_API_KEY") else "Missing",
        "REDIS_URL": "Set" if os.getenv("REDIS_URL") else "Missing",
        "POSTGRES_URL": "Set" if os.getenv("POSTGRES_URL") else "Missing",
        "POSTGRES_URL_SIM_READY": "Set"
        if os.getenv("POSTGRES_URL_SIM_READY")
        else "Missing",
        "APP_USERNAME": "Set" if os.getenv("APP_USERNAME") else "Missing",
        "APP_PASSWORD": "Set" if os.getenv("APP_PASSWORD") else "Missing",
    }

    try:
        redis_client.ping()
        redis_status = "Connected"
    except Exception as e:
        redis_status = f"Failed: {type(e).__name__}"
        logger.error("Redis health check failed: %s", str(e)[:200])

    return {
        "status": "healthy",
        "build": get_build_info(),
        "environment": env_vars,
        "redis": redis_status,
        "authoring_persistence": AUTHORING_ENABLED,
        "final_orders": FINAL_ORDERS_ENABLED,
        "oracle_settings": describe_settings(),
        "oracle_stem_version": oracle_stems.DEFAULT_STEM_VERSION,
    }


@app.post("/regenerate-lrs", response_model=RegenerateLRResponse)
async def regenerate_lrs(
    request: RegenerateLRRequest, username: str = Depends(verify_credentials)
):
    """Regenerate feature likelihood ratios for a session using strict bucket names.

    Ported from the pre-divergence `main` lineage with two changes: it now requires
    auth (it spends LLM budget and mutates session state, so it belongs with the
    other mutating endpoints), and it uses the async LLM wrapper so the call does
    not block the event loop.
    """
    session_data: SessionData | None = None
    try:
        raw = redis_client.get(f"session:{request.session_id}")
        if raw:
            session_data = SessionData.model_validate_json(raw)
    except Exception as e:
        logger.warning("Could not load session from Redis: %s", str(e)[:200])

    if session_data is None:
        if not (request.case_details and request.diagnostic_framework):
            raise HTTPException(
                status_code=404,
                detail="Session not found or expired, and no case/framework provided",
            )
        session_data = SessionData(
            case_details=request.case_details,
            diagnostic_framework=request.diagnostic_framework,
            feature_likelihood_ratios=[],
            original_input=CaseInput(description="", primary_diagnosis=""),
        )

    try:
        case_struct = CaseDetailsStructured.model_validate(session_data.case_details)
        tiers_struct = []
        for tier in session_data.diagnostic_framework:
            probs = tier.get("a_priori_probabilities", {})
            tiers_struct.append(
                DiagnosticTierStructured(
                    tier_level=int(tier.get("tier_level", 1)),
                    buckets=[
                        DiagnosticBucketStructured(
                            name=b.get("name", ""), description=b.get("description", "")
                        )
                        for b in tier.get("buckets", [])
                    ],
                    a_priori_probabilities=[
                        ProbabilityEntry(bucket_name=k, probability=float(v))
                        for k, v in probs.items()
                    ],
                )
            )
        framework_struct = DiagnosticFrameworkStructured(tiers=tiers_struct)
    except Exception as e:
        logger.exception("Failed to build structured inputs for LR regeneration")
        raise HTTPException(
            status_code=400, detail=f"Invalid case/framework structure: {e}"
        ) from e

    try:
        flr_struct = await llm_service.generate_feature_likelihood_ratios_async(
            case_struct, framework_struct
        )
        flr_list = [lr.model_dump() for lr in flr_struct.feature_likelihood_ratios]
    except Exception as e:
        logger.exception("LLM LR regeneration failed")
        raise HTTPException(
            status_code=500, detail=f"Failed to regenerate likelihood ratios: {e}"
        ) from e

    try:
        session_data.feature_likelihood_ratios = flr_list
        redis_client.setex(
            f"session:{request.session_id}", 3600, session_data.model_dump_json()
        )
    except Exception as e:
        logger.warning("Failed to persist regenerated LRs to Redis: %s", str(e)[:200])

    logger.info("Regenerated %d LRs for session=%s", len(flr_list), request.session_id)
    return RegenerateLRResponse(feature_likelihood_ratios=flr_list)


@app.put("/edit-case")
async def edit_case(
    edit_request: CaseEditRequest, username: str = Depends(verify_credentials)
):
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
            session_data.diagnostic_framework = [
                tier.model_dump() for tier in edit_request.diagnostic_framework
            ]
        if edit_request.feature_likelihood_ratios:
            session_data.feature_likelihood_ratios = [
                lr.model_dump() for lr in edit_request.feature_likelihood_ratios
            ]

        redis_client.setex(session_key, 3600, session_data.model_dump_json())
        logger.info("Session updated: %s", edit_request.session_id)

        return {
            "status": "success",
            "message": "Case updated successfully",
            "session_id": edit_request.session_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to edit case")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/session/{session_id}")
async def get_session_data(
    session_id: str, username: str = Depends(verify_credentials)
):
    """Retrieve current session data for editing."""
    try:
        session_json = redis_client.get(f"session:{session_id}")

        if not session_json:
            raise HTTPException(status_code=404, detail="Session not found or expired")

        session_data = SessionData.model_validate_json(session_json)

        return {
            "case_details": session_data.case_details,
            "diagnostic_framework": session_data.diagnostic_framework,
            "feature_likelihood_ratios": session_data.feature_likelihood_ratios,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to retrieve session %s", session_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/finalize-case")
async def finalize_case(
    save_request: CaseSaveRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    username: str = Depends(verify_credentials),
):
    """Save the edited case to the database."""
    logger.info(
        "Finalize case requested: session=%s, diagnosis=%s, format=%s",
        save_request.session_id,
        save_request.primary_diagnosis,
        save_request.output_format,
    )
    try:
        session_json = redis_client.get(f"session:{save_request.session_id}")

        if not session_json:
            raise HTTPException(status_code=404, detail="Session not found or expired")

        session_data = SessionData.model_validate_json(session_json)
        is_sim_ready = save_request.output_format == "sim_ready"

        if is_sim_ready:
            # --- Sim-Ready path: save to case_details table in sim-ready DB ---
            rendered_content = (
                save_request.rendered_content
                or render_sim_ready_content(session_data.case_details)
            )
            saved_name = save_request.title or session_data.case_details.get(
                "case_title", f"Case: {save_request.primary_diagnosis}"
            )
            custom_input = save_request.custom_input or build_default_custom_input()
            custom_evaluation = (
                save_request.custom_evaluation or build_default_custom_evaluation()
            )
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

                # Read the values out now, while `record` is still live. The
                # authoring write below commits on this same session, which expires
                # every loaded instance; `sim_db.close()` then detaches them, so any
                # later attribute access would try to refresh a detached object and
                # raise. Plain locals are immune to both.
                saved_case_id = record.id
                saved_case_name = record.saved_name
                logger.info("Sim-ready case saved: id=%d", saved_case_id)

                # Persist the canonical record: clinical content + framework + LRs.
                # Previously this analysis was generated and thrown away on this path
                # (ADR-001). Failures here must not fail the case save, which is
                # already committed — log loudly and continue.
                saved_version_id: int | None = None
                final_orders_saved = 0
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
                            case_detail_id=saved_case_id,
                            oracle_specialty=save_request.oracle_specialty,
                        )
                        saved_version_id = version.id
                        logger.info(
                            "Authoring record saved: case_version=%d (family=%d v%d), "
                            "%d tiers, %d LRs",
                            version.id,
                            version.case_family_id,
                            version.version,
                            len(session_data.diagnostic_framework or []),
                            len(session_data.feature_likelihood_ratios or []),
                        )
                    except Exception:
                        sim_db.rollback()
                        logger.exception(
                            "Failed to persist authoring record for case_detail_id=%d; "
                            "the case itself was saved",
                            saved_case_id,
                        )

                # Final Orders are optional. Zero of them is the normal case and means
                # the case carries no script concordance item (ADR-014). Failures here
                # must not fail the save, which is already committed.
                if (
                    saved_version_id is not None
                    and FINAL_ORDERS_ENABLED
                    and save_request.final_orders
                ):
                    try:
                        rows = final_orders_store.replace_final_orders(
                            sim_db,
                            saved_version_id,
                            [fo.model_dump() for fo in save_request.final_orders],
                        )
                        final_orders_saved = len(rows)
                    except Exception:
                        sim_db.rollback()
                        logger.exception(
                            "Failed to persist Final Orders for case_version=%d; the "
                            "case itself was saved",
                            saved_version_id,
                        )
                elif save_request.final_orders and not FINAL_ORDERS_ENABLED:
                    logger.error(
                        "%d Final Order(s) submitted but the schema is unavailable "
                        "(migration 0003 not applied); they were NOT saved",
                        len(save_request.final_orders),
                    )
            finally:
                sim_db.close()

            redis_client.delete(f"session:{save_request.session_id}")
            logger.info(
                "Sim-ready case finalized: id=%d, session cleaned up", saved_case_id
            )

            # The panel takes 3-5 minutes, so it cannot run inside this request. It also
            # only runs at finalization, never at preview: authors regenerate previews
            # repeatedly while drafting and each one would otherwise trigger 75 calls.
            oracle_started = False
            if save_request.run_oracle and final_orders_saved and saved_version_id:
                background_tasks.add_task(
                    oracle_service.run_oracle_for_case_version, saved_version_id
                )
                oracle_started = True
                logger.info(
                    "Oracle panel queued for case_version=%d (%d order(s))",
                    saved_version_id,
                    final_orders_saved,
                )

            response = SimReadyCaseResponse(
                case_id=saved_case_id,
                saved_name=saved_case_name,
                output_format="sim_ready",
            )
            return {
                **response.model_dump(),
                "case_version_id": saved_version_id,
                "final_orders_saved": final_orders_saved,
                "oracle_started": oracle_started,
            }
        else:
            # --- Beta path: save to cases/frameworks/LRs tables (original behavior) ---
            def save_case():
                case = Case(
                    title=save_request.title
                    or f"Case: {save_request.primary_diagnosis}",
                    description=save_request.description,
                    primary_diagnosis=save_request.primary_diagnosis,
                    case_details=session_data.case_details,
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
                        a_priori_probabilities=tier_data["a_priori_probabilities"],
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
                        likelihood_ratio=lr_data["likelihood_ratio"],
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
                feature_likelihood_ratios=session_data.feature_likelihood_ratios,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to finalize case")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-case", response_model=CaseResponse)
async def generate_case(
    case_input: CaseInput,
    db: Session = Depends(get_db),
    username: str = Depends(verify_credentials),
):
    """Generate and save case in one step (legacy flow)."""
    logger.info("Generate case requested: diagnosis=%s", case_input.primary_diagnosis)
    try:
        case_details = await llm_service.generate_case_details_async(
            case_input.description, case_input.primary_diagnosis
        )

        def save_case():
            case = Case(
                title=f"Case: {case_input.primary_diagnosis}",
                description=case_input.description,
                primary_diagnosis=case_input.primary_diagnosis,
                case_details=case_details.model_dump(),
            )
            db.add(case)
            db.commit()
            db.refresh(case)
            return case

        case = retry_db_operation(save_case)

        diagnostic_framework = await llm_service.generate_diagnostic_framework_async(
            case_details, case_input.primary_diagnosis
        )

        def save_frameworks():
            for tier in diagnostic_framework.tiers:
                prob_dict = {
                    prob.bucket_name: prob.probability
                    for prob in tier.a_priori_probabilities
                }
                framework = DiagnosticFramework(
                    case_id=case.id,
                    tier_level=tier.tier_level,
                    diagnostic_buckets=[bucket.model_dump() for bucket in tier.buckets],
                    a_priori_probabilities=prob_dict,
                )
                db.add(framework)
            db.commit()

        retry_db_operation(save_frameworks)

        feature_lrs = await llm_service.generate_feature_likelihood_ratios_async(
            case_details, diagnostic_framework
        )

        def save_feature_lrs():
            for feature in feature_lrs.feature_likelihood_ratios:
                lr = FeatureLikelihoodRatio(
                    case_id=case.id,
                    framework_id=None,
                    feature_name=feature.feature_name,
                    feature_category=feature.feature_category.value,
                    diagnostic_bucket=feature.diagnostic_bucket,
                    likelihood_ratio=feature.likelihood_ratio,
                )
                db.add(lr)
            db.commit()

        retry_db_operation(save_feature_lrs)
        logger.info("Case generated and saved: id=%d", case.id)

        diagnostic_tiers = []
        for tier in diagnostic_framework.tiers:
            prob_dict = {
                prob.bucket_name: prob.probability
                for prob in tier.a_priori_probabilities
            }
            diagnostic_tiers.append(
                {
                    "tier_level": tier.tier_level,
                    "buckets": [bucket.model_dump() for bucket in tier.buckets],
                    "a_priori_probabilities": prob_dict,
                }
            )

        return CaseResponse(
            case_id=case.id,
            case_details=case_details.model_dump(),
            diagnostic_framework=diagnostic_tiers,
            feature_likelihood_ratios=[
                lr.model_dump() for lr in feature_lrs.feature_likelihood_ratios
            ],
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

    frameworks = (
        db.query(DiagnosticFramework)
        .filter(DiagnosticFramework.case_id == case_id)
        .all()
    )
    feature_lrs = (
        db.query(FeatureLikelihoodRatio)
        .filter(FeatureLikelihoodRatio.case_id == case_id)
        .all()
    )

    case_details_json = {
        "case_id": case.id,
        "title": case.title,
        "description": case.description,
        "primary_diagnosis": case.primary_diagnosis,
        "presentation": case.case_details.get("presentation"),
        "patient_personality": case.case_details.get("patient_personality"),
        "history_questions": case.case_details.get("history_questions", []),
        "physical_exam_findings": case.case_details.get("physical_exam_findings", []),
        "diagnostic_workup": case.case_details.get("diagnostic_workup", []),
    }

    a_priori_probabilities_json = {}
    for framework in frameworks:
        tier_key = f"tier_{framework.tier_level}"
        a_priori_probabilities_json[tier_key] = {
            "buckets": framework.diagnostic_buckets,
            "probabilities": framework.a_priori_probabilities,
        }

    feature_likelihood_ratios_json = {
        "history": {},
        "physical_exam": {},
        "diagnostic_workup": {},
    }

    for lr in feature_lrs:
        category = lr.feature_category
        if category not in feature_likelihood_ratios_json:
            feature_likelihood_ratios_json[category] = {}

        feature_name = lr.feature_name
        if feature_name not in feature_likelihood_ratios_json[category]:
            feature_likelihood_ratios_json[category][feature_name] = {}

        feature_likelihood_ratios_json[category][feature_name][lr.diagnostic_bucket] = (
            lr.likelihood_ratio
        )

    return CaseOutputFiles(
        case_details_json=case_details_json,
        a_priori_probabilities_json=a_priori_probabilities_json,
        feature_likelihood_ratios_json=feature_likelihood_ratios_json,
    )


@app.get("/cases")
async def list_cases(db: Session = Depends(get_db)):
    cases = db.query(Case).all()
    return [
        {
            "id": case.id,
            "title": case.title,
            "primary_diagnosis": case.primary_diagnosis,
        }
        for case in cases
    ]


@app.get("/case/{case_id}/simulator-exports")
async def get_simulator_export_info(case_id: int, db: Session = Depends(get_db)):
    """Get information about available simulator exports for a case."""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    frameworks = (
        db.query(DiagnosticFramework)
        .filter(DiagnosticFramework.case_id == case_id)
        .all()
    )
    feature_lrs = (
        db.query(FeatureLikelihoodRatio)
        .filter(FeatureLikelihoodRatio.case_id == case_id)
        .all()
    )

    available_tiers = sorted({f.tier_level for f in frameworks})

    return {
        "case_id": case_id,
        "case_title": case.title,
        "available_tiers": available_tiers,
        "total_features": len({lr.feature_name for lr in feature_lrs}),
        "total_diagnostic_buckets": len({lr.diagnostic_bucket for lr in feature_lrs}),
        "available_exports": [
            "feature_lr_matrix_csv",
            "feature_lr_matrix_excel",
            "prior_probabilities_json",
            "case_summary_txt",
        ],
    }


@app.get("/case/{case_id}/debug-lr-data")
async def debug_lr_data(case_id: int, db: Session = Depends(get_db)):
    """Debug endpoint to see raw LR data before matrix creation."""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    feature_lrs = (
        db.query(FeatureLikelihoodRatio)
        .filter(FeatureLikelihoodRatio.case_id == case_id)
        .all()
    )

    debug_data = {
        "total_feature_lrs": len(feature_lrs),
        "feature_lrs": [
            {
                "feature_name": lr.feature_name,
                "feature_category": lr.feature_category,
                "diagnostic_bucket": lr.diagnostic_bucket,
                "likelihood_ratio": lr.likelihood_ratio,
            }
            for lr in feature_lrs
        ],
        "case_details_features": {
            "history_questions": [
                hq.get("question", "")
                for hq in case.case_details.get("history_questions", [])
            ],
            "physical_exam": [
                pe.get("examination", "")
                for pe in case.case_details.get("physical_exam_findings", [])
            ],
            "diagnostic_workup": [
                dw.get("test", "")
                for dw in case.case_details.get("diagnostic_workup", [])
            ],
        },
    }

    return debug_data


@app.get("/case/{case_id}/simulator-export/lr-matrix-csv")
async def export_lr_matrix_csv(
    case_id: int, tier_level: int = 2, db: Session = Depends(get_db)
):
    """Export feature likelihood ratio matrix as CSV for simulator app."""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    frameworks = (
        db.query(DiagnosticFramework)
        .filter(DiagnosticFramework.case_id == case_id)
        .all()
    )
    feature_lrs = (
        db.query(FeatureLikelihoodRatio)
        .filter(FeatureLikelihoodRatio.case_id == case_id)
        .all()
    )

    diagnostic_framework = []
    for framework in frameworks:
        diagnostic_framework.append(
            {
                "tier_level": framework.tier_level,
                "buckets": framework.diagnostic_buckets,
                "a_priori_probabilities": framework.a_priori_probabilities,
            }
        )

    feature_likelihood_ratios = []
    for lr in feature_lrs:
        feature_likelihood_ratios.append(
            {
                "feature_name": lr.feature_name,
                "feature_category": lr.feature_category,
                "diagnostic_bucket": lr.diagnostic_bucket,
                "likelihood_ratio": lr.likelihood_ratio,
            }
        )

    lr_matrix = create_feature_lr_matrix(
        case.case_details,
        diagnostic_framework,
        feature_likelihood_ratios,
        tier_level=tier_level,
    )

    validation = validate_lr_matrix_for_simulator(lr_matrix)
    if not validation["valid"]:
        raise HTTPException(
            status_code=400, detail=f"Invalid LR matrix: {validation['errors']}"
        )

    csv_content = export_to_csv(lr_matrix)

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=case_{case_id}_lr_matrix.csv"
        },
    )


@app.get("/case/{case_id}/simulator-export/lr-matrix-excel")
async def export_lr_matrix_excel(
    case_id: int, tier_level: int = 2, db: Session = Depends(get_db)
):
    """Export feature likelihood ratio matrix as Excel for simulator app."""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    frameworks = (
        db.query(DiagnosticFramework)
        .filter(DiagnosticFramework.case_id == case_id)
        .all()
    )
    feature_lrs = (
        db.query(FeatureLikelihoodRatio)
        .filter(FeatureLikelihoodRatio.case_id == case_id)
        .all()
    )

    diagnostic_framework = []
    for framework in frameworks:
        diagnostic_framework.append(
            {
                "tier_level": framework.tier_level,
                "buckets": framework.diagnostic_buckets,
                "a_priori_probabilities": framework.a_priori_probabilities,
            }
        )

    feature_likelihood_ratios = []
    for lr in feature_lrs:
        feature_likelihood_ratios.append(
            {
                "feature_name": lr.feature_name,
                "feature_category": lr.feature_category,
                "diagnostic_bucket": lr.diagnostic_bucket,
                "likelihood_ratio": lr.likelihood_ratio,
            }
        )

    lr_matrix = create_feature_lr_matrix(
        case.case_details,
        diagnostic_framework,
        feature_likelihood_ratios,
        tier_level=tier_level,
    )

    validation = validate_lr_matrix_for_simulator(lr_matrix)
    if not validation["valid"]:
        raise HTTPException(
            status_code=400, detail=f"Invalid LR matrix: {validation['errors']}"
        )

    excel_content = export_to_excel(lr_matrix)

    return Response(
        content=excel_content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=case_{case_id}_lr_matrix.xlsx"
        },
    )


@app.get("/case/{case_id}/simulator-export/prior-probabilities")
async def export_prior_probabilities(
    case_id: int, tier_level: int = 2, db: Session = Depends(get_db)
):
    """Export prior probabilities for specific tier as JSON for simulator app."""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    frameworks = (
        db.query(DiagnosticFramework)
        .filter(DiagnosticFramework.case_id == case_id)
        .all()
    )

    diagnostic_framework = []
    for framework in frameworks:
        diagnostic_framework.append(
            {
                "tier_level": framework.tier_level,
                "buckets": framework.diagnostic_buckets,
                "a_priori_probabilities": framework.a_priori_probabilities,
            }
        )

    prior_probs = create_prior_probabilities_file(diagnostic_framework, tier_level)

    if not prior_probs:
        raise HTTPException(
            status_code=404,
            detail=f"No prior probabilities found for tier {tier_level}",
        )

    total_prob = sum(prior_probs.values())
    if abs(total_prob - 1.0) > 0.01:
        raise HTTPException(
            status_code=400,
            detail=f"Prior probabilities sum to {total_prob:.3f}, must sum to 1.0",
        )

    return Response(
        content=json.dumps(prior_probs, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=case_{case_id}_tier_{tier_level}_priors.json"
        },
    )


@app.get("/case/{case_id}/simulator-export/case-summary")
async def export_case_summary(case_id: int, db: Session = Depends(get_db)):
    """Export case summary as text file for simulator app transcript input."""
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    summary_text = create_case_summary_for_simulator(
        case.case_details, case.primary_diagnosis, case_id
    )

    return Response(
        content=summary_text,
        media_type="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename=case_{case_id}_summary.txt"
        },
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
        case = (
            sim_db.query(CaseDetailSimReady)
            .filter(CaseDetailSimReady.id == case_id)
            .first()
        )
        if not case:
            raise HTTPException(status_code=404, detail="Sim-ready case not found")
        custom_input = coerce_json_field(
            case.custom_input, build_default_custom_input()
        )
        custom_input["Image Links"] = normalize_image_links(
            custom_input.get("Image Links")
        )
        return {
            "id": case.id,
            "saved_name": case.saved_name,
            "content": case.content,
            "custom_input": custom_input,
            "custom_evaluation": coerce_json_field(
                case.custom_evaluation, build_default_custom_evaluation()
            ),
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
        case = (
            sim_db.query(CaseDetailSimReady)
            .filter(CaseDetailSimReady.id == case_id)
            .first()
        )
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


# --- Final Orders (SCT) + Oracle panel ---
#
# A case with no Final Orders has no script concordance item and gets no Oracle panel.
# That is the whole opt-out mechanism and it is deliberate (ADR-014).


def _require_final_orders() -> None:
    if not FINAL_ORDERS_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Final Orders are unavailable: the authoring schema is missing "
            "case_final_orders / panel_runs / panel_ratings. Run 'alembic upgrade head' "
            "in the direct-sim repo.",
        )


def _resolve_case_version(sim_db: Session, case_id: int):
    """The latest case version behind a simulator-facing case row, or 404."""
    version = final_orders_store.latest_version_for_case_detail(sim_db, case_id)
    if version is None:
        raise HTTPException(
            status_code=404,
            detail="No authoring record for this case. Cases finalized before the "
            "authoring record existed cannot carry Final Orders until they are "
            "re-saved.",
        )
    return version


@app.post("/final-orders/propose")
async def propose_final_orders(
    request: ProposeFinalOrdersRequest, username: str = Depends(verify_credentials)
):
    """Propose candidate Final Orders. Writes nothing.

    Candidates are suggestions; the author decides. Anything accepted is stored with
    provenance `llm_suggested_accepted` so a reviewer can test whether model-proposed
    orders behave differently from author-written ones (ADR-004).
    """
    case_details_raw = request.case_details
    primary_diagnosis = request.primary_diagnosis or ""

    if case_details_raw is None and request.session_id:
        raw = redis_client.get(f"session:{request.session_id}")
        if not raw:
            raise HTTPException(status_code=404, detail="Session not found or expired")
        session_data = SessionData.model_validate_json(raw)
        case_details_raw = session_data.case_details
        primary_diagnosis = (
            primary_diagnosis or session_data.original_input.primary_diagnosis
        )

    if not case_details_raw:
        raise HTTPException(
            status_code=400, detail="Provide either session_id or case_details"
        )

    try:
        # Sim-ready records carry the expanded shape; adapt to the common one the way
        # the framework and LR calls already do.
        if (
            "diagnostic_workup" in case_details_raw
            and "presentation" not in case_details_raw
        ):
            case_struct = CaseDetailsStructured(
                presentation=case_details_raw.get("paragraph_summary", ""),
                patient_personality=(
                    (case_details_raw.get("patient_approach") or {}).get(
                        "communication_style", ""
                    )
                ),
                history_questions=case_details_raw.get("history_questions", []),
                physical_exam_findings=case_details_raw.get(
                    "physical_exam_findings", []
                ),
                diagnostic_workup=case_details_raw.get("diagnostic_workup", []),
            )
        else:
            case_struct = CaseDetailsStructured.model_validate(case_details_raw)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid case_details structure: {e}"
        ) from e

    try:
        proposed = await llm_service.propose_final_orders_async(
            case_struct, primary_diagnosis, request.max_candidates
        )
    except Exception as e:
        logger.exception("Final Order proposal failed")
        raise HTTPException(
            status_code=500, detail=f"Failed to propose Final Orders: {e}"
        ) from e

    stem = oracle_stems.get_stem()
    candidates = []
    for candidate in proposed.candidates:
        action = candidate.stem_action or oracle_stems.default_action_phrase(
            candidate.order_text
        )
        candidates.append(
            {
                **candidate.model_dump(),
                # The rendered item is returned so the author reads the exact sentence a
                # learner will read before accepting the order, rather than after.
                "learner_item_preview": oracle_stems.render_item(
                    action, audience="learner", stem_version=stem.version
                ),
                "oracle_item_preview": oracle_stems.render_item(
                    action, audience="oracle", stem_version=stem.version
                ),
            }
        )

    logger.info(
        "Proposed %d Final Order candidate(s) for %s", len(candidates), username
    )
    return {
        "candidates": candidates,
        "stem_version": stem.version,
        "stem_label": stem.label,
        "max_candidates": request.max_candidates,
    }


@app.get("/oracle/stems")
async def get_oracle_stems():
    """Every registered rating stem, rendered side by side.

    Exists so "show me the proposed change before we adopt it" is answerable from the
    code that will actually run rather than from a document that can drift away from it.
    """
    return {
        "default_stem_version": oracle_stems.DEFAULT_STEM_VERSION,
        "stems": {
            version: {
                **stem.model_dump(),
                "learner_example": oracle_stems.render_item(
                    "ordering a brain MRI", audience="learner", stem_version=version
                ),
                "oracle_example": oracle_stems.render_item(
                    "ordering a brain MRI", audience="oracle", stem_version=version
                ),
            }
            for version, stem in oracle_stems.STEMS.items()
        },
        "comparison_markdown": oracle_stems.comparison_table(),
    }


@app.get("/oracle/roster")
async def get_oracle_roster(specialty: str | None = None):
    """The versioned panel roster and provider settings a run would use."""
    roster = panel_roster.build_roster(specialty)
    return {
        "panel_roster_version": panel_roster.ROSTER_VERSION,
        "panel_size": len(roster),
        "specialty_seat_index": panel_roster.SPECIALTY_SEAT_INDEX,
        "resolved_specialty": specialty or panel_roster.DEFAULT_SPECIALTY,
        "panelists": [
            {"index": p.index, "persona_id": p.persona_id, "role": p.role}
            for p in roster
        ],
        "settings": describe_settings(),
    }


@app.get("/sim-ready/case/{case_id}/final-orders")
async def get_case_final_orders(case_id: int):
    """Final Orders for a case, resolved through its latest version.

    Unauthenticated because the simulator reads this on every case load. It returns
    author-written configuration, no learner data and no diagnosis.
    """
    _require_final_orders()
    sim_db = next(get_sim_ready_db())
    try:
        version, orders = final_orders_store.load_final_orders_for_case_detail(
            sim_db, case_id
        )
        if version is None:
            # Not a 404: a case with no authoring record simply has no Final Orders, and
            # the simulator must treat that as "behave exactly as before".
            return {
                "case_id": case_id,
                "case_version_id": None,
                "final_orders": [],
                "suppression_terms": [],
            }
        payload = [final_orders_store.serialize_final_order(o) for o in orders]
        return {
            "case_id": case_id,
            "case_version_id": version.id,
            "oracle_specialty": version.oracle_specialty,
            "final_orders": payload,
            # Flattened for the simulator's pre-model interception: every string that
            # should short-circuit before any LLM call sees it.
            "suppression_terms": [
                {
                    "final_order_id": o.id,
                    "terms": final_orders_store.suppression_terms(o),
                    "message": o.suppression_message,
                }
                for o in orders
                if o.suppress_results
            ],
        }
    finally:
        sim_db.close()


@app.put("/sim-ready/case/{case_id}/final-orders")
async def update_case_final_orders(
    case_id: int,
    update: FinalOrdersUpdateRequest,
    background_tasks: BackgroundTasks,
    username: str = Depends(verify_credentials),
):
    """Replace the Final Orders on a case's latest version.

    Editing was requested from the start rather than deferred: post-finalization editing
    was the top item from the March feedback, and shipping Final Orders that cannot be
    edited would immediately reopen it.
    """
    _require_final_orders()
    sim_db = next(get_sim_ready_db())
    try:
        version = _resolve_case_version(sim_db, case_id)

        if update.oracle_specialty is not None:
            version.oracle_specialty = update.oracle_specialty.strip() or None
            sim_db.commit()

        try:
            rows = final_orders_store.replace_final_orders(
                sim_db,
                version.id,
                [fo.model_dump() for fo in update.final_orders],
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        oracle_started = False
        if update.run_oracle and rows:
            background_tasks.add_task(
                oracle_service.run_oracle_for_case_version, version.id
            )
            oracle_started = True

        logger.info(
            "Final Orders updated by %s: case=%d version=%d count=%d",
            username,
            case_id,
            version.id,
            len(rows),
        )
        return {
            "case_id": case_id,
            "case_version_id": version.id,
            "oracle_specialty": version.oracle_specialty,
            "final_orders": [final_orders_store.serialize_final_order(r) for r in rows],
            "oracle_started": oracle_started,
        }
    finally:
        sim_db.close()


@app.get("/sim-ready/case/{case_id}/oracle/preflight")
async def oracle_preflight(case_id: int, username: str = Depends(verify_credentials)):
    """Everything checkable before spending a model call.

    Shows the author the exact blinded context the panel will see, the leak-audit
    verdict, the rendered items, and the roster. `ready: false` with reason
    `diagnosis_leak` is blocking — a warning here would be dismissed, and the resulting
    distribution would look valid while measuring nothing.
    """
    _require_final_orders()
    sim_db = next(get_sim_ready_db())
    try:
        version = _resolve_case_version(sim_db, case_id)
        result = oracle_service.preflight(sim_db, version.id)
        return {"case_id": case_id, **result}
    finally:
        sim_db.close()


@app.post("/sim-ready/case/{case_id}/oracle/run")
async def run_oracle(
    case_id: int,
    background_tasks: BackgroundTasks,
    request: OracleRunRequest | None = None,
    username: str = Depends(verify_credentials),
):
    """Queue the Oracle panel for every Final Order on a case.

    Returns immediately with runs pending; poll `GET .../oracle`. Refuses when the case
    has no Final Orders and when the leak audit fails, so neither condition can be
    discovered after 75 calls have been spent.

    A failing leak audit can be overridden only by stating a reason, which is stored on
    every run it produces. That exists because the audit has legitimate false positives —
    "CVA" under Family History is the father's history, not this patient's diagnosis —
    and the alternative to a recorded override is an author who stops trusting the check.
    """
    _require_final_orders()
    override = (request.leak_override_reason or "").strip() if request else ""

    sim_db = next(get_sim_ready_db())
    try:
        version = _resolve_case_version(sim_db, case_id)
        preflight = oracle_service.preflight(sim_db, version.id)
        # Only a leak hit is overridable. Content drift means the panel would rate a case
        # the learner will not see, and no stated reason makes that measurement valid.
        blocked_by_leak = preflight.get("reason") == "diagnosis_leak"

        if not preflight.get("ready") and not (blocked_by_leak and override):
            raise HTTPException(
                status_code=400,
                detail={
                    "reason": preflight.get("reason"),
                    "message": preflight.get(
                        "message", "The Oracle cannot run for this case yet."
                    ),
                    "leak_audit": preflight.get("leak_audit"),
                    "content_parity": preflight.get("content_parity"),
                    "override_available": blocked_by_leak,
                },
            )
        version_id = version.id
        estimated = preflight.get("estimated_calls")
    finally:
        sim_db.close()

    background_tasks.add_task(
        oracle_service.run_oracle_for_case_version,
        version_id,
        leak_override_reason=override or None,
    )
    logger.info(
        "Oracle panel queued by %s: case=%d version=%d (~%s calls)%s",
        username,
        case_id,
        version_id,
        estimated,
        " WITH LEAK OVERRIDE" if override else "",
    )
    return {
        "case_id": case_id,
        "case_version_id": version_id,
        "status": "queued",
        "estimated_calls": estimated,
        "leak_override_applied": bool(override),
        "settings": describe_settings(),
    }


@app.get("/sim-ready/case/{case_id}/oracle")
async def get_case_oracle(case_id: int):
    """Current Oracle distributions and item-quality flags for a case.

    Aggregates are recomputed from the per-rating rows on every read, so the scoring rule
    can change without regenerating any model output.
    """
    _require_final_orders()
    sim_db = next(get_sim_ready_db())
    try:
        version = _resolve_case_version(sim_db, case_id)
        result = oracle_service.load_oracle_for_case_version(sim_db, version.id)
        return {"case_id": case_id, **result}
    finally:
        sim_db.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
