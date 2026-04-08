"""Password hashing and JWT creation/verification (HS256)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings as app_settings

# --- JWT configuration (see environment: JWT_SECRET_KEY) ---
ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
REFRESH_TOKEN_EXPIRE_DAYS: int = 7

# Use a long-password-safe primary scheme for new hashes, while still accepting
# existing bcrypt hashes during login.
_pwd_context = CryptContext(
    schemes=["pbkdf2_sha256", "bcrypt"],
    deprecated="auto",
)


def _jwt_secret() -> str:
    """
    Read signing secret.

    Prefer real OS environment (e.g. Docker/K8s), then Pydantic ``Settings``
    (which loads ``.env``). ``os.getenv`` alone misses variables that only exist
    in ``.env`` because that file is not always exported into ``os.environ``.
    """
    for candidate in (
        os.getenv("JWT_SECRET_KEY"),
        os.getenv("SECRET_KEY"),
        getattr(app_settings, "JWT_SECRET_KEY", None),
        getattr(app_settings, "SECRET_KEY", None),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return ""


def _require_jwt_secret() -> str:
    secret = _jwt_secret()
    if not secret:
        raise ValueError("JWT_SECRET_KEY is not set; cannot sign JWTs.")
    return secret


def hash_password(password: str) -> str:
    """
    Hash a plain password for storage.

    Returns a string suitable for storing in ``users.password_hash``.
    """
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify ``plain_password`` against a stored password hash.

    Returns ``False`` on mismatch or if verification fails.
    """
    try:
        return _pwd_context.verify(plain_password, hashed_password)
    except (ValueError, TypeError):
        return False


def _exp_timestamp(minutes: int = 0, days: int = 0) -> int:
    """UTC unix timestamp for JWT ``exp`` claim."""
    delta = timedelta(minutes=minutes) if minutes else timedelta(days=days)
    when = datetime.now(timezone.utc) + delta
    return int(when.timestamp())


def create_access_token(data: dict[str, Any]) -> str:
    """
    Create a short-lived access JWT (30 minutes).

    Merges ``data`` with ``exp`` and ``type: "access"``. Expected keys include
    ``sub`` (user id) and ``role``.

    Payload shape: ``{"sub": ..., "role": ..., "exp": <unix_ts>, "type": "access"}``
    plus any extra keys from ``data``.
    """
    secret = _require_jwt_secret()
    to_encode = {
        **data,
        "exp": _exp_timestamp(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
    }
    return jwt.encode(to_encode, secret, algorithm=ALGORITHM)


def create_refresh_token(data: dict[str, Any]) -> str:
    """
    Create a long-lived refresh JWT (7 days).

    Merges ``data`` with ``exp`` and ``type: "refresh"``. Expected keys include
    ``sub`` (user id).

    Payload shape: ``{"sub": ..., "exp": <unix_ts>, "type": "refresh"}``.
    """
    secret = _require_jwt_secret()
    to_encode = {
        **data,
        "exp": _exp_timestamp(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "type": "refresh",
    }
    return jwt.encode(to_encode, secret, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[dict[str, Any]]:
    """
    Decode and validate a JWT.

    Returns the claims dict if the signature is valid and the token is not expired.
    Returns ``None`` on any failure (invalid signature, expired, malformed).
    """
    secret = _jwt_secret()
    if not secret:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
        return payload if isinstance(payload, dict) else None
    except JWTError:
        return None
