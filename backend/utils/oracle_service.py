"""Oracle orchestration: blinded context -> leak audit -> panel -> distribution.

Runs as a background job. A full panel is 75 calls and takes 3-5 minutes at concurrency
8, which exceeds both typical HTTP timeouts and Azure Container Apps ingress timeouts, so
finalization returns immediately with runs marked `pending` and a status endpoint reports
progress (`docs/llm-panels.md` §9).

**A case with no Final Orders gets no Oracle panel.** That is Cory's explicit condition
from the 2026-07-29 review, not an optimisation: no Final Order means no script
concordance item, so there is nothing for a reference distribution to describe
(ADR-014).
"""

import asyncio
import logging
from typing import Any

from sqlalchemy.orm import Session

from backend.models.database import CaseDetailSimReady, SimReadySessionLocal
from backend.utils import final_orders_store as store
from backend.utils import oracle_stems, panel_roster, panel_runner
from backend.utils.blinded_context import audit_leak, build_oracle_context
from backend.utils.panel_aggregate import aggregate_oracle

logger = logging.getLogger(__name__)

ITEM_TYPE = "final_order_appropriateness"


def _action_for(order: Any) -> str:
    """The gerund phrase for the stem, preferring the authored one."""
    stored = (getattr(order, "stem_action", None) or "").strip()
    return stored or oracle_stems.default_action_phrase(order.order_text)


def _all_suppression_terms(orders: list[Any]) -> list[str]:
    terms: list[str] = []
    for order in orders:
        terms.extend(store.suppression_terms(order))
    return terms


def _normalize_content(text: str | None) -> str:
    """Reduce markdown to its words, so only a real content edit counts as a change.

    The editor splits the document on the Door Chart delimiter and rejoins the halves
    with a fixed blank line, so blank-line placement and trailing spaces shift on a save
    that changed nothing. Blank lines and indentation also cannot affect the Oracle,
    which reads structured fields rather than this markdown.

    So the comparison is whitespace-insensitive: any change to a word is caught, any
    change to only layout is not. A check that fires when nothing changed is one people
    learn to route around, and this check blocks a panel.
    """
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def check_content_parity(db: Session, version: Any) -> dict[str, Any]:
    """Is the Oracle about to rate the case the learner will actually see?

    The two sides come from different columns. The panel's blinded context is built from
    `case_versions.content_structured`; the simulator serves `case_details.content`.
    Those agree when a case is finalized and diverge afterwards in two ways:

    1. **`render_detached`** — the author hand-edited the markdown at finalize, so the
       rendered document is no longer a projection of the structured record (ADR-002).
    2. **In-place edits** — `PUT /sim-ready/case/{id}` writes `case_details.content`
       without creating a new `case_version`, leaving `content_structured` behind. This
       is the known versioning gap in `ToDos.md` Phase 1.

    Either one means the panel would rate a case that no longer exists, producing a
    distribution that looks valid and describes the wrong thing. Blocking is the point:
    a stale distribution is worse than a missing one, because it will be used.
    """
    detail = (
        db.query(CaseDetailSimReady)
        .filter(CaseDetailSimReady.id == version.case_detail_id)
        .first()
        if version.case_detail_id
        else None
    )

    if detail is None:
        return {
            "in_parity": False,
            "reason": "no_simulator_row",
            "message": (
                "This case version is not linked to a row the simulator serves, so there "
                "is no way to confirm the panel would rate what a learner sees."
            ),
        }

    if version.render_detached:
        return {
            "in_parity": False,
            "reason": "render_detached",
            "message": (
                "This case's markdown was hand-edited, so it is no longer generated from "
                "the structured record the Oracle reads, and the panel would rate the "
                "pre-edit case. Use 'Re-read case content' to rebuild the structured "
                "record from the edited document."
            ),
        }

    # Whitespace-normalised. The editor splits the markdown on the Door Chart delimiter
    # and recombines it on save, which can shift blank lines and trailing spaces without
    # any author touching a word. A byte comparison would block the panel on a save that
    # changed nothing, and a check that fires on non-changes is one people learn to
    # route around.
    stored = _normalize_content(version.content_rendered)
    live = _normalize_content(detail.content)
    if stored != live:
        return {
            "in_parity": False,
            "reason": "content_drift",
            "message": (
                "The case content has changed since this version was written, so the "
                "Oracle's view is stale and the panel would rate the previous wording. "
                "Use 'Re-read case content' to rebuild the structured record from the "
                "current document and create a new version."
            ),
        }

    return {"in_parity": True, "reason": None, "message": None}


