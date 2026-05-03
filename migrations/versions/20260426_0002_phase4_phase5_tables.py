"""phase 4 and phase 5 tables

Revision ID: 20260426_0002
Revises: 20260424_0001
Create Date: 2026-04-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260426_0002"
down_revision = "20260424_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("intake_templates"):
        op.create_table(
            "intake_templates",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=180), nullable=False),
            sa.Column("patient_type", sa.String(length=80), nullable=False),
            sa.Column("insurer", sa.String(length=160), nullable=True),
            sa.Column("age_band", sa.String(length=80), nullable=True),
            sa.Column("modality", sa.String(length=80), nullable=True),
            sa.Column("source_channel", sa.String(length=80), nullable=True),
            sa.Column("required_items", sa.JSON(), nullable=False),
            sa.Column("questionnaire_schema", sa.JSON(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_intake_templates_tenant_id"), "intake_templates", ["tenant_id"])

    if not inspector.has_table("intake_checklist_items"):
        op.create_table(
            "intake_checklist_items",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("patient_id", sa.String(length=36), nullable=True),
            sa.Column("referral_id", sa.String(length=36), nullable=True),
            sa.Column("template_id", sa.String(length=36), nullable=True),
            sa.Column("item_key", sa.String(length=120), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=False),
            sa.Column("item_type", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source_document_id", sa.String(length=36), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
            sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"]),
            sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"]),
            sa.ForeignKeyConstraint(["template_id"], ["intake_templates.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "referral_id", "item_key", name="uq_intake_items_referral_key"),
        )
        op.create_index(op.f("ix_intake_checklist_items_patient_id"), "intake_checklist_items", ["patient_id"])
        op.create_index(op.f("ix_intake_checklist_items_referral_id"), "intake_checklist_items", ["referral_id"])
        op.create_index(op.f("ix_intake_checklist_items_status"), "intake_checklist_items", ["status"])
        op.create_index(op.f("ix_intake_checklist_items_template_id"), "intake_checklist_items", ["template_id"])
        op.create_index(op.f("ix_intake_checklist_items_tenant_id"), "intake_checklist_items", ["tenant_id"])

    if not inspector.has_table("questionnaire_responses"):
        op.create_table(
            "questionnaire_responses",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("patient_id", sa.String(length=36), nullable=True),
            sa.Column("referral_id", sa.String(length=36), nullable=True),
            sa.Column("template_id", sa.String(length=36), nullable=True),
            sa.Column("questionnaire_name", sa.String(length=160), nullable=False),
            sa.Column("answers", sa.JSON(), nullable=False),
            sa.Column("score_summary", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
            sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"]),
            sa.ForeignKeyConstraint(["template_id"], ["intake_templates.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_questionnaire_responses_patient_id"), "questionnaire_responses", ["patient_id"])
        op.create_index(op.f("ix_questionnaire_responses_referral_id"), "questionnaire_responses", ["referral_id"])
        op.create_index(op.f("ix_questionnaire_responses_status"), "questionnaire_responses", ["status"])
        op.create_index(op.f("ix_questionnaire_responses_template_id"), "questionnaire_responses", ["template_id"])
        op.create_index(op.f("ix_questionnaire_responses_tenant_id"), "questionnaire_responses", ["tenant_id"])

    if not inspector.has_table("therapist_prep_briefs"):
        op.create_table(
            "therapist_prep_briefs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("patient_id", sa.String(length=36), nullable=True),
            sa.Column("referral_id", sa.String(length=36), nullable=True),
            sa.Column("therapist_id", sa.String(length=36), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("source_summary", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
            sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["therapist_id"], ["therapists.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_therapist_prep_briefs_patient_id"), "therapist_prep_briefs", ["patient_id"])
        op.create_index(op.f("ix_therapist_prep_briefs_referral_id"), "therapist_prep_briefs", ["referral_id"])
        op.create_index(op.f("ix_therapist_prep_briefs_status"), "therapist_prep_briefs", ["status"])
        op.create_index(op.f("ix_therapist_prep_briefs_tenant_id"), "therapist_prep_briefs", ["tenant_id"])
        op.create_index(op.f("ix_therapist_prep_briefs_therapist_id"), "therapist_prep_briefs", ["therapist_id"])


def downgrade() -> None:
    for table in [
        "therapist_prep_briefs",
        "questionnaire_responses",
        "intake_checklist_items",
        "intake_templates",
    ]:
        if sa.inspect(op.get_bind()).has_table(table):
            op.drop_table(table)
