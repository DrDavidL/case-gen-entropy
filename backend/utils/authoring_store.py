"""Persistence for the canonical case record (Decisions.md ADR-001, ADR-003).

Every case — regardless of output format — gets a family, a version, and its full
diagnostic framework and likelihood-ratio data stored in the shared database.

Before this existed, the sim-ready path generated the framework and LR matrix, used
them for nothing, and discarded them: they survived only in Streamlit session state
and were lost on refresh. That is the data every downstream goal depends on.
"""

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.models.database import (
    AuthoringDiagnosticFramework,
    AuthoringFeatureLikelihoodRatio,
    CaseFamily,
    CaseVersion,
)

logger = logging.getLogger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_length: int = 80) -> str:
    slug = _SLUG_STRIP.sub("-", (value or "").lower()).strip("-")
    return slug[:max_length] or "case"


def _unique_slug(db: Session, base: str) -> str:
    """Append a numeric suffix until the slug is free."""
    slug = base
    n = 2
    while db.query(CaseFamily.id).filter(CaseFamily.slug == slug).first() is not None:
        suffix = f"-{n}"
        slug = f"{base[: 80 - len(suffix)]}{suffix}"
        n += 1
    return slug


def get_or_create_family(
    db: Session, title: str, family_id: int | None = None
) -> CaseFamily:
    if family_id is not None:
        family = db.get(CaseFamily, family_id)
        if family is not None:
            return family
        logger.warning("case_family_id=%s not found; creating a new family", family_id)

    family = CaseFamily(slug=_unique_slug(db, slugify(title)), title=title)
    db.add(family)
    db.flush()
    return family


def _next_version(db: Session, case_family_id: int) -> int:
    latest = (
        db.query(CaseVersion.version)
        .filter(CaseVersion.case_family_id == case_family_id)
        .order_by(CaseVersion.version.desc())
        .first()
    )
    return (latest[0] + 1) if latest else 1


def persist_case_version(
    db: Session,
    *,
    title: str,
    description: str,
    primary_diagnosis: str,
    case_details: dict[str, Any],
    diagnostic_framework: list[dict[str, Any]],
    feature_likelihood_ratios: list[dict[str, Any]],
    output_format: str,
    rendered_content: str | None = None,
    render_detached: bool = False,
    case_detail_id: int | None = None,
    family_id: int | None = None,
    parent_version_id: int | None = None,
    oracle_specialty: str | None = None,
) -> CaseVersion:
    """Write one published case version with its full analysis. Commits."""
    family = get_or_create_family(db, title, family_id)

    version = CaseVersion(
        case_family_id=family.id,
        version=_next_version(db, family.id),
        status="published",
        title=title,
        description=description,
        primary_diagnosis=primary_diagnosis,
        content_structured=case_details,
        content_rendered=rendered_content,
        render_detached=render_detached,
        parent_version_id=parent_version_id,
        case_detail_id=case_detail_id,
        output_format=output_format,
        oracle_specialty=(oracle_specialty or None),
        # Naive on purpose: the DateTime columns are naive and every other default in
        # backend/models/database.py uses utcnow(). A tz-aware value here alone would
        # mix naive and aware datetimes in the same table.
        published_at=datetime.utcnow(),  # noqa: DTZ003
    )
    db.add(version)
    db.flush()

    for tier in diagnostic_framework or []:
        db.add(
            AuthoringDiagnosticFramework(
                case_version_id=version.id,
                tier_level=tier.get("tier_level"),
                diagnostic_buckets=tier.get("buckets"),
                a_priori_probabilities=tier.get("a_priori_probabilities"),
            )
        )

    for lr in feature_likelihood_ratios or []:
        db.add(
            AuthoringFeatureLikelihoodRatio(
                case_version_id=version.id,
                feature_name=lr.get("feature_name"),
                feature_category=lr.get("feature_category"),
                diagnostic_bucket=lr.get("diagnostic_bucket"),
                tier_level=lr.get("tier_level"),
                likelihood_ratio=lr.get("likelihood_ratio"),
                provenance="llm_generated",
            )
        )

    db.commit()
    db.refresh(version)
    return version


def snapshot_version(version: CaseVersion) -> dict[str, Any]:
    """Everything a successor version carries forward, as plain values.

    Read while the instance is live and returned detached from the session on purpose.
    Writing a new version commits, which expires every loaded instance; closing the
    session then detaches them, so a later attribute access would try to refresh a
    detached object and raise. Plain dicts are immune to both.
    """
    return {
        "version_id": version.id,
        "family_id": version.case_family_id,
        "version": version.version,
        "title": version.title,
        "description": version.description or "",
        "primary_diagnosis": version.primary_diagnosis or "",
        "content_structured": version.content_structured or {},
        "content_rendered": version.content_rendered,
        "render_detached": bool(version.render_detached),
        "oracle_specialty": version.oracle_specialty,
        "diagnostic_framework": [
            {
                "tier_level": f.tier_level,
                "buckets": f.diagnostic_buckets,
                "a_priori_probabilities": f.a_priori_probabilities,
            }
            for f in sorted(version.frameworks, key=lambda f: f.tier_level or 0)
        ],
        "feature_likelihood_ratios": [
            {
                "feature_name": lr.feature_name,
                "feature_category": lr.feature_category,
                "diagnostic_bucket": lr.diagnostic_bucket,
                "tier_level": lr.tier_level,
                "likelihood_ratio": lr.likelihood_ratio,
            }
            for lr in version.feature_lrs
        ],
    }


def load_analysis(db: Session, case_detail_id: int) -> dict[str, Any] | None:
    """Return the framework + LR data for the latest version of a sim-ready case."""
    # Order by id, not version: `version` is monotonic only within a family, so ordering
    # by it across families could return an older record with a higher version number.
    version = (
        db.query(CaseVersion)
        .filter(CaseVersion.case_detail_id == case_detail_id)
        .order_by(CaseVersion.id.desc())
        .first()
    )
    if version is None:
        return None

    return {
        "case_version_id": version.id,
        "case_family_id": version.case_family_id,
        "version": version.version,
        "primary_diagnosis": version.primary_diagnosis,
        "render_detached": version.render_detached,
        "diagnostic_framework": [
            {
                "tier_level": f.tier_level,
                "buckets": f.diagnostic_buckets,
                "a_priori_probabilities": f.a_priori_probabilities,
            }
            for f in sorted(version.frameworks, key=lambda f: f.tier_level or 0)
        ],
        "feature_likelihood_ratios": [
            {
                # Exposed so an in-place edit can address one row. Feature name plus
                # bucket is not a key -- the same feature legitimately carries a
                # different LR for each bucket it discriminates.
                "id": lr.id,
                "feature_name": lr.feature_name,
                "feature_category": lr.feature_category,
                "diagnostic_bucket": lr.diagnostic_bucket,
                "tier_level": lr.tier_level,
                "likelihood_ratio": lr.likelihood_ratio,
                "provenance": lr.provenance,
            }
            for lr in version.feature_lrs
        ],
    }


def update_analysis_in_place(
    db: Session,
    case_detail_id: int,
    *,
    likelihood_ratios: list[dict[str, Any]],
    tier_priors: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, int, int]:
    """Edit LR values and tier priors on the latest version, without versioning. Commits.

    Returns `(analysis, lrs_changed, tiers_changed)`.

    **In place on purpose** (2026-08-02, David). `ADR-003` makes versions immutable so a
    learner run stays attributable to what the learner saw — but LRs are not learner-facing.
    The learner reads `case_details.content`; the Oracle rates blinded structured fields.
    Neither consults these numbers, so a new version per LR tweak would add lineage noise
    without protecting anything. The working assumption is that the case text is settled
    before LRs are tuned.

    The residual exposure is `ADR-011`/Phase 6, where predicted entropy is compared against
    observed learner variance: editing an LR after runs exist silently changes the
    prediction. `provenance` and `updated_at` are what make that recoverable after the
    fact, which is why a changed row is stamped rather than quietly overwritten.

    Only rows already belonging to this case's latest version can be touched — an id from
    another case is ignored rather than trusted.
    """
    version = (
        db.query(CaseVersion)
        .filter(CaseVersion.case_detail_id == case_detail_id)
        .order_by(CaseVersion.id.desc())
        .first()
    )
    if version is None:
        return None, 0, 0

    by_id = {lr.id: lr for lr in version.feature_lrs}
    lrs_changed = 0
    for edit in likelihood_ratios or []:
        row = by_id.get(edit.get("id"))
        if row is None:
            continue
        new_value = edit.get("likelihood_ratio")
        if new_value is None:
            continue
        # Compare before stamping. Re-saving an untouched form must not relabel every
        # row as author_overridden -- provenance is only meaningful if it marks rows a
        # human actually changed. Same reasoning as `render_detached`.
        if float(row.likelihood_ratio or 0) != float(new_value):
            row.likelihood_ratio = float(new_value)
            row.provenance = "author_overridden"
            lrs_changed += 1

    by_tier = {f.tier_level: f for f in version.frameworks}
    tiers_changed = 0
    for edit in tier_priors or []:
        row = by_tier.get(edit.get("tier_level"))
        priors = edit.get("a_priori_probabilities")
        if row is None or not isinstance(priors, dict):
            continue
        # Keys are bucket names. Unknown ones are dropped rather than written: a bucket
        # renamed in the framework would otherwise silently accumulate an orphan prior.
        known = {
            b.get("name") for b in (row.diagnostic_buckets or []) if isinstance(b, dict)
        }
        cleaned = {k: float(v) for k, v in priors.items() if k in known}
        if cleaned != (row.a_priori_probabilities or {}):
            row.a_priori_probabilities = cleaned
            tiers_changed += 1

    if lrs_changed or tiers_changed:
        version.updated_at = datetime.utcnow()  # noqa: DTZ003 — column is naive
        db.commit()
    else:
        db.rollback()

    return load_analysis(db, case_detail_id), lrs_changed, tiers_changed