def preflight(
    db: Session,
    case_version_id: int,
    *,
    stem_version: str | None = None,
) -> dict[str, Any]:
    """Everything that can be checked before spending a single model call.

    Returns the blinded context summary, the leak-audit verdict, the rendered items an
    author can read, and the provider settings. Callers must treat `leak_audit.passed`
    as blocking.
    """
    version = db.get(store.CaseVersion, case_version_id)
    if version is None:
        raise ValueError(f"case_version {case_version_id} not found")

    orders = store.load_final_orders(db, case_version_id)
    if not orders:
        return {
            "ready": False,
            "reason": "no_final_orders",
            "message": (
                "This case has no Final Orders, so there is no script concordance item "
                "and no Oracle panel to run."
            ),
        }

    parity = check_content_parity(db, version)

    case_details = version.content_structured or {}
    context = build_oracle_context(
        case_details, suppression_terms=_all_suppression_terms(orders)
    )
    audit = audit_leak(context.text, version.primary_diagnosis or "")

    resolved_stem = oracle_stems.get_stem(stem_version)
    roster = panel_roster.build_roster(version.oracle_specialty)

    items = [
        {
            "final_order_id": order.id,
            "order_text": order.order_text,
            "provenance": order.provenance,
            "oracle_item": oracle_stems.render_item(
                _action_for(order),
                audience="oracle",
                stem_version=resolved_stem.version,
                stem_template_override=order.stem_template,
            ),
            "learner_item": oracle_stems.render_item(
                _action_for(order),
                audience="learner",
                stem_version=resolved_stem.version,
                stem_template_override=order.stem_template,
            ),
        }
        for order in orders
    ]

    # Parity is checked first. A leak in a context that describes the wrong case is the
    # less interesting of the two problems.
    if not parity["in_parity"]:
        ready, reason = False, parity["reason"]
    elif not audit.passed:
        ready, reason = False, "diagnosis_leak"
    else:
        ready, reason = True, None

    return {
        "ready": ready,
        "reason": reason,
        "message": parity["message"],
        "content_parity": parity,
        "case_version_id": case_version_id,
        "primary_diagnosis_withheld": bool(version.primary_diagnosis),
        "blinded_context": context.text,
        "blinded_context_hash": context.content_hash,
        "included_sections": context.included_sections,
        "excluded_sections": context.excluded_sections,
        "suppressed_tests": context.suppressed_tests,
        "leak_audit": audit.model_dump(),
        "stem_version": resolved_stem.version,
        "stem_label": resolved_stem.label,
        "panel_roster_version": panel_roster.ROSTER_VERSION,
        "roster_specialty": version.oracle_specialty or panel_roster.DEFAULT_SPECIALTY,
        "roster": [{"index": p.index, "role": p.role} for p in roster],
        "settings": panel_runner.describe_settings(),
        "items": items,
        "estimated_calls": len(orders) * len(roster),
    }


