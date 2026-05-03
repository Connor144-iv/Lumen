"""phase 6 clinical documentation foundation

Revision ID: 20260427_0003
Revises: 20260426_0002
Create Date: 2026-04-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260427_0003"
down_revision = "20260426_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("session_notes"):
        op.create_table(
            "session_notes",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("patient_id", sa.String(length=36), nullable=False),
            sa.Column("referral_id", sa.String(length=36), nullable=True),
            sa.Column("therapist_id", sa.String(length=36), nullable=True),
            sa.Column("appointment_id", sa.String(length=36), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("source_document_id", sa.String(length=36), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"]),
            sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
            sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"]),
            sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["therapist_id"], ["therapists.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_session_notes_appointment_id"), "session_notes", ["appointment_id"])
        op.create_index(op.f("ix_session_notes_patient_id"), "session_notes", ["patient_id"])
        op.create_index(op.f("ix_session_notes_referral_id"), "session_notes", ["referral_id"])
        op.create_index(op.f("ix_session_notes_status"), "session_notes", ["status"])
        op.create_index(op.f("ix_session_notes_tenant_id"), "session_notes", ["tenant_id"])
        op.create_index(op.f("ix_session_notes_therapist_id"), "session_notes", ["therapist_id"])

    if not inspector.has_table("clinical_library_records"):
        op.create_table(
            "clinical_library_records",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("record_type", sa.String(length=80), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("version", sa.String(length=80), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("source_document_id", sa.String(length=36), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_clinical_library_records_record_type"), "clinical_library_records", ["record_type"])
        op.create_index(op.f("ix_clinical_library_records_status"), "clinical_library_records", ["status"])
        op.create_index(op.f("ix_clinical_library_records_tenant_id"), "clinical_library_records", ["tenant_id"])

    if not inspector.has_table("score_records"):
        op.create_table(
            "score_records",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("patient_id", sa.String(length=36), nullable=False),
            sa.Column("referral_id", sa.String(length=36), nullable=True),
            sa.Column("source_response_id", sa.String(length=36), nullable=True),
            sa.Column("instrument_name", sa.String(length=160), nullable=False),
            sa.Column("score_summary", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
            sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"]),
            sa.ForeignKeyConstraint(["source_response_id"], ["questionnaire_responses.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_score_records_instrument_name"), "score_records", ["instrument_name"])
        op.create_index(op.f("ix_score_records_patient_id"), "score_records", ["patient_id"])
        op.create_index(op.f("ix_score_records_referral_id"), "score_records", ["referral_id"])
        op.create_index(op.f("ix_score_records_source_response_id"), "score_records", ["source_response_id"])
        op.create_index(op.f("ix_score_records_status"), "score_records", ["status"])
        op.create_index(op.f("ix_score_records_tenant_id"), "score_records", ["tenant_id"])

    if not inspector.has_table("document_chunks"):
        op.create_table(
            "document_chunks",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("patient_id", sa.String(length=36), nullable=True),
            sa.Column("document_id", sa.String(length=36), nullable=True),
            sa.Column("source_type", sa.String(length=80), nullable=False),
            sa.Column("source_id", sa.String(length=36), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("embedding_model", sa.String(length=160), nullable=True),
            sa.Column("vector_ref", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
            sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_type", "source_id", "chunk_index", name="uq_document_chunks_source_index"),
        )
        op.create_index(op.f("ix_document_chunks_document_id"), "document_chunks", ["document_id"])
        op.create_index(op.f("ix_document_chunks_patient_id"), "document_chunks", ["patient_id"])
        op.create_index(op.f("ix_document_chunks_source_id"), "document_chunks", ["source_id"])
        op.create_index(op.f("ix_document_chunks_source_type"), "document_chunks", ["source_type"])
        op.create_index(op.f("ix_document_chunks_tenant_id"), "document_chunks", ["tenant_id"])


def downgrade() -> None:
    for table in [
        "document_chunks",
        "score_records",
        "clinical_library_records",
        "session_notes",
    ]:
        if sa.inspect(op.get_bind()).has_table(table):
            op.drop_table(table)
