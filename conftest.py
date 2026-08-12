"""Put the repo root on sys.path so tests can import `backend.*`.

The application is imported as a package from the repo root (`backend.utils.…`) but the
repo has no installable package metadata, so nothing else would place the root on the
path. pytest inserts the directory holding the rootdir conftest, which is exactly this.
"""