async def run_oracle_for_case_version(
    case_version_id: int,
    *,
    stem_version: str | None = None,
    leak_override_reason: str | None = None,
) -> dict[str, Any]:
    """Run the Oracle for every Final Order on a case version.

    Opens its own database session: this executes after the HTTP response has been sent,
    so the request-scoped session is already closed.

    Orders run one after another while panelists within an order run concurrently. That
    keeps peak concurrency at the configured semaphore rather than orders x panelists,
    and it means a failure on order 3 does not discard the completed distributions for
    orders 1 and 2.
    """
    if SimReadySessionLocal is None:
        raise ValueError("POSTGRES_URL_SIM_READY is not configured")

    db: Session = SimReadySessionLocal()
    summary: dict[str, Any] = {"case_version_id": case_version_id, "runs": []}
    try:
        version = db.get(store.CaseVersion, case_version_id)
        if version is None:
            raise ValueError(f"case_version {case_version_id} not found")

        orders = store.load_final_orders(db, case_version_id)
        if not orders:
            logger.info(
                "Oracle skipped: case_version=%d has no Final Orders", case_version_id
            )
            return {**summary, "status": "skipped", "reason": "no_final_orders"}

        # Not overridable, unlike the leak audit. A leak hit can be a true match with a
        # benign explanation; content drift cannot — it means the panel would rate a case
        # that is not the one a learner will see, and no reason makes that measurement
        # valid.
        parity = check_content_parity(db, version)
        if not parity["in_parity"]:
            logger.error(
                "Oracle blocked for case_version=%d: %s",
                case_version_id,
                parity["message"],
            )
            return {
                **summary,
                "status": "blocked",
                "reason": parity["reason"],
                "message": parity["message"],
            }

        context = build_oracle_context(
            version.content_structured or {},
            suppression_terms=_all_suppression_terms(orders),
        )
        audit = audit_leak(context.text, version.primary_diagnosis or "")
        if not audit.passed and not leak_override_reason:
            # Blocking. Spending 75 calls on a context that names the diagnosis produces
            # a distribution that looks valid and measures nothing.
            logger.error(
                "Oracle blocked for case_version=%d: diagnosis leaked into the blinded "
                "context (%d term(s): %s)",
                case_version_id,
                len(audit.hits),
                ", ".join(h.term for h in audit.hits[:5]),
            )
            return {
                **summary,
                "status": "blocked",
                "reason": "diagnosis_leak",
                "leak_audit": audit.model_dump(),
            }
        if not audit.passed:
            # Logged at error level even though the author authorised it. A term the
            # audit matched is still in the context the panel will read, and whoever
            # analyses this data later needs to find that without reading the code.
            logger.error(
                "Oracle running for case_version=%d DESPITE a failed leak audit "
                "(%s). Author reason: %s",
                case_version_id,
                "; ".join(f"{h.term} in {h.section}" for h in audit.hits[:5]),
                leak_override_reason,
            )

        resolved_stem = oracle_stems.get_stem(stem_version)
        roster = panel_roster.build_roster(version.oracle_specialty)
        settings = panel_runner.describe_settings()

        for order in orders:
            rendered_item = oracle_stems.render_item(
                _action_for(order),
                audience="oracle",
                stem_version=resolved_stem.version,
                stem_template_override=order.stem_template,
            )

            previous = await asyncio.to_thread(
                store.latest_run, db, ITEM_TYPE, order.id
            )

            run = await asyncio.to_thread(
                store.create_panel_run,
                db,
                item_type=ITEM_TYPE,
                item_ref_id=order.id,
                case_version_id=case_version_id,
                panel_size_requested=len(roster),
                model=str(settings["model"]),
                reasoning_effort=str(settings["reasoning_effort"]),
                provider=str(settings["provider"]),
                api_surface=str(settings["api_surface"]),
                prompt_template_version=str(settings["prompt_template_version"]),
                stem_version=resolved_stem.version,
                panel_roster_version=panel_roster.ROSTER_VERSION,
                roster_specialty=version.oracle_specialty,
                blinded_context_hash=context.content_hash,
                claim_hash=panel_runner.claim_hash(rendered_item),
                leak_override_reason=leak_override_reason,
            )

            try:
                await asyncio.to_thread(store.mark_run_running, db, run.id)
                results = await panel_runner.run_panel(
                    roster=roster,
                    blinded_context=context.text,
                    rendered_item=rendered_item,
                )
                payload = [r.model_dump() for r in results]

                realized = await asyncio.to_thread(
                    store.record_ratings, db, run.id, payload
                )
                aggregate = aggregate_oracle(
                    payload,
                    requested_n=len(roster),
                    primary_diagnosis=version.primary_diagnosis or "",
                )
                await asyncio.to_thread(
                    store.complete_run,
                    db,
                    run.id,
                    panel_size_realized=realized,
                    aggregates=aggregate.model_dump(),
                )

                if previous is not None:
                    await asyncio.to_thread(
                        store.supersede_run, db, previous.id, run.id
                    )

                summary["runs"].append(
                    {
                        "final_order_id": order.id,
                        "order_text": order.order_text,
                        "run_id": run.id,
                        "status": "complete" if realized else "failed",
                        "realized_n": realized,
                    }
                )
                logger.info(
                    "Oracle run %d complete: order=%r realized=%d/%d",
                    run.id,
                    order.order_text,
                    realized,
                    len(roster),
                )
            except Exception as e:  # noqa: BLE001 — one order must not lose the others
                logger.exception(
                    "Oracle run %d failed for order %r", run.id, order.order_text
                )
                await asyncio.to_thread(
                    store.fail_run, db, run.id, f"{type(e).__name__}: {e}"
                )
                summary["runs"].append(
                    {
                        "final_order_id": order.id,
                        "order_text": order.order_text,
                        "run_id": run.id,
                        "status": "failed",
                        "error": str(e)[:300],
                    }
                )

        completed = sum(1 for r in summary["runs"] if r["status"] == "complete")
        summary["status"] = (
            "complete"
            if completed == len(orders)
            else ("partial" if completed else "failed")
        )
        return summary
    finally:
        db.close()


def load_oracle_for_case_version(db: Session, case_version_id: int) -> dict[str, Any]:
    """Current Oracle state for every Final Order on a case version.

    Recomputes aggregates from the stored per-rating rows rather than returning the
    convenience copy on `panel_runs.aggregates`, so a change to the scoring rule takes
    effect without regenerating any model output.
    """
    version = db.get(store.CaseVersion, case_version_id)
    if version is None:
        raise ValueError(f"case_version {case_version_id} not found")

    orders = store.load_final_orders(db, case_version_id)
    items: list[dict[str, Any]] = []

    for order in orders:
        run = store.latest_run(db, ITEM_TYPE, order.id)
        entry: dict[str, Any] = {
            "final_order": store.serialize_final_order(order),
            "run": None,
            "aggregate": None,
            # A run is stale exactly when it was generated against a different blinded
            # context than the case now has (ADR-003). Computed, not stored, so an edit
            # cannot leave a run claiming to be current.
            "stale": False,
        }
        if run is not None:
            entry["run"] = store.serialize_run(run, include_ratings=True)
            ratings = entry["run"]["ratings"]
            entry["aggregate"] = aggregate_oracle(
                ratings,
                requested_n=run.panel_size_requested,
                primary_diagnosis=version.primary_diagnosis or "",
            ).model_dump()

            current_context = build_oracle_context(
                version.content_structured or {},
                suppression_terms=_all_suppression_terms(orders),
            )
            entry["stale"] = run.blinded_context_hash != current_context.content_hash

        items.append(entry)

    return {
        "case_version_id": case_version_id,
        "primary_diagnosis": version.primary_diagnosis,
        "items": items,
    }
