from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import os
from dotenv import load_dotenv

load_dotenv()

security = HTTPBasic()

def get_auth_credentials():
    """Get authentication credentials from environment"""
    username = os.getenv("APP_USERNAME", "admin")
    password = os.getenv("APP_PASSWORD", "dhds-bypass")
    return username, password

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic Auth credentials"""
    correct_username, correct_password = get_auth_credentials()
    
    is_correct_username = secrets.compare_digest(
        credentials.username, correct_username
    )
    is_correct_password = secrets.compare_digest(
        credentials.password, correct_password
    )
    
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username