"""documentation data layer tables

Revision ID: 20260510_0008
Revises: 20260504_0007
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260510_0008"
down_revision = "20260504_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("documentation_sessions"):
        op.create_table(
            "documentation_sessions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("patient_id", sa.String(length=36), nullable=False),
            sa.Column("therapist_id", sa.String(length=36), nullable=False),
            sa.Column("referral_id", sa.String(length=36), nullable=True),
            sa.Column("appointment_id", sa.String(length=36), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("patient_label_snapshot", sa.String(length=255), nullable=True),
            sa.Column("therapist_label_snapshot", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"]),
            sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
            sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["therapist_id"], ["therapists.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_documentation_sessions_appointment_id"), "documentation_sessions", ["appointment_id"])
        op.create_index(op.f("ix_documentation_sessions_patient_id"), "documentation_sessions", ["patient_id"])
        op.create_index(op.f("ix_documentation_sessions_referral_id"), "documentation_sessions", ["referral_id"])
        op.create_index(op.f("ix_documentation_sessions_status"), "documentation_sessions", ["status"])
        op.create_index(op.f("ix_documentation_sessions_tenant_id"), "documentation_sessions", ["tenant_id"])
        op.create_index(op.f("ix_documentation_sessions_therapist_id"), "documentation_sessions", ["therapist_id"])

    if not inspector.has_table("documentation_session_texts"):
        op.create_table(
            "documentation_session_texts",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("documentation_session_id", sa.String(length=36), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("input_type", sa.String(length=80), nullable=False),
            sa.Column("source_metadata", sa.JSON(), nullable=False),
            sa.Column("raw_source_stored", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["documentation_session_id"], ["documentation_sessions.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_documentation_session_texts_documentation_session_id"),
            "documentation_session_texts",
            ["documentation_session_id"],
        )
        op.create_index(op.f("ix_documentation_session_texts_input_type"), "documentation_session_texts", ["input_type"])
        op.create_index(op.f("ix_documentation_session_texts_tenant_id"), "documentation_session_texts", ["tenant_id"])

    if not inspector.has_table("documentation_session_notes"):
        op.create_table(
            "documentation_session_notes",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("documentation_session_id", sa.String(length=36), nullable=False),
            sa.Column("source_text_id", sa.String(length=36), nullable=True),
            sa.Column("note_json", sa.JSON(), nullable=False),
            sa.Column("reviewed_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("generator", sa.String(length=80), nullable=True),
            sa.Column("model", sa.String(length=255), nullable=True),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reviewer_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["documentation_session_id"], ["documentation_sessions.id"]),
            sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["source_text_id"], ["documentation_session_texts.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_documentation_session_notes_documentation_session_id"),
            "documentation_session_notes",
            ["documentation_session_id"],
        )
        op.create_index(op.f("ix_documentation_session_notes_source_text_id"), "documentation_session_notes", ["source_text_id"])
        op.create_index(op.f("ix_documentation_session_notes_status"), "documentation_session_notes", ["status"])
        op.create_index(op.f("ix_documentation_session_notes_tenant_id"), "documentation_session_notes", ["tenant_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("documentation_session_notes"):
        op.drop_table("documentation_session_notes")
    if inspector.has_table("documentation_session_texts"):
        op.drop_table("documentation_session_texts")
    if inspector.has_table("documentation_sessions"):
        op.drop_table("documentation_sessions")
