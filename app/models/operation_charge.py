from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, String, Uuid, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class OperationCharge(Base):
    __tablename__ = "operation_charges"
    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('Tillage','Sowing','Spraying','Weeding','Harvesting','Threshing','Grading')",
            name="ck_operation_charges_operation_type",
        ),
        UniqueConstraint("owner_id", "operation_type", name="uq_operation_charges_owner_operation_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operation_type = Column(String(50), nullable=False)
    charge_per_ha = Column(Float, nullable=False)
    currency = Column(String(10), nullable=False, server_default=text("'INR'"))
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

    owner: Mapped["User"] = relationship("User", foreign_keys=[owner_id])
