"""add users, user_profiles, user_sessions and tractor/implement ownership columns

Revision ID: d4e5f6a7b8c0
Revises: b2a4e1c0d99f
Create Date: 2026-03-30

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c0"
down_revision = "b2a4e1c0d99f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() is built-in on PostgreSQL 13+.
    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("phone_number", sa.String(length=15), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('farmer', 'operator', 'owner', 'researcher')",
            name="ck_users_role",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone_number", name="uq_users_phone_number"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_phone_number", "users", ["phone_number"], unique=False)
    op.create_index("ix_users_role", "users", ["role"], unique=False)

    op.create_table(
        "user_profiles",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("farm_name", sa.String(length=255), nullable=True),
        sa.Column("farm_location", sa.String(length=255), nullable=True),
        sa.Column("total_land_hectares", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("license_number", sa.String(length=50), nullable=True),
        sa.Column("experience_years", sa.Integer(), nullable=True),
        sa.Column("wage_rate_per_hour", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("wage_rate_per_hectare", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("specialization", sa.String(length=100), nullable=True),
        sa.Column("business_name", sa.String(length=255), nullable=True),
        sa.Column("gst_number", sa.String(length=20), nullable=True),
        sa.Column("bank_account_number", sa.String(length=20), nullable=True),
        sa.Column("ifsc_code", sa.String(length=15), nullable=True),
        sa.Column("upi_id", sa.String(length=100), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("pincode", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_profiles_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_profiles_user_id"),
    )

    op.create_table(
        "user_sessions",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("access_token", sa.String(length=500), nullable=False),
        sa.Column("refresh_token", sa.String(length=500), nullable=False),
        sa.Column("device_info", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("access_token", name="uq_user_sessions_access_token"),
        sa.UniqueConstraint("refresh_token", name="uq_user_sessions_refresh_token"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"], unique=False)
    op.create_index(
        "ix_user_sessions_access_token",
        "user_sessions",
        ["access_token"],
        unique=False,
    )

    op.add_column("tractors", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.add_column(
        "tractors",
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_foreign_key(
        "fk_tractors_owner_id_users",
        "tractors",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_tractors_owner", "tractors", ["owner_id"], unique=False)

    op.add_column("implements", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.add_column(
        "implements",
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_foreign_key(
        "fk_implements_owner_id_users",
        "implements",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_implements_owner", "implements", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_implements_owner", table_name="implements")
    op.drop_constraint("fk_implements_owner_id_users", "implements", type_="foreignkey")
    op.drop_column("implements", "is_public")
    op.drop_column("implements", "owner_id")

    op.drop_index("idx_tractors_owner", table_name="tractors")
    op.drop_constraint("fk_tractors_owner_id_users", "tractors", type_="foreignkey")
    op.drop_column("tractors", "is_public")
    op.drop_column("tractors", "owner_id")

    op.drop_index("ix_user_sessions_access_token", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_table("user_profiles")

    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_phone_number", table_name="users")
    op.drop_table("users")
