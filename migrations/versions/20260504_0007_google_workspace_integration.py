"""google workspace integration fields

Revision ID: 20260504_0007
Revises: 20260503_0006
Create Date: 2026-05-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260504_0007"
down_revision = "20260503_0006"
branch_labels = None
depends_on = None


COMMUNICATION_DRAFT_COLUMNS = {
    "recipient_email": sa.Column("recipient_email", sa.String(length=255), nullable=True),
    "sent_at": sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    "provider": sa.Column("provider", sa.String(length=80), nullable=True),
    "gmail_message_id": sa.Column("gmail_message_id", sa.String(length=255), nullable=True),
    "gmail_thread_id": sa.Column("gmail_thread_id", sa.String(length=255), nullable=True),
    "last_provider_error": sa.Column("last_provider_error", sa.Text(), nullable=True),
}

APPOINTMENT_COLUMNS = {
    "google_calendar_id": sa.Column("google_calendar_id", sa.String(length=255), nullable=True),
    "google_calendar_event_id": sa.Column("google_calendar_event_id", sa.String(length=255), nullable=True),
    "google_calendar_event_link": sa.Column("google_calendar_event_link", sa.String(length=500), nullable=True),
    "google_calendar_synced_at": sa.Column("google_calendar_synced_at", sa.DateTime(timezone=True), nullable=True),
    "last_provider_error": sa.Column("last_provider_error", sa.Text(), nullable=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("communication_drafts"):
        existing = {column["name"] for column in inspector.get_columns("communication_drafts")}
        with op.batch_alter_table("communication_drafts") as batch:
            for name, column in COMMUNICATION_DRAFT_COLUMNS.items():
                if name not in existing:
                    batch.add_column(column)

    if inspector.has_table("appointments"):
        existing = {column["name"] for column in inspector.get_columns("appointments")}
        with op.batch_alter_table("appointments") as batch:
            for name, column in APPOINTMENT_COLUMNS.items():
                if name not in existing:
                    batch.add_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("appointments"):
        existing = {column["name"] for column in inspector.get_columns("appointments")}
        with op.batch_alter_table("appointments") as batch:
            for name in reversed(list(APPOINTMENT_COLUMNS)):
                if name in existing:
                    batch.drop_column(name)

    if inspector.has_table("communication_drafts"):
        existing = {column["name"] for column in inspector.get_columns("communication_drafts")}
        with op.batch_alter_table("communication_drafts") as batch:
            for name in reversed(list(COMMUNICATION_DRAFT_COLUMNS)):
                if name in existing:
                    batch.drop_column(name)
