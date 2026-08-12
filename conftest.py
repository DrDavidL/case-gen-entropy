"""Make the application importable in a test process.

Two things, both of which must happen at *import* time rather than in a fixture, because
test modules import the application during collection — a fixture runs far too late.

1. The repo root on `sys.path`. The app is imported as a package (`backend.utils.…`) but
   the repo has no installable package metadata, so nothing else would put the root there.
   pytest inserts the directory holding the rootdir conftest, which is this one.

2. A placeholder `POSTGRES_URL`. `backend/models/database.py` raises at import if it is
   unset. It does **not** connect: `create_engine()` builds a lazy pool, so a syntactically
   valid URL pointing nowhere is enough to import every module that is not the FastAPI app
   itself. Nothing in the suite opens a connection, and the unroutable port is the backstop
   if something ever tries.
"""

import os

os.environ.setdefault(
    "POSTGRES_URL", "postgresql://tests:tests@127.0.0.1:1/case_gen_tests_no_such_db"
)
