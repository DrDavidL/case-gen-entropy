"""Write the FastAPI OpenAPI schema to web/openapi.json.

The schema is committed, and the TypeScript types are generated from the committed copy
rather than from a running server (ADR-020). Two reasons. A checkout builds without a
backend, database, or API keys; and a change to a Pydantic model shows up as a reviewable
diff in this file, next to the code that caused it, instead of as a type that silently
changes shape on whoever regenerates next.

Run after changing any request or response model:

    uv run python scripts/dump_openapi.py && (cd web && npm run gen:types)

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
sys.path.insert(0, str(ROOT))

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
