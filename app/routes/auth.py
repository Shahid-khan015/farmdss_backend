# Follows pattern from: app/api/v1/routes/tractors.py (APIRouter, Depends, Session, status)
"""Authentication endpoints: register, login, refresh, logout, profile."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_db
from app.middleware.auth import get_current_user, require_role
from app.models.user import User, UserProfile, UserSession
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    ProfileResponse,
    ProfileUpdateRequest,
    RegisterRequest,
    RefreshTokenRequest,
    FarmerOptionResponse,
    UserResponse,
)
from app.utils.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
    verify_token,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

_ACCESS_EXPIRES_SECONDS = ACCESS_TOKEN_EXPIRE_MINUTES * 60


class AccessTokenResponse(BaseModel):
    """New access token after refresh (refresh token unchanged)."""

    model_config = ConfigDict(json_schema_extra={"example": {"access_token": "...", "token_type": "Bearer", "expires_in": 1800}})

    access_token: str
    token_type: str = Field(default="Bearer")
    expires_in: int = Field(..., ge=0, description="Access token lifetime in seconds.")


class MessageResponse(BaseModel):
    message: str


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP (proxies may set X-Forwarded-For)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _profile_to_response(profile: UserProfile) -> ProfileResponse:
    return ProfileResponse(
        id=str(profile.id),
        user_id=str(profile.user_id),
        farm_name=profile.farm_name,
        farm_location=profile.farm_location,
        total_land_hectares=profile.total_land_hectares,
        license_number=profile.license_number,
        experience_years=profile.experience_years,
        wage_rate_per_hour=profile.wage_rate_per_hour,
        wage_rate_per_hectare=profile.wage_rate_per_hectare,
        specialization=profile.specialization,
        business_name=profile.business_name,
        gst_number=profile.gst_number,
        bank_account_number=profile.bank_account_number,
        ifsc_code=profile.ifsc_code,
        upi_id=profile.upi_id,
        address=profile.address,
        city=profile.city,
        state=profile.state,
        pincode=profile.pincode,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _user_response_with_profile(user: User, profile: UserProfile | None) -> UserResponse:
    pr = _profile_to_response(profile) if profile is not None else None
    return UserResponse(
        id=str(user.id),
        phone_number=user.phone_number,
        name=user.name,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        profile=pr,
    )


def _user_response_without_profile(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        phone_number=user.phone_number,
        name=user.name,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        profile=None,
    )


def _farmer_option_response(user: User, profile: UserProfile | None) -> FarmerOptionResponse:
    return FarmerOptionResponse(
        id=str(user.id),
        name=user.name,
        phone_number=user.phone_number,
        farm_name=profile.farm_name if profile is not None else None,
        farm_location=profile.farm_location if profile is not None else None,
        total_land_hectares=profile.total_land_hectares if profile is not None else None,
    )


def _issue_tokens_and_session(
    *,
    db: Session,
    user: User,
    request: Request,
) -> LoginResponse:
    """Create access/refresh JWTs and persist a ``UserSession`` row."""
    access = create_access_token({"sub": str(user.id), "role": user.role})
    refresh = create_refresh_token({"sub": str(user.id)})
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    row = UserSession(
        user_id=user.id,
        access_token=access,
        refresh_token=refresh,
        device_info=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(user)
    prof = db.scalars(select(UserProfile).where(UserProfile.user_id == user.id)).first()
    return LoginResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="Bearer",
        expires_in=_ACCESS_EXPIRES_SECONDS,
        user=_user_response_with_profile(user, prof),
    )


@router.post(
    "/register",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
def register(
    body: RegisterRequest,
    http_request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Create user, profile, session, and return tokens."""
    exists = db.scalars(select(User).where(User.phone_number == body.phone_number)).first()
    if exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone already registered",
        )

    try:
        password_hash = hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    user = User(
        phone_number=body.phone_number,
        password_hash=password_hash,
        name=body.name,
        email=body.email,
        role=body.role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    profile = UserProfile(
        user_id=user.id,
        business_name=body.business_name,
        gst_number=body.gst_number,
        license_number=body.license_number,
        experience_years=body.experience_years,
        wage_rate_per_hour=body.wage_rate_per_hour,
        wage_rate_per_hectare=body.wage_rate_per_hectare,
        farm_name=body.farm_name,
        farm_location=body.farm_location,
        total_land_hectares=body.total_land_hectares,
    )
    db.add(profile)
    db.flush()

    try:
        access = create_access_token({"sub": str(user.id), "role": user.role})
        refresh = create_refresh_token({"sub": str(user.id)})
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service misconfigured (JWT_SECRET_KEY).",
        ) from exc

    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    sess = UserSession(
        user_id=user.id,
        access_token=access,
        refresh_token=refresh,
        device_info=http_request.headers.get("user-agent"),
        ip_address=_client_ip(http_request),
        expires_at=expires_at,
    )
    db.add(sess)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration failed (duplicate email or constraint violation).",
        ) from exc

    db.refresh(user)
    fresh_profile = db.scalars(select(UserProfile).where(UserProfile.user_id == user.id)).first()
    return LoginResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="Bearer",
        expires_in=_ACCESS_EXPIRES_SECONDS,
        user=_user_response_with_profile(user, fresh_profile),
    )


