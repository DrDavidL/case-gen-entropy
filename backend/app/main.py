import json
import logging
import os
import time
import uuid
from pathlib import Path

import redis
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
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
    AdoptCaseRequest,
    AuthCheckResponse,
    FinalizeCaseResponse,
    FinalOrdersUpdateResponse,
    CaseEditRequest,
    CasePreviewResponse,
    CaseSaveRequest,
    FinalOrdersUpdateRequest,
    OracleRunRequest,
    ProposeFinalOrdersRequest,
    RegenerateLRRequest,
    RegenerateLRResponse,
    SessionData,
    CaseAnalysisResponse,
    CaseListItemResponse,
    OracleItemPreviewRequest,
    SimReadyCaseDetailResponse,
    StructuredRecordResponse,
    SimReadyCaseCopyRequest,
    SimReadyCasePreviewResponse,
    SimReadyCaseUpdateRequest,
    SimReadyRenderPreviewRequest,
    SimReadyRenderPreviewResponse,
    SimReadyStructuredUpdateRequest,
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
from backend.utils.auth import verify_credentials, verify_credentials_silent
from backend.utils.authoring_store import (
    load_analysis,
    persist_case_version,
    snapshot_version,
)
from backend.utils.build_info import get_build_info
from backend.utils.llm_service import LLMService
from backend.utils.panel_runner import describe_settings
from backend.utils.sim_ready_transform import (
    DOOR_CHART_DELIMITER,
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


@app.post(
    "/preview-case",
    response_model=SimReadyCasePreviewResponse | CasePreviewResponse,
)
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


# Union, richest first. The beta branch returns `CaseResponse`, which has no
# `saved_name`, so a bare `FinalizeCaseResponse` would fail response validation and turn
# a working beta save into a 500. Streamlit hardcodes sim_ready today, but beta is still
# a supported path until ADR-001 finishes retiring it.
@app.post(
    "/finalize-case",
    response_model=FinalizeCaseResponse | CaseResponse,
)
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
            # Render from the record regardless, so the supplied content can be compared
            # against it rather than merely counted as present.
            canonical_content = render_sim_ready_content(session_data.case_details)
            rendered_content = save_request.rendered_content or canonical_content

            # `render_detached` means "someone hand-edited the markdown, so it no longer
            # follows the structured record". It used to be set from
            # `rendered_content is not None`, which is presence, not difference -- and
            # both UIs send the content back on every save whether or not the author
            # touched it. So EVERY case created through the Generate flow was born marked
            # as hand-edited, which blocks the Oracle on a brand-new case and shows the
            # author a destructive-save confirmation for an edit they never made.
            #
            # Compare instead, whitespace-insensitively, reusing the same normalisation
            # `check_content_parity` uses. Same reasoning as there: a check that fires
            # when nothing changed is one people learn to route around, and this one
            # blocks a panel.
            detached_at_save = (
                save_request.rendered_content is not None
                and oracle_service.normalize_content(save_request.rendered_content)
                != oracle_service.normalize_content(canonical_content)
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
                            render_detached=detached_at_save,
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
                        rows, _ = final_orders_store.replace_final_orders(
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


@app.get("/sim-ready/cases", response_model=list[CaseListItemResponse])
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


@app.get("/sim-ready/case/{case_id}/analysis", response_model=CaseAnalysisResponse)
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


@app.get(
    "/sim-ready/case/{case_id}/structured", response_model=StructuredRecordResponse
)
async def get_sim_ready_case_structured(case_id: int):
    """The canonical structured record for a case's latest version (ADR-002).

    ADR-002 makes this record the source of truth and `case_details.content` its
    projection, but nothing could read it back out: it was written on save and only ever
    consumed in-process. An editor cannot edit fields it cannot fetch, which is why
    editing markdown and re-reading it with a model call was the only path available.

    `parity_broken` is returned alongside rather than left for the caller to derive.
    An editor has to show that state continuously, because the way an author discovers
    it today is the Oracle refusing to run (ADR-017), long after the edit that caused it.
    """
    if not AUTHORING_ENABLED:
        raise HTTPException(status_code=503, detail="Authoring schema is not available")

    sim_db = next(get_sim_ready_db())
    try:
        version = final_orders_store.latest_version_for_case_detail(sim_db, case_id)
        if version is None:
            raise HTTPException(
                status_code=404,
                detail="This case has no authoring record, so it has no structured "
                "record to edit. Adopt it first: POST /sim-ready/case/"
                f"{case_id}/adopt",
            )
        parity = oracle_service.check_content_parity(sim_db, version)
        snapshot = snapshot_version(version)
    finally:
        sim_db.close()

    return {
        "case_id": case_id,
        "case_version_id": snapshot["version_id"],
        "case_family_id": snapshot["family_id"],
        "version": snapshot["version"],
        "title": snapshot["title"],
        "description": snapshot["description"],
        "primary_diagnosis": snapshot["primary_diagnosis"],
        "oracle_specialty": snapshot["oracle_specialty"],
        "content_structured": snapshot["content_structured"],
        "content_rendered": snapshot["content_rendered"],
        "render_detached": snapshot["render_detached"],
        "parity_broken": not parity["in_parity"],
        "parity_reason": parity["reason"],
        "parity_message": parity["message"],
    }


@app.get("/auth/check", response_model=AuthCheckResponse)
async def check_auth(username: str = Depends(verify_credentials_silent)):
    """Validate a credential without performing any action. The SPA's login (ADR-021).

    The SPA holds HTTP Basic credentials and sends them on every authenticated call, so
    it needs somewhere cheap to find out whether they are right *before* an author starts
    editing. Discovering a bad password by having a save fail is the wrong moment.

    Uses `verify_credentials_silent`, so a wrong password returns a plain 401 the login
    form can render instead of a browser-native credential dialog the app cannot control.

    Reads nothing and writes nothing; the credential itself is the entire input.
    """
    return AuthCheckResponse(authenticated=True, username=username)


@app.post(
    "/sim-ready/render-preview",
    response_model=SimReadyRenderPreviewResponse,
)
async def render_structured_preview(request: SimReadyRenderPreviewRequest):
    """Render a structured record to markdown. Writes nothing, costs no model call.

    The editor's preview pane. Server-side so exactly one renderer stays authoritative —
    the same `render_sim_ready_content()` a save calls, so what an author previews is
    byte-for-byte what `PUT .../structured` would store. A client-side reimplementation
    would be a second renderer that drifts, which is not a hypothetical: the SPA's mirror
    of the *stem* renderer had already drifted before `POST /oracle/render-items`
    replaced it (ADR-020).

    Unauthenticated, matching `/oracle/render-items`: it touches no database, reads no
    case, and returns only a transformation of what the caller already sent.

    Not case-scoped on purpose. The preview must reflect the author's unsaved buffer, not
    the stored record, so there is nothing to look up and no case id to take.
    """
    structured = request.content_structured.model_dump()
    try:
        rendered = render_sim_ready_content(structured)
    except (KeyError, TypeError) as e:
        # Same 422 the save path gives, from the same renderer, so a record that cannot
        # render fails in the preview rather than at save time.
        raise HTTPException(
            status_code=422,
            detail=f"The structured record could not be rendered: {e}",
        ) from e

    return SimReadyRenderPreviewResponse(
        content_rendered=rendered,
        door_chart_delimiter_present=DOOR_CHART_DELIMITER in rendered,
        character_count=len(rendered),
    )


@app.put("/sim-ready/case/{case_id}/structured")
async def update_sim_ready_case_structured(
    case_id: int,
    update: SimReadyStructuredUpdateRequest,
    credentials: str = Depends(verify_credentials),
):
    """Save structured field edits; the renderer writes the markdown (ADR-002).

    The inversion. `PUT /sim-ready/case/{id}` takes markdown, writes it to the simulator
    row, and then pays for a model call to read it back into the structured record, which
    can fail and leave the version detached. This path has no such gap: the structured
    record arrives as the input, the renderer derives the markdown from it, and the two
    are written together from the same source in the same transaction. Parity is restored
    by construction, including for a case that was previously detached.

    Always a new version. There is no `in_place` mode here on purpose: in-place exists for
    corrections an author does not want recorded, and a structured edit is by definition a
    change to the canonical record (ADR-003, ADR-019).
    """
    if not AUTHORING_ENABLED:
        raise HTTPException(status_code=503, detail="Authoring schema is not available")

    structured = update.content_structured.model_dump()
    try:
        rendered = render_sim_ready_content(structured)
    except (KeyError, TypeError) as e:
        # A renderer failure must not reach the database. Rendering before any write is
        # what keeps the simulator row and the structured record from disagreeing.
        raise HTTPException(
            status_code=422,
            detail=f"The structured record could not be rendered: {e}",
        ) from e

    sim_db = next(get_sim_ready_db())
    try:
        case = (
            sim_db.query(CaseDetailSimReady)
            .filter(CaseDetailSimReady.id == case_id)
            .first()
        )
        if not case:
            raise HTTPException(status_code=404, detail="Sim-ready case not found")

        version = final_orders_store.latest_version_for_case_detail(sim_db, case_id)
        if version is None:
            raise HTTPException(
                status_code=404,
                detail="This case has no authoring record to version. Adopt it first: "
                f"POST /sim-ready/case/{case_id}/adopt",
            )

        snapshot = snapshot_version(version)
        previous_orders = (
            [
                final_orders_store.serialize_final_order(o)
                for o in final_orders_store.load_final_orders(sim_db, version.id)
            ]
            if FINAL_ORDERS_ENABLED
            else []
        )

        title = update.saved_name or snapshot["title"]
        case.saved_name = title
        case.content = rendered
        if update.custom_input is not None:
            case.custom_input = update.custom_input
        if update.custom_evaluation is not None:
            case.custom_evaluation = update.custom_evaluation
        if update.allow_orders is not None:
            case.allow_orders = update.allow_orders
        if update.learner_tasks is not None:
            case.learner_tasks = update.learner_tasks

        new_version = persist_case_version(
            sim_db,
            title=title,
            description=(
                update.description
                if update.description is not None
                else snapshot["description"]
            ),
            primary_diagnosis=(
                update.primary_diagnosis
                if update.primary_diagnosis is not None
                else snapshot["primary_diagnosis"]
            ),
            case_details=structured,
            diagnostic_framework=snapshot["diagnostic_framework"],
            feature_likelihood_ratios=snapshot["feature_likelihood_ratios"],
            output_format="sim_ready",
            rendered_content=rendered,
            # False by construction: the markdown was just produced from this record.
            render_detached=False,
            case_detail_id=case_id,
            family_id=snapshot["family_id"],
            parent_version_id=snapshot["version_id"],
            oracle_specialty=(
                update.oracle_specialty
                if update.oracle_specialty is not None
                else snapshot["oracle_specialty"]
            ),
        )

        carried = 0
        if previous_orders:
            # New version, so there is nothing to reconcile against and nothing can
            # detach; the second element is always empty here.
            carried_rows, _ = final_orders_store.replace_final_orders(
                sim_db, new_version.id, previous_orders
            )
            carried = len(carried_rows)

        sim_db.refresh(case)
        payload = _sim_case_payload(case)
        logger.info(
            "Case %d saved from structured fields as version %d (v%d) by %s: "
            "parent=%d, %d Final Order(s) carried",
            case_id,
            new_version.id,
            new_version.version,
            credentials,
            snapshot["version_id"],
            carried,
        )
        return {
            **payload,
            "save_mode": "new_version",
            "case_version_id": new_version.id,
            "version": new_version.version,
            "parent_version_id": snapshot["version_id"],
            "render_detached": False,
            "parity_broken": False,
            "final_orders_carried_forward": carried,
        }
    except HTTPException:
        sim_db.rollback()
        raise
    except Exception as e:
        sim_db.rollback()
        logger.exception("Structured save failed for case %d", case_id)
        raise HTTPException(
            status_code=500, detail=f"The structured save did not complete: {e}"
        ) from e
    finally:
        sim_db.close()


@app.get("/sim-ready/case/{case_id}", response_model=SimReadyCaseDetailResponse)
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


def _apply_case_fields(case: CaseDetailSimReady, update) -> None:
    """Copy the set fields of an update/copy request onto a simulator case row."""
    for field in (
        "saved_name",
        "content",
        "custom_input",
        "custom_evaluation",
        "allow_orders",
        "learner_tasks",
    ):
        value = getattr(update, field, None)
        if value is not None:
            setattr(case, field, value)


def _sim_case_payload(case: CaseDetailSimReady) -> dict:
    return {
        "case_id": case.id,
        "saved_name": case.saved_name,
        "content": case.content,
        "custom_input": case.custom_input,
        "custom_evaluation": case.custom_evaluation,
        "allow_orders": case.allow_orders,
        "learner_tasks": case.learner_tasks,
    }


async def _structured_for_new_version(
    snapshot: dict, content: str, resync_requested: bool | None
) -> tuple[dict, bool, bool]:
    """The structured record a successor version should carry, and whether it is attached.

    Returns `(content_structured, render_detached, resynced)`.

    The default is to re-read the markdown only when the markdown actually changed. An
    author who edited the learner tasks or the Final Orders and nothing else should not
    pay for a model call, and an author who edited the case document must, because
    otherwise the structured record describes the previous case and the Oracle's parity
    check blocks the panel (ADR-017).

    A failed extraction does not fail the save. The case content is already committed at
    this point; the version is written with the parent's structured record and marked
    detached, which is a true statement about that version and leaves the author with the
    'Re-read case content' path.
    """
    content_changed = oracle_service.normalize_content(
        content
    ) != oracle_service.normalize_content(snapshot["content_rendered"])

    should_resync = content_changed if resync_requested is None else resync_requested
    if not should_resync or not content.strip():
        # Content that changed without a re-read is by definition no longer a projection
        # of the structured record. Content that did not change keeps the parent's state.
        detached = True if content_changed else snapshot["render_detached"]
        return snapshot["content_structured"], detached, False

    try:
        structured = await llm_service.extract_structured_from_content_async(
            content, snapshot["primary_diagnosis"]
        )
    except Exception:
        logger.exception(
            "Could not re-read content into the structured record for version %s; "
            "writing the successor with the parent's record, marked detached",
            snapshot["version_id"],
        )
        return snapshot["content_structured"], True, False

    return structured.model_dump(), False, True


@app.put("/sim-ready/case/{case_id}")
async def update_sim_ready_case(
    case_id: int,
    update: SimReadyCaseUpdateRequest,
    credentials: str = Depends(verify_credentials),
):
    """Save an edited case, as a new version by default.

    Until 2026-07-30 this only ever overwrote the row, which contradicted ADR-003 and had
    two consequences an author could not see: an edit left no record that it happened, so
    learner runs from before and after it were pooled; and the structured record kept
    describing the pre-edit case, which blocked the Oracle with no obvious way forward.

    `save_mode="in_place"` keeps the old behaviour for corrections, and says so.
    """
    sim_db = next(get_sim_ready_db())
    try:
        case = (
            sim_db.query(CaseDetailSimReady)
            .filter(CaseDetailSimReady.id == case_id)
            .first()
        )
        if not case:
            raise HTTPException(status_code=404, detail="Sim-ready case not found")

        _apply_case_fields(case, update)
        sim_db.commit()
        sim_db.refresh(case)
        payload = _sim_case_payload(case)
        live_content = case.content or ""

        version = (
            final_orders_store.latest_version_for_case_detail(sim_db, case_id)
            if AUTHORING_ENABLED
            else None
        )
        if version is None:
            # No authoring record to version. The content save stands; adoption is a
            # separate, explicit step because it needs the primary diagnosis.
            return {
                **payload,
                "save_mode": "in_place",
                "case_version_id": None,
                "note": (
                    "Saved. This case has no authoring record, so no version was "
                    "written and it cannot carry Final Orders or an Oracle panel. "
                    "Adopt it first."
                    if AUTHORING_ENABLED
                    else "Saved. Authoring persistence is unavailable on this backend."
                ),
            }

        if update.save_mode == "in_place":
            drifted = oracle_service.normalize_content(
                live_content
            ) != oracle_service.normalize_content(version.content_rendered)
            logger.info(
                "Case %d updated in place by %s (version %d unchanged, drift=%s)",
                case_id,
                credentials,
                version.id,
                drifted,
            )
            return {
                **payload,
                "save_mode": "in_place",
                "case_version_id": version.id,
                "version": version.version,
                "parity_broken": drifted,
                "note": (
                    "Saved in place. The case content now differs from version "
                    f"{version.version}'s record, so the Oracle is blocked until the "
                    "content is re-read."
                    if drifted
                    else "Saved in place. No new version was written."
                ),
            }

        snapshot = snapshot_version(version)
        previous_orders = (
            [
                final_orders_store.serialize_final_order(o)
                for o in final_orders_store.load_final_orders(sim_db, version.id)
            ]
            if FINAL_ORDERS_ENABLED
            else []
        )
    finally:
        sim_db.close()

    structured, detached, resynced = await _structured_for_new_version(
        snapshot, live_content, update.resync_structured
    )

    sim_db = next(get_sim_ready_db())
    try:
        new_version = persist_case_version(
            sim_db,
            title=payload["saved_name"] or snapshot["title"],
            description=snapshot["description"],
            primary_diagnosis=snapshot["primary_diagnosis"],
            case_details=structured,
            diagnostic_framework=snapshot["diagnostic_framework"],
            feature_likelihood_ratios=snapshot["feature_likelihood_ratios"],
            output_format="sim_ready",
            rendered_content=live_content,
            render_detached=detached,
            case_detail_id=case_id,
            family_id=snapshot["family_id"],
            parent_version_id=snapshot["version_id"],
            oracle_specialty=snapshot["oracle_specialty"],
        )
        carried = 0
        if previous_orders:
            # Carried so the orders survive even if the caller never follows up with a
            # Final Orders write. A subsequent PUT replaces them on this same version.
            # New version, so there is nothing to reconcile against and nothing can
            # detach; the second element is always empty here.
            carried_rows, _ = final_orders_store.replace_final_orders(
                sim_db, new_version.id, previous_orders
            )
            carried = len(carried_rows)

        logger.info(
            "Case %d saved as version %d (v%d) by %s: parent=%d, resynced=%s, "
            "detached=%s, %d Final Order(s) carried",
            case_id,
            new_version.id,
            new_version.version,
            credentials,
            snapshot["version_id"],
            resynced,
            detached,
            carried,
        )
        return {
            **payload,
            "save_mode": "new_version",
            "case_version_id": new_version.id,
            "version": new_version.version,
            "parent_version_id": snapshot["version_id"],
            "structured_resynced": resynced,
            "render_detached": detached,
            "final_orders_carried_forward": carried,
            "parity_broken": detached,
        }
    except Exception as e:
        sim_db.rollback()
        logger.exception(
            "Case %d content saved but the new version could not be written", case_id
        )
        raise HTTPException(
            status_code=500,
            detail=f"The case content was saved, but the new version was not: {e}",
        ) from e
    finally:
        sim_db.close()


@app.post("/sim-ready/case/{case_id}/copy")
async def copy_sim_ready_case(
    case_id: int,
    request: SimReadyCaseCopyRequest,
    credentials: str = Depends(verify_credentials),
):
    """Fork an edited case into a new simulator row and a new case family.

    Distinct from saving a new version: a version is the same case concept edited, and
    learner performance across its versions is comparable in a way performance across a
    fork is not. Lineage is still recorded via `parent_version_id`, across families.
    """
    sim_db = next(get_sim_ready_db())
    try:
        source = (
            sim_db.query(CaseDetailSimReady)
            .filter(CaseDetailSimReady.id == case_id)
            .first()
        )
        if not source:
            raise HTTPException(status_code=404, detail="Sim-ready case not found")

        new_case = CaseDetailSimReady(
            saved_name=request.saved_name,
            content=source.content,
            custom_input=source.custom_input,
            custom_evaluation=source.custom_evaluation,
            allow_orders=source.allow_orders,
            learner_tasks=source.learner_tasks,
        )
        # The editor's unsaved state wins over the source row: the author is forking what
        # is on their screen, not what is in the database.
        _apply_case_fields(new_case, request)
        new_case.saved_name = request.saved_name

        sim_db.add(new_case)
        sim_db.commit()
        sim_db.refresh(new_case)
        payload = _sim_case_payload(new_case)
        new_case_id = new_case.id
        live_content = new_case.content or ""

        version = (
            final_orders_store.latest_version_for_case_detail(sim_db, case_id)
            if AUTHORING_ENABLED
            else None
        )
        if version is None:
            return {
                **payload,
                "case_version_id": None,
                "note": (
                    "Copied. The source case has no authoring record, so the copy has "
                    "none either and cannot carry Final Orders or an Oracle panel."
                ),
            }

        snapshot = snapshot_version(version)
        previous_orders = (
            [
                final_orders_store.serialize_final_order(o)
                for o in final_orders_store.load_final_orders(sim_db, version.id)
            ]
            if FINAL_ORDERS_ENABLED
            else []
        )
    finally:
        sim_db.close()

    structured, detached, resynced = await _structured_for_new_version(
        snapshot, live_content, request.resync_structured
    )

    sim_db = next(get_sim_ready_db())
    try:
        new_version = persist_case_version(
            sim_db,
            title=request.saved_name,
            description=snapshot["description"],
            primary_diagnosis=snapshot["primary_diagnosis"],
            case_details=structured,
            diagnostic_framework=snapshot["diagnostic_framework"],
            feature_likelihood_ratios=snapshot["feature_likelihood_ratios"],
            output_format="sim_ready",
            rendered_content=live_content,
            render_detached=detached,
            case_detail_id=new_case_id,
            # No family_id: a fork is a new case concept, so it starts its own family at
            # v1. `parent_version_id` is what records where it came from.
            family_id=None,
            parent_version_id=snapshot["version_id"],
            oracle_specialty=snapshot["oracle_specialty"],
        )
        carried = 0
        if previous_orders:
            # New version, so there is nothing to reconcile against and nothing can
            # detach; the second element is always empty here.
            carried_rows, _ = final_orders_store.replace_final_orders(
                sim_db, new_version.id, previous_orders
            )
            carried = len(carried_rows)

        logger.info(
            "Case %d copied to %d as family %d v1 by %s (parent version %d), "
            "%d Final Order(s) carried",
            case_id,
            new_case_id,
            new_version.case_family_id,
            credentials,
            snapshot["version_id"],
            carried,
        )
        return {
            **payload,
            "copied_from_case_id": case_id,
            "case_version_id": new_version.id,
            "case_family_id": new_version.case_family_id,
            "version": new_version.version,
            "parent_version_id": snapshot["version_id"],
            "structured_resynced": resynced,
            "render_detached": detached,
            "final_orders_carried_forward": carried,
        }
    except Exception as e:
        sim_db.rollback()
        logger.exception(
            "Case %d copied to %d but the authoring record was not written",
            case_id,
            new_case_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"The copy was created (ID {new_case_id}), but its authoring record "
            f"was not: {e}",
        ) from e
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
            "authoring record existed need to be adopted first: POST "
            f"/sim-ready/case/{case_id}/adopt with the case's primary diagnosis "
            "rebuilds the structured record from its markdown.",
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


@app.post("/oracle/render-items")
async def render_oracle_items(request: OracleItemPreviewRequest):
    """The exact items a learner will see, rendered from the active stem.

    An authoring UI has to show the author the sentence a learner will read, and the only
    way to do that faithfully is to render it here. The Streamlit editor rebuilt the item
    client-side from the stem's anchors and reimplemented `default_action_phrase`, which
    had already drifted: the copy added an article to labels like "A-fib protocol" that
    the real function leaves alone. The item is the measurement instrument (ADR-005), so
    there is exactly one renderer and clients call it.

    Batched because the caller renders every Final Order on the case at once, and a
    per-order round trip on each keystroke is the reason a client would reimplement this.
    """
    items = []
    for order in request.orders:
        action = (
            order.stem_action or ""
        ).strip() or oracle_stems.default_action_phrase(order.order_text)
        items.append(
            {
                "order_text": order.order_text,
                "action_phrase": action,
                "learner_item": oracle_stems.render_item(
                    action,
                    audience="learner",
                    stem_version=request.stem_version,
                    stem_template_override=order.stem_template,
                ),
            }
        )
    return {
        "stem_version": request.stem_version or oracle_stems.DEFAULT_STEM_VERSION,
        "items": items,
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


@app.put(
    "/sim-ready/case/{case_id}/final-orders",
    response_model=FinalOrdersUpdateResponse,
)
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
            rows, detached = final_orders_store.replace_final_orders(
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
            # Non-empty when the edit removed an order that had already been rated. The
            # runs still exist and remain readable via their snapshot, but they no longer
            # attach to any live order, so the UI should say so rather than let a
            # distribution quietly vanish from the case.
            "detached_panel_runs": detached,
        }
    finally:
        sim_db.close()


@app.post("/sim-ready/case/{case_id}/adopt")
async def adopt_case_into_authoring(
    case_id: int,
    request: AdoptCaseRequest,
    username: str = Depends(verify_credentials),
):
    """Give a pre-authoring-record case its first version, read from its markdown.

    Every Final Orders and Oracle path resolves through the latest `case_version` for a
    simulator case row. Cases finalized before that table existed have none, so they were
    a dead end: Final Orders could not attach, the Oracle 404'd, and `/resync` — the one
    path that rebuilds a structured record from markdown — could not help either, because
    it starts from a version there is none of.

    This is the way in. It reconstructs the structured record from the document the
    simulator already serves and writes it as v1 of a new family.

    Two things it cannot recover, both reported rather than papered over: the diagnostic
    framework and the likelihood ratios were never stored for these cases, so the version
    starts with none; and any clinical detail the markdown does not state is reconstructed
    by the model, so the case needs a read-through afterwards.
    """
    if not AUTHORING_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Authoring persistence is unavailable: the shared database is missing "
            "the authoring schema. Run 'alembic upgrade head' in the direct-sim repo.",
        )

    sim_db = next(get_sim_ready_db())
    try:
        detail = (
            sim_db.query(CaseDetailSimReady)
            .filter(CaseDetailSimReady.id == case_id)
            .first()
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="Sim-ready case not found")

        existing = final_orders_store.latest_version_for_case_detail(sim_db, case_id)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"This case already has an authoring record (version "
                f"{existing.version}). Use 'Re-read case content' to rebuild its "
                "structured record from the current document.",
            )

        content = detail.content or ""
        if not content.strip():
            raise HTTPException(
                status_code=400, detail="This case has no content to read."
            )
        title = request.title or detail.saved_name or f"Case {case_id}"
    finally:
        sim_db.close()

    try:
        structured = await llm_service.extract_structured_from_content_async(
            content, request.primary_diagnosis
        )
    except Exception as e:
        logger.exception("Adoption failed for case %d", case_id)
        raise HTTPException(
            status_code=500, detail=f"Could not read the case content: {e}"
        ) from e

    sim_db = next(get_sim_ready_db())
    try:
        version = persist_case_version(
            sim_db,
            title=title,
            description=request.description or "",
            primary_diagnosis=request.primary_diagnosis,
            case_details=structured.model_dump(),
            # Empty, and empty is the honest value: this analysis was generated and
            # discarded for every case finalized before ADR-001, so there is nothing to
            # recover. Regenerating it here would invent numbers and present them as the
            # case's own.
            diagnostic_framework=[],
            feature_likelihood_ratios=[],
            output_format="sim_ready",
            rendered_content=content,
            # The structured record was just built from this exact document, so they are
            # in parity and the Oracle can run.
            render_detached=False,
            case_detail_id=case_id,
            oracle_specialty=request.oracle_specialty,
        )
        logger.info(
            "Case %d adopted by %s as family %d v%d (case_version=%d)",
            case_id,
            username,
            version.case_family_id,
            version.version,
            version.id,
        )
        return {
            "case_id": case_id,
            "case_version_id": version.id,
            "case_family_id": version.case_family_id,
            "version": version.version,
            "analysis_available": False,
            "note": (
                "The structured record was rebuilt from this case's markdown. The "
                "diagnostic framework and likelihood ratios were never stored for cases "
                "of this vintage, so this version has none. Any detail the document does "
                "not state was reconstructed — review the case before running the panel."
            ),
        }
    finally:
        sim_db.close()


@app.post("/sim-ready/case/{case_id}/resync")
async def resync_case_structured(
    case_id: int, username: str = Depends(verify_credentials)
):
    """Rebuild the structured record from the case's current markdown, as a new version.

    Editing case content leaves the structured record behind, which blocks the Oracle
    because its blinded view is built from that record (ADR-017). This is the supported
    way back: re-read the document the simulator now serves, write it as a new version
    with lineage, and carry the framework, likelihood ratios, and Final Orders forward.

    Deliberately author-initiated. It spends a model call and reconstructs any field the
    markdown does not state, so it is not something to do silently on every save.
    """
    _require_final_orders()
    sim_db = next(get_sim_ready_db())
    try:
        version = _resolve_case_version(sim_db, case_id)
        detail = (
            sim_db.query(CaseDetailSimReady)
            .filter(CaseDetailSimReady.id == case_id)
            .first()
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="Sim-ready case not found")

        content = detail.content or ""
        if not content.strip():
            raise HTTPException(
                status_code=400, detail="This case has no content to re-read."
            )

        previous_orders = [
            final_orders_store.serialize_final_order(o)
            for o in final_orders_store.load_final_orders(sim_db, version.id)
        ]
        family_id = version.case_family_id
        parent_version_id = version.id
        primary_diagnosis = version.primary_diagnosis or ""
        description = version.description or ""
        title = version.title or detail.saved_name
        specialty = version.oracle_specialty
        # Read the analysis off the current version so it carries forward rather than
        # being regenerated: the framework and LRs describe the same case.
        framework = [
            {
                "tier_level": f.tier_level,
                "buckets": f.diagnostic_buckets,
                "a_priori_probabilities": f.a_priori_probabilities,
            }
            for f in version.frameworks
        ]
        lrs = [
            {
                "feature_name": lr.feature_name,
                "feature_category": lr.feature_category,
                "diagnostic_bucket": lr.diagnostic_bucket,
                "tier_level": lr.tier_level,
                "likelihood_ratio": lr.likelihood_ratio,
            }
            for lr in version.feature_lrs
        ]
    finally:
        sim_db.close()

    try:
        structured = await llm_service.extract_structured_from_content_async(
            content, primary_diagnosis
        )
    except Exception as e:
        logger.exception("Content re-sync failed for case %d", case_id)
        raise HTTPException(
            status_code=500, detail=f"Could not re-read the case content: {e}"
        ) from e

    sim_db = next(get_sim_ready_db())
    try:
        new_version = persist_case_version(
            sim_db,
            title=title,
            description=description,
            primary_diagnosis=primary_diagnosis,
            case_details=structured.model_dump(),
            diagnostic_framework=framework,
            feature_likelihood_ratios=lrs,
            output_format="sim_ready",
            rendered_content=content,
            # The structured record now describes this exact document, so the version is
            # attached again and the Oracle can run.
            render_detached=False,
            case_detail_id=case_id,
            family_id=family_id,
            parent_version_id=parent_version_id,
            oracle_specialty=specialty,
        )
        if previous_orders:
            final_orders_store.replace_final_orders(
                sim_db, new_version.id, previous_orders
            )  # new version: nothing to detach

        logger.info(
            "Re-synced case %d: version %d -> %d (v%d), %d Final Order(s) carried forward",
            case_id,
            parent_version_id,
            new_version.id,
            new_version.version,
            len(previous_orders),
        )
        return {
            "case_id": case_id,
            "case_version_id": new_version.id,
            "version": new_version.version,
            "parent_version_id": parent_version_id,
            "final_orders_carried_forward": len(previous_orders),
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


# ---------------------------------------------------------------------------
# Serve the React SPA (ADR-020). Registered last, and namespaced under /app.
#
# NOT mounted at "/". `GET /` returns the build stamp, and that endpoint is how every
# deploy is verified — ADR-012 exists because a mutable image tag let a four-month-old
# image serve behind green deploys for four months, and the build stamp is the check that
# would have caught it. Shadowing it with index.html to save a path segment would remove
# the only signal that says whether a deploy actually rolled.
#
# The /app prefix also means this cannot shadow an API route, so adding it carries no risk
# to anything the Streamlit UI or the simulator already calls. Streamlit stays live and
# untouched on its own container app; the two UIs coexist until Phase 4e retires it.
# ---------------------------------------------------------------------------

_WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


@app.get("/app", include_in_schema=False)
@app.get("/app/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str = ""):
    """Serve the built SPA, falling back to index.html for client-side routes."""
    index = _WEB_DIST / "index.html"

    if full_path:
        candidate = (_WEB_DIST / full_path).resolve()
        # Containment check before touching the filesystem. Without it, a request for
        # `/app/../../etc/passwd` resolves outside the dist directory and FileResponse
        # would happily serve it. `is_relative_to` is the whole guard.
        if candidate.is_relative_to(_WEB_DIST.resolve()) and candidate.is_file():
            return FileResponse(candidate)

    if index.is_file():
        # Any unmatched path returns the SPA shell so react-router can handle it. A 200
        # here is correct for a client-side route and wrong for a genuinely missing
        # asset, which is why the file check above runs first.
        return FileResponse(index)

    # The image was built without the SPA. Say so plainly rather than 404 -- a bare 404
    # reads as "wrong URL" when the actual cause is a build that skipped the node stage.
    raise HTTPException(
        status_code=503,
        detail="The web UI is not present in this image. It is built by the node stage "
        "in Dockerfile.backend; a backend started from source needs `cd web && npm run "
        "build` first, or use the Vite dev server on :5174.",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
