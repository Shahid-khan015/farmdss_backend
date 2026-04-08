"""JWT bearer authentication and role-based access dependencies."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.user import User
from app.utils.security import verify_token

security = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """
    Validate ``Authorization: Bearer <access_token>`` and return the active user.

    Expects a verified access JWT with claims ``sub`` (user id) and ``type`` equal
    to ``\"access\"``.
    """
    token = credentials.credentials
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        uid = uuid.UUID(str(user_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.scalars(select(User).where(User.id == uid)).first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def require_role(
    allowed_roles: Sequence[str],
) -> Callable[..., User]:
    """
    Build a dependency that requires ``current_user.role`` to be one of ``allowed_roles``.

    Usage::

        @router.get("/admin", dependencies=[Depends(require_role(["owner"]))])
        def admin_only(...):
            ...

        # or inject user:
        @router.get("/me")
        def me(user: User = Depends(require_role(["farmer", "owner"]))):
            ...
    """
    allowed = frozenset(allowed_roles)

    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {sorted(allowed)}",
            )
        return current_user

    return role_checker
