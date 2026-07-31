import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

load_dotenv()

security = HTTPBasic()


def get_auth_credentials():
    """Get authentication credentials from environment"""
    username = os.getenv("APP_USERNAME", "admin")
    password = os.getenv("APP_PASSWORD", "dhds-bypass")
    return username, password


def _credentials_match(credentials: HTTPBasicCredentials) -> bool:
    """Constant-time comparison against the configured single account.

    `compare_digest` on both fields, and both are always evaluated — short-circuiting on
    a wrong username would leak which half failed through timing.
    """
    correct_username, correct_password = get_auth_credentials()
    is_correct_username = secrets.compare_digest(credentials.username, correct_username)
    is_correct_password = secrets.compare_digest(credentials.password, correct_password)
    return is_correct_username and is_correct_password


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic Auth credentials"""
    if not _credentials_match(credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# `auto_error=False` so a missing header reaches the function as `None` rather than
# FastAPI raising its own 401 with a `WWW-Authenticate` challenge.
_silent_security = HTTPBasic(auto_error=False)


def verify_credentials_silent(
    credentials: HTTPBasicCredentials | None = Depends(_silent_security),
):
    """Same check as `verify_credentials`, but never sends a `WWW-Authenticate` header.

    For the SPA's login probe. That header is what makes a browser throw up its own
    native credential dialog on a 401, which would fight the app's own login form: the
    user would get a system popup they cannot style, cannot log out of, and which
    reappears on every failed attempt.

    Deliberately a separate dependency rather than a change to `verify_credentials`. The
    challenge header is correct for every other caller — the Streamlit UI and anything
    hitting the API directly — and quietly removing it everywhere to suit one client
    would weaken a working mechanism for the rest (ADR-021).
    """
    if credentials is None or not _credentials_match(credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    return credentials.username
