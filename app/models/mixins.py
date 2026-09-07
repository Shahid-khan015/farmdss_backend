from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Uuid, text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.expression import FunctionElement


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class uuid_server_default(FunctionElement):  # noqa: N801 - reads as SQL, not a class
    """A database-side random UUID default that compiles on Postgres *and* SQLite.

    Every model here declares ``server_default=text("gen_random_uuid()")``, which is
    Postgres-only: SQLite cannot parse it and fails the CREATE TABLE outright. That makes
    ``Base.metadata.create_all`` -- the documented SQLite fallback boot in ``main.py`` --
    unusable, and it is why the session/telemetry tests could not build a local schema.

    Python-side ``default=uuid.uuid4`` already supplies the value for every insert the ORM
    makes; the server default only matters for rows inserted by raw SQL, so emitting
    SQLite's own random hex is an equivalent guarantee rather than a downgrade.
    """

    name = "uuid_server_default"
    inherit_cache = True


@compiles(uuid_server_default)
def _compile_uuid_default(element, compiler, **kw) -> str:
    return "gen_random_uuid()"


@compiles(uuid_server_default, "sqlite")
def _compile_uuid_default_sqlite(element, compiler, **kw) -> str:
    return "(lower(hex(randomblob(16))))"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=utcnow,
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

