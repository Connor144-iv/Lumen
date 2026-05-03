"""phase 7 report drafts

Revision ID: 20260427_0004
Revises: 20260427_0003
Create Date: 2026-04-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260427_0004"
down_revision = "20260427_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("report_drafts"):
        return
    op.create_table(
        "report_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=False),
        sa.Column("referral_id", sa.String(length=36), nullable=True),
        sa.Column("therapist_id", sa.String(length=36), nullable=True),
        sa.Column("report_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("claim_evidence_map", sa.JSON(), nullable=False),
        sa.Column("unsupported_claims", sa.JSON(), nullable=False),
        sa.Column("retrieval_summary", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("signed_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_off_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"]),
        sa.ForeignKeyConstraint(["signed_off_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["therapist_id"], ["therapists.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_report_drafts_patient_id"), "report_drafts", ["patient_id"])
    op.create_index(op.f("ix_report_drafts_referral_id"), "report_drafts", ["referral_id"])
    op.create_index(op.f("ix_report_drafts_report_type"), "report_drafts", ["report_type"])
    op.create_index(op.f("ix_report_drafts_status"), "report_drafts", ["status"])
    op.create_index(op.f("ix_report_drafts_tenant_id"), "report_drafts", ["tenant_id"])
    op.create_index(op.f("ix_report_drafts_therapist_id"), "report_drafts", ["therapist_id"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("report_drafts"):
        op.drop_table("report_drafts")
