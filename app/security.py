"""Small, dependency-free signed access tokens for the demo API.

Use an external identity provider/OIDC in a multi-user production deployment.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .config import settings

bearer = HTTPBearer(auto_error=False)


def _secret() -> bytes:
    if not settings.secret_key:
        raise RuntimeError("SECRET_KEY must be configured")
    return settings.secret_key.encode()


def issue_token(subject: str, role: str = "operator") -> str:
    payload = {"sub": subject, "role": role, "exp": int(time.time()) + 3600, "jti": secrets.token_hex(8)}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    signature = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return raw.decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


def current_user(credentials: HTTPAuthorizationCredentials | None = Security(bearer)) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Authentication required")
    try:
        raw, signature = credentials.credentials.split(".", 1)
        expected = hmac.new(_secret(), raw.encode(), hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, supplied):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if payload["exp"] < time.time():
            raise ValueError
        return payload
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid or expired access token")


def require_admin(user: dict = Security(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Administrator role required")
    return user
