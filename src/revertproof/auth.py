"""Authentication, PBKDF2 password hashing, HMAC-SHA256 tokens, and RBAC.

Roles:
- `admin`: full access (users, repositories, DB connections, audit logs, runs)
- `developer`: run scans/verifications, connect repos, view reports
- `viewer`: read-only access to overview, history, and reports
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel

from revertproof.store import StateStore

ROLE_HIERARCHY: dict[str, int] = {"viewer": 1, "developer": 2, "admin": 3}


class AuthUser(BaseModel):
    username: str
    role: str


def _auth_secret() -> bytes:
    return os.getenv("RP_AUTH_SECRET", "revertproof-dev-secret-change-in-prod").encode("utf-8")


def hash_password(password: str) -> str:
    """Hash a password using salted PBKDF2-HMAC-SHA256 (200,000 iterations)."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"pbkdf2_sha256$200000${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iters_s, salt, hex_digest = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iters_s)
        )
        return hmac.compare_digest(dk.hex(), hex_digest)
    except Exception:
        return False


def create_access_token(username: str, role: str, ttl_seconds: int = 3600 * 8) -> str:
    """Create an HMAC-SHA256 signed bearer token."""
    payload = {"sub": username, "role": role, "exp": int(time.time()) + ttl_seconds}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    sig = hmac.new(_auth_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def decode_access_token(token: str) -> AuthUser:
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(_auth_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("Invalid token signature")
        payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("Token expired")
        return AuthUser(username=str(payload["sub"]), role=str(payload["role"]))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired authentication token: {exc}",
        ) from exc


def ensure_bootstrap_admin(store: StateStore) -> None:
    """Create initial admin user from env vars on first launch if no users exist."""
    if store.count_users() > 0:
        return
    admin_user = os.getenv("RP_ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("RP_ADMIN_PASSWORD", "revertproof-local-admin")
    store.create_user(admin_user, hash_password(admin_pass), role="admin")


def is_auth_enabled() -> bool:
    return os.getenv("RP_AUTH_ENABLED", "false").lower() in ("1", "true", "yes")


def get_current_user(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> AuthUser:
    """Authenticate via Bearer token or CI/CD X-API-Key when RP_AUTH_ENABLED=true."""
    if not is_auth_enabled():
        return AuthUser(username="local-admin", role="admin")

    configured_api_key = os.getenv("RP_CI_API_KEY", "")
    if x_api_key and configured_api_key and hmac.compare_digest(x_api_key, configured_api_key):
        return AuthUser(username="ci-pipeline", role="developer")

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return decode_access_token(token)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Sign in via POST /api/auth/login or provide X-API-Key.",
    )


def require_role(min_role: str):
    """FastAPI dependency enforcing minimum RBAC role (`viewer` < `developer` < `admin`)."""

    def _checker(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if ROLE_HIERARCHY.get(user.role, 0) < ROLE_HIERARCHY[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' lacks required '{min_role}' permission.",
            )
        return user

    return _checker
