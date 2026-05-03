"""phase 8 imports and phase 11 feedback

Revision ID: 20260427_0005
Revises: 20260427_0004
Create Date: 2026-04-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260427_0005"
down_revision = "20260427_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("referral_import_batches"):
        op.create_table(
            "referral_import_batches",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("source_channel", sa.String(length=80), nullable=False),
            sa.Column("file_name", sa.String(length=255), nullable=True),
            sa.Column("source_document_id", sa.String(length=36), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("total_rows", sa.Integer(), nullable=False),
            sa.Column("imported_count", sa.Integer(), nullable=False),
            sa.Column("error_count", sa.Integer(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_referral_import_batches_source_channel"), "referral_import_batches", ["source_channel"])
        op.create_index(op.f("ix_referral_import_batches_source_document_id"), "referral_import_batches", ["source_document_id"])
        op.create_index(op.f("ix_referral_import_batches_status"), "referral_import_batches", ["status"])
        op.create_index(op.f("ix_referral_import_batches_tenant_id"), "referral_import_batches", ["tenant_id"])

    if not inspector.has_table("referral_import_errors"):
        op.create_table(
            "referral_import_errors",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("batch_id", sa.String(length=36), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("raw_row", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["batch_id"], ["referral_import_batches.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_referral_import_errors_batch_id"), "referral_import_errors", ["batch_id"])
        op.create_index(op.f("ix_referral_import_errors_tenant_id"), "referral_import_errors", ["tenant_id"])

    if not inspector.has_table("draft_feedback"):
        op.create_table(
            "draft_feedback",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("patient_id", sa.String(length=36), nullable=True),
            sa.Column("referral_id", sa.String(length=36), nullable=True),
            sa.Column("report_draft_id", sa.String(length=36), nullable=True),
            sa.Column("reviewer_id", sa.String(length=36), nullable=True),
            sa.Column("feedback_type", sa.String(length=80), nullable=False),
            sa.Column("original_text", sa.Text(), nullable=True),
            sa.Column("final_text", sa.Text(), nullable=True),
            sa.Column("edit_summary", sa.JSON(), nullable=False),
            sa.Column("usable_for_practice_memory", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
            sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"]),
            sa.ForeignKeyConstraint(["report_draft_id"], ["report_drafts.id"]),
            sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_draft_feedback_feedback_type"), "draft_feedback", ["feedback_type"])
        op.create_index(op.f("ix_draft_feedback_patient_id"), "draft_feedback", ["patient_id"])
        op.create_index(op.f("ix_draft_feedback_referral_id"), "draft_feedback", ["referral_id"])
        op.create_index(op.f("ix_draft_feedback_report_draft_id"), "draft_feedback", ["report_draft_id"])
        op.create_index(op.f("ix_draft_feedback_tenant_id"), "draft_feedback", ["tenant_id"])
        op.create_index(op.f("ix_draft_feedback_usable_for_practice_memory"), "draft_feedback", ["usable_for_practice_memory"])


def downgrade() -> None:
    for table in ["draft_feedback", "referral_import_errors", "referral_import_batches"]:
        if sa.inspect(op.get_bind()).has_table(table):
            op.drop_table(table)
