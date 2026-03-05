"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("first_name", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clients_telegram_id", "clients", ["telegram_id"], unique=True)
    op.create_index("ix_clients_phone", "clients", ["phone"])

    op.create_table(
        "masters",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("photo_url", sa.String(512), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "services",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "master_services",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("master_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_master_services_master_service", "master_services", ["master_id", "service_id"], unique=True)

    op.create_table(
        "bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("master_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("confirmed", "cancelled_client", "cancelled_admin", "completed", name="bookingstatus"),
            nullable=False,
            server_default="confirmed",
        ),
        sa.Column("price_at_booking", sa.Numeric(10, 2), nullable=False),
        sa.Column("reminder_24h_sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reminder_2h_sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("review_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bookings_client_status", "bookings", ["client_id", "status"])
    op.create_index(
        "ix_bookings_pending_24h_reminder",
        "bookings",
        ["id"],
        postgresql_where=sa.text("status = 'confirmed' AND reminder_24h_sent = FALSE"),
    )

    op.create_table(
        "slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("master_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"]),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_slots_master_starts_at", "slots", ["master_id", "starts_at"])
    op.create_index("ix_slots_booking_id", "slots", ["booking_id"])

    op.create_table(
        "reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("booking_id"),
    )


def downgrade() -> None:
    op.drop_table("reviews")
    op.drop_index("ix_slots_booking_id", table_name="slots")
    op.drop_index("ix_slots_master_starts_at", table_name="slots")
    op.drop_table("slots")
    op.drop_index("ix_bookings_pending_24h_reminder", table_name="bookings")
    op.drop_index("ix_bookings_client_status", table_name="bookings")
    op.drop_table("bookings")
    op.execute("DROP TYPE IF EXISTS bookingstatus")
    op.drop_index("ix_master_services_master_service", table_name="master_services")
    op.drop_table("master_services")
    op.drop_table("services")
    op.drop_table("masters")
    op.drop_index("ix_clients_phone", table_name="clients")
    op.drop_index("ix_clients_telegram_id", table_name="clients")
    op.drop_table("clients")
