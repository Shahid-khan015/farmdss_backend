# Follows pattern from: app/models/simulation.py (Mapped, mapped_column, relationships, Uuid FK)
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    DECIMAL,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('farmer', 'operator', 'owner', 'researcher')",
            name="ck_users_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    phone_number: Mapped[str] = mapped_column(String(15), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    profile: Mapped[Optional["UserProfile"]] = relationship(
        "UserProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        "UserSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    operated_sessions: Mapped[list["OperationSession"]] = relationship(
        "OperationSession",
        back_populates="operator",
        foreign_keys="OperationSession.operator_id",
    )
    owned_sessions: Mapped[list["OperationSession"]] = relationship(
        "OperationSession",
        back_populates="owner",
        foreign_keys="OperationSession.tractor_owner_id",
    )
    client_sessions: Mapped[list["OperationSession"]] = relationship(
        "OperationSession",
        back_populates="farmer",
        foreign_keys="OperationSession.client_farmer_id",
    )

    def __repr__(self) -> str:
        return f"User(id={self.id!s}, phone_number={self.phone_number!r}, role={self.role!r})"


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    farm_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    farm_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    total_land_hectares: Mapped[Optional[Decimal]] = mapped_column(
        DECIMAL(10, 2),
        nullable=True,
    )
    license_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    experience_years: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    wage_rate_per_hour: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    wage_rate_per_hectare: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2), nullable=True)
    specialization: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    business_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    gst_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bank_account_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ifsc_code: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    upi_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship("User", back_populates="profile")

    def __repr__(self) -> str:
        return f"UserProfile(id={self.id!s}, user_id={self.user_id!s})"


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    access_token: Mapped[str] = mapped_column(String(500), nullable=False, unique=True, index=True)
    refresh_token: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    device_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship("User", back_populates="sessions")

    def __repr__(self) -> str:
        return f"UserSession(id={self.id!s}, user_id={self.user_id!s})"