@router.post("/login", response_model=LoginResponse, summary="Login with phone and password")
def login(
    body: LoginRequest,
    http_request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Authenticate user, create a new session, return tokens."""
    phone = body.phone_number.strip()
    user = db.scalars(select(User).where(User.phone_number == phone)).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    try:
        return _issue_tokens_and_session(db=db, user=user, request=http_request)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service misconfigured (JWT_SECRET_KEY).",
        ) from exc


@router.post("/refresh", response_model=AccessTokenResponse, summary="Issue a new access token")
def refresh_token(
    body: RefreshTokenRequest,
    db: Session = Depends(get_db),
) -> AccessTokenResponse:
    """Validate refresh JWT and stored session; rotate access token on the session row."""
    payload = verify_token(body.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    sub = payload.get("sub")
    if sub is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    sess = db.scalars(
        select(UserSession).where(UserSession.refresh_token == body.refresh_token)
    ).first()
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session not found or revoked",
        )
    if str(sess.user_id) != str(sub):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    now = datetime.now(timezone.utc)
    exp = sess.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh session expired",
        )

    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    try:
        new_access = create_access_token({"sub": str(user.id), "role": user.role})
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service misconfigured (JWT_SECRET_KEY).",
        ) from exc

    sess.access_token = new_access
    db.commit()

    return AccessTokenResponse(
        access_token=new_access,
        token_type="Bearer",
        expires_in=_ACCESS_EXPIRES_SECONDS,
    )


@router.post("/logout", response_model=MessageResponse, summary="Log out (revoke all sessions)")
def logout(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Delete all refresh/access sessions for the current user."""
    db.execute(delete(UserSession).where(UserSession.user_id == current_user.id))
    db.commit()
    return MessageResponse(message="Logged out successfully")


@router.get("/me", summary="Current user and profile")
def read_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return authenticated user core fields plus profile (role-specific columns as stored)."""
    user = db.scalars(
        select(User)
        .where(User.id == current_user.id)
        .options(selectinload(User.profile))
    ).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    prof = user.profile
    user_out = _user_response_without_profile(user).model_dump()
    profile_out = _profile_to_response(prof).model_dump() if prof is not None else None
    return {"user": user_out, "profile": profile_out}


@router.patch("/profile", response_model=MessageResponse, summary="Update profile (partial)")
def update_profile(
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Apply non-None profile fields (PATCH semantics)."""
    profile = db.scalars(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    ).first()
    if profile is None:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
        db.flush()

    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is not None and hasattr(profile, key):
            setattr(profile, key, value)

    profile.updated_at = datetime.now(timezone.utc)
    db.commit()

    return MessageResponse(message="Profile updated successfully")


@router.get("/farmers", response_model=list[FarmerOptionResponse], summary="List farmers for session assignment")
def list_farmers(
    _: User = Depends(require_role(["operator", "owner", "researcher"])),
    db: Session = Depends(get_db),
) -> list[FarmerOptionResponse]:
    farmers = db.scalars(
        select(User)
        .where(User.role == "farmer", User.is_active.is_(True))
        .options(selectinload(User.profile))
        .order_by(User.name.asc())
    ).all()
    return [_farmer_option_response(farmer, farmer.profile) for farmer in farmers]
