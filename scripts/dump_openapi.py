"""Write the FastAPI OpenAPI schema to web/openapi.json.

The schema is committed, and the TypeScript types are generated from the committed copy
rather than from a running server (ADR-020). Two reasons. A checkout builds without a
backend, database, or API keys; and a change to a Pydantic model shows up as a reviewable
diff in this file, next to the code that caused it, instead of as a type that silently
changes shape on whoever regenerates next.

Run after changing any request or response model:

    uv run --isolated --python 3.11 --with-requirements requirements.txt \
      python scripts/dump_openapi.py && (cd web && npm run gen:types)

**Run it against the pinned dependencies in `requirements.txt`**, which is what the
production image installs and what CI checks. The generated JSON Schema is
version-sensitive: fastapi 0.104.1 / pydantic 2.5.0 wrap `$ref`s in `allOf` and omit
`additionalProperties`, where newer versions do the opposite. Generating with an ad-hoc
`uv run --with fastapi --with pydantic` pulls the newest releases and produces a schema
that does not describe the deployed API — which is how the committed schema came to
disagree with production on 2026-07-31, caught by the schema-check workflow on its first
run.

**A bare `uv run` is not safe either**, which is what the old version of this line said to
use. The project `.venv` drifts: on 2026-08-12 it held fastapi 0.129 and pydantic 2.12
against those pins, and produced exactly the wrong-schema commit this docstring warns
about. Caught by the workflow again. Hence `--isolated --with-requirements` above, which
resolves from the pins rather than from whatever the venv has become.

CI should run this and fail if the result is dirty, so a model change cannot land without
its schema.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "openapi.json"

# Importing the app constructs LLMService, which refuses to start without a provider key.
# Nothing here calls a model, so a placeholder is honest: it documents that the schema
# dump is a pure read of the route table, not a live capability.
#
# Tested for emptiness, not just presence. An exported-but-empty OPENROUTER_API_KEY is a
# live condition in this environment, and it defeats both `setdefault` and `load_dotenv()`,
# which do not override a variable that already exists. The failure then reads as "key is
# required" while the key sits correctly in .env.
if not os.environ.get("OPENROUTER_API_KEY"):
    os.environ["OPENROUTER_API_KEY"] = "schema-dump-placeholder"
os.environ.setdefault(
    "POSTGRES_URL", "postgresql://schema:dump@localhost:5432/placeholder"
)
sys.path.insert(0, str(ROOT))

# Importing the app also runs `Base.metadata.create_all()` and probes the authoring
# schema, both at module scope — so without a reachable database the import raises and
# this script writes nothing. That made the docstring above false, and it made a CI
# "is the schema current?" check hollow: the dump would die, leave the committed file
# untouched, and the diff would come back clean. Caught by planting a deliberately stale
# schema and watching the check pass anyway.
#
# Neutralised here rather than in the app. `create_all` becoming conditional on an env
# var would be a production behaviour switch existing only to serve a build script, and
# the readiness probes failing soft is exactly what should NOT happen on a real backend.
# The route table is what is being read, and it does not depend on either.
import sqlalchemy  # noqa: E402

from backend.models import database as _db  # noqa: E402

sqlalchemy.MetaData.create_all = lambda *a, **k: None  # type: ignore[method-assign]
# Reported as available so the dump matches a healthy deployment. These flags gate
# request-time behaviour, never route registration, so this cannot change the schema —
# but pinning them keeps the output independent of whatever database happens to be
# reachable when the dump runs.
_db.authoring_schema_ready = lambda _bind: True  # type: ignore[assignment]
_db.final_orders_schema_ready = lambda _bind: True  # type: ignore[assignment]
_db.panel_run_snapshot_ready = lambda _bind: True  # type: ignore[assignment]

from backend.app.main import app  # noqa: E402


def main() -> int:
    schema = app.openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so an unrelated route addition cannot reshuffle the whole file, and a
    # trailing newline so the diff stays one line when only one field changed.
    OUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    paths = len(schema.get("paths", {}))
    models = len(schema.get("components", {}).get("schemas", {}))
    print(f"wrote {OUT.relative_to(ROOT)}: {paths} paths, {models} models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
