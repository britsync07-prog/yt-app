"""JWT auth: one owner access key -> short-lived tokens.

Flow: POST /auth/login {"key": "<ACCESS_KEY>"} -> {"access_token", ...}
Protect routes with Depends(require_auth). /health stays public.
"""
import hmac
import os
import time

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

router = APIRouter()

JWT_ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    key: str


def _secret() -> str:
    return os.environ.get("JWT_SECRET", "")


def create_access_token() -> str:
    secret = _secret()
    if not secret:
        raise HTTPException(status_code=500, detail="Server misconfigured (JWT).")
    expire_minutes = int(os.environ.get("JWT_EXPIRE_MINUTES", "10080"))
    now = int(time.time())
    payload = {"sub": "owner", "iat": now, "exp": now + expire_minutes * 60}
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """Dependency: rejects requests without a valid Bearer JWT (401)."""
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials
    ):
        raise HTTPException(status_code=401, detail="Not authenticated.")
    try:
        jwt.decode(credentials.credentials, _secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")
    return True


@router.post("/auth/login")
def login(req: LoginRequest):
    """Exchange the owner access key for a short-lived JWT."""
    expected = os.environ.get("ACCESS_KEY", "")
    if not expected or not hmac.compare_digest(req.key, expected):
        raise HTTPException(status_code=401, detail="Invalid access key.")
    return {"access_token": create_access_token(), "token_type": "bearer"}
