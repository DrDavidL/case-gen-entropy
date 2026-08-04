"""Learner-facing text for Final Orders, shared across models, schemas and storage.

Deliberately has no imports and no import-time side effects. It lived in
`backend/models/database.py` for one commit, which made `editing_schemas` — a pure Pydantic
module — unimportable without `POSTGRES_URL`, because that module raises at import when the
database is not configured. A constant is not worth that coupling.

`direct-sim/backend/case_content.py` exists for the same reason on the simulator side.
"""

# What a learner reads when they order a Final Order during an encounter.
#
# Three constraints shaped it, and they pull against each other:
#
# 1. **No promise.** Reusing the simulator's ordinary delayed-result wording ("available
#    after you complete your clinical reasoning") would blend in perfectly and would be a
#    lie — a Final Order never returns a result.
# 2. **No announcement.** "No result will be returned for this study" is honest and tells
#    the learner exactly which items are being measured, minutes before they rate them.
# 3. **Plausible in world.** A resource constraint is real in an emergency department, and
#    it leaves the clinical question genuinely open, which is what the rating needs.
#
# Names neither a modality nor a result: a Final Order may be a test, a consult, or a
# treatment ("Intravenous fluid therapy" is one in production), and one sentence has to
# read correctly for all three.
DEFAULT_SUPPRESSION_MESSAGE = (
    "Order received. This is not available through the health system at this time."
)


def merge_synonyms(
    label: str, existing: list[str], proposed: list[str]
) -> tuple[list[str], list[str]]:
    """Combine an author's synonyms with the model's suggestions.

    Returns `(merged, added)`. Merge rather than replace, and the author's list comes
    first: a suggestion that dropped a synonym the author added deliberately would widen
    the very leak this feature exists to close, and would do it silently.

    The label itself is never included. The simulator already matches on `order_text`, so
    echoing it back is noise that makes a real synonym harder to spot in the field.

    Deduplication is case-insensitive but preserves the first spelling seen, so the
    author's capitalisation survives.
    """
    merged: list[str] = []
    seen: set[str] = set()
    label_key = label.strip().casefold()

    for term in [*existing, *proposed]:
        cleaned = (term or "").strip()
        key = cleaned.casefold()
        if not cleaned or key == label_key or key in seen:
            continue
        seen.add(key)
        merged.append(cleaned)

    existing_keys = {(e or "").strip().casefold() for e in existing}
    added = [m for m in merged if m.casefold() not in existing_keys]
    return merged, added
