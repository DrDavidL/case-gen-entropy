"""Persistence for Final Orders and LLM panel runs (ADR-004, ADR-006, ADR-014).

Final Orders are version-pinned. The simulator, which knows only a `case_details.id`,
resolves them through the latest `case_versions` row for that id — see
`load_final_orders_for_case_detail`. That indirection is deliberate: denormalising
`case_detail_id` onto `case_final_orders` would make "which orders belong to this case"
ambiguous the moment a case has more than one version.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.models.database import (
    CaseFinalOrder,
    CaseVersion,
    PanelRating,
    PanelRun,
)

logger = logging.getLogger(__name__)

MAX_FINAL_ORDERS = 5


def suppression_terms(order: CaseFinalOrder) -> list[str]:
    """Everything the simulator should treat as this order: the label plus its synonyms."""
    terms = [order.order_text]
    synonyms = order.suppression_synonyms
    if isinstance(synonyms, list):
        terms.extend(str(s) for s in synonyms if s)
    return [t.strip() for t in terms if t and str(t).strip()]


def serialize_final_order(order: CaseFinalOrder) -> dict[str, Any]:
    return {
        "id": order.id,
        "case_version_id": order.case_version_id,
        "display_order": order.display_order,
        "order_text": order.order_text,
        "stem_action": order.stem_action,
        "stem_template": order.stem_template,
        "provenance": order.provenance,
        "suppress_results": order.suppress_results,
        "suppression_message": order.suppression_message,
        "suppression_synonyms": order.suppression_synonyms or [],
    }


def replace_final_orders(
    db: Session,
    case_version_id: int,
    orders: list[dict[str, Any]],
) -> list[CaseFinalOrder]:
    """Set the Final Orders for a case version, replacing any that exist. Commits.

    Replace rather than merge: the author's list is the authoritative statement of what
    the case has, and a merge would leave a deleted order silently attached. The cap is
    re-checked here as well as in the request schema, because this is the last point
    before the write and the Oracle cost bound (5 x 15 calls) depends on it.
    """
    if len(orders) > MAX_FINAL_ORDERS:
        raise ValueError(
            f"A case may have at most {MAX_FINAL_ORDERS} Final Orders; got {len(orders)}"
        )

    existing = (
        db.query(CaseFinalOrder)
        .filter(CaseFinalOrder.case_version_id == case_version_id)
        .all()
    )
    for row in existing:
        db.delete(row)
    db.flush()

    created: list[CaseFinalOrder] = []
    for position, order in enumerate(orders, start=1):
        text = (order.get("order_text") or "").strip()
        if not text:
            continue
        synonyms = [
            s.strip()
            for s in (order.get("suppression_synonyms") or [])
            if s and str(s).strip()
        ]
        row = CaseFinalOrder(
            case_version_id=case_version_id,
            display_order=order.get("display_order") or position,
            order_text=text,
            stem_action=((order.get("stem_action") or "").strip() or None),
            stem_template=(order.get("stem_template") or None),
            provenance=order.get("provenance") or "author_entered",
            suppress_results=(
                True
                if order.get("suppress_results") is None
                else bool(order["suppress_results"])
            ),
            suppression_message=(order.get("suppression_message") or "Result pending"),
            suppression_synonyms=synonyms,
        )
        db.add(row)
        created.append(row)

    db.commit()
    for row in created:
        db.refresh(row)
    logger.info(
        "Final Orders written: case_version=%d, %d order(s)",
        case_version_id,
        len(created),
    )
    return created


def load_final_orders(db: Session, case_version_id: int) -> list[CaseFinalOrder]:
    return (
        db.query(CaseFinalOrder)
        .filter(CaseFinalOrder.case_version_id == case_version_id)
        .order_by(CaseFinalOrder.display_order, CaseFinalOrder.id)
        .all()
    )


def latest_version_for_case_detail(
    db: Session, case_detail_id: int
) -> CaseVersion | None:
    """The most recently written case version behind a simulator-facing case row.

    Ordered by id, not `version`: `version` is monotonic only within a family, so
    ordering by it across families could surface an older record with a higher number.
    """
    return (
        db.query(CaseVersion)
        .filter(CaseVersion.case_detail_id == case_detail_id)
        .order_by(CaseVersion.id.desc())
        .first()
    )


def load_final_orders_for_case_detail(
    db: Session, case_detail_id: int
) -> tuple[CaseVersion | None, list[CaseFinalOrder]]:
    version = latest_version_for_case_detail(db, case_detail_id)
    if version is None:
        return None, []
    return version, load_final_orders(db, version.id)


# --- Panel runs -----------------------------------------------------------------


def create_panel_run(
    db: Session,
    *,
    item_type: str,
    item_ref_id: int,
    case_version_id: int,
    panel_size_requested: int,
    model: str,
    reasoning_effort: str,
    provider: str,
    api_surface: str,
    prompt_template_version: str,
    stem_version: str,
    panel_roster_version: str,
    roster_specialty: str | None,
    blinded_context_hash: str,
    claim_hash: str,
    leak_override_reason: str | None = None,
) -> PanelRun:
    """Open a run in `pending`. Commits so a status endpoint can see it immediately."""
    run = PanelRun(
        item_type=item_type,
        item_ref_id=item_ref_id,
        case_version_id=case_version_id,
        panel_size_requested=panel_size_requested,
        panel_size_realized=0,
        model=model,
        reasoning_effort=reasoning_effort,
        provider=provider,
        api_surface=api_surface,
        prompt_template_version=prompt_template_version,
        stem_version=stem_version,
        panel_roster_version=panel_roster_version,
        roster_specialty=roster_specialty,
        blinded_context_hash=blinded_context_hash,
        claim_hash=claim_hash,
        leak_override_reason=leak_override_reason,
        status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def mark_run_running(db: Session, run_id: int) -> None:
    run = db.get(PanelRun, run_id)
    if run is not None:
        run.status = "running"
        db.commit()


def record_ratings(
    db: Session,
    run_id: int,
    results: list[dict[str, Any]],
) -> int:
    """Write one row per panelist and return the count with status 'ok'.

    Every panelist gets a row, including failures. A missing row and a failed row are
    different facts, and only the second one is true.
    """
    realized = 0
    for result in results:
        status = result.get("status") or "ok"
        if status == "ok":
            realized += 1
        db.add(
            PanelRating(
                run_id=run_id,
                panelist_index=result["panelist_index"],
                persona_id=result.get("persona_id"),
                persona_hash=result.get("persona_hash"),
                value=result.get("value"),
                rationale=result.get("rationale"),
                top_concerns=result.get("top_concerns"),
                status=status,
                error=result.get("error"),
                raw_response_id=result.get("raw_response_id"),
                latency_ms=result.get("latency_ms"),
                tokens_in=result.get("tokens_in"),
                tokens_out=result.get("tokens_out"),
            )
        )
    db.commit()
    return realized


def complete_run(
    db: Session,
    run_id: int,
    *,
    panel_size_realized: int,
    aggregates: dict[str, Any],
) -> None:
    run = db.get(PanelRun, run_id)
    if run is None:
        return
    run.panel_size_realized = panel_size_realized
    run.aggregates = aggregates
    # A run where nobody answered is a failure, not a complete run with an empty
    # distribution. Reporting it as complete is how an empty result gets treated as data.
    run.status = "complete" if panel_size_realized > 0 else "failed"
    if panel_size_realized == 0:
        run.error = "no panelist returned a usable rating"
    run.completed_at = datetime.utcnow()  # noqa: DTZ003 — column is naive, matches the rest
    db.commit()


def fail_run(db: Session, run_id: int, error: str) -> None:
    run = db.get(PanelRun, run_id)
    if run is None:
        return
    run.status = "failed"
    run.error = error[:1000]
    run.completed_at = datetime.utcnow()  # noqa: DTZ003
    db.commit()


def supersede_run(db: Session, old_run_id: int, new_run_id: int) -> None:
    """Point an old run at its replacement. Runs are append-only; nothing is deleted."""
    run = db.get(PanelRun, old_run_id)
    if run is not None:
        run.superseded_by = new_run_id
        db.commit()


def latest_run(db: Session, item_type: str, item_ref_id: int) -> PanelRun | None:
    """The current run for an item — the newest one nothing supersedes."""
    return (
        db.query(PanelRun)
        .filter(
            PanelRun.item_type == item_type,
            PanelRun.item_ref_id == item_ref_id,
            PanelRun.superseded_by.is_(None),
        )
        .order_by(PanelRun.id.desc())
        .first()
    )


def serialize_run(run: PanelRun, *, include_ratings: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run.id,
        "item_type": run.item_type,
        "item_ref_id": run.item_ref_id,
        "case_version_id": run.case_version_id,
        "status": run.status,
        "error": run.error,
        "panel_size_requested": run.panel_size_requested,
        "panel_size_realized": run.panel_size_realized,
        "model": run.model,
        "reasoning_effort": run.reasoning_effort,
        "provider": run.provider,
        "api_surface": run.api_surface,
        "prompt_template_version": run.prompt_template_version,
        "stem_version": run.stem_version,
        "panel_roster_version": run.panel_roster_version,
        "roster_specialty": run.roster_specialty,
        "blinded_context_hash": run.blinded_context_hash,
        "claim_hash": run.claim_hash,
        "leak_override_reason": run.leak_override_reason,
        "superseded_by": run.superseded_by,
        "aggregates": run.aggregates,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
    if include_ratings:
        payload["ratings"] = [
            {
                "panelist_index": r.panelist_index,
                "persona_id": r.persona_id,
                "persona_hash": r.persona_hash,
                "value": r.value,
                "rationale": r.rationale,
                "top_concerns": r.top_concerns,
                "status": r.status,
                "error": r.error,
                "latency_ms": r.latency_ms,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
            }
            for r in run.ratings
        ]
    return payload
