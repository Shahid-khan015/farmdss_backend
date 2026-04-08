# Follows pattern from: app/models/tractor.py, app/models/simulation.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import UUIDPrimaryKeyMixin

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IoTReading(Base, UUIDPrimaryKeyMixin):
    """Append-only time-series row; `adafruit_id` enables idempotent HTTP poll ingestion."""

    __tablename__ = "iot_readings"
    __table_args__ = (
        Index("ix_iot_readings_feed_key_device_ts", "feed_key", "device_timestamp"),
        Index("ix_iot_readings_device_feed", "device_id", "feed_key"),
        Index("ix_iot_readings_session_id", "session_id"),
    )

    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    feed_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Original payload as received (stringified); numeric/GPS derived fields below.
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)

    numeric_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    device_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Adafruit data point id when from REST; MQTT transport may use synthetic ids (see normalizer).
    adafruit_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    session: Mapped[Optional["OperationSession"]] = relationship(
        "OperationSession",
        foreign_keys="[IoTReading.session_id]",
    )
