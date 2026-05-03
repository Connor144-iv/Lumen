"""admin referral state alignment

Revision ID: 20260503_0006
Revises: 20260427_0005
Create Date: 2026-05-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260503_0006"
down_revision = "20260427_0005"
branch_labels = None
depends_on = None


LEGACY_STATUS_MAP = {
    "new": "new_referral",
    "normalizing": "normalising",
    "match_pending_approval": "match_recommended",
    "outreach_draft_pending": "awaiting_patient_contact",
    "ready_to_contact": "awaiting_patient_contact",
    "contacted": "appointment_confirmed",
    "closed": "closed_not_suitable",
}

DOWN_STATUS_MAP = {
    "new_referral": "new",
    "normalising": "normalizing",
    "match_recommended": "match_pending_approval",
    "awaiting_patient_contact": "ready_to_contact",
    "appointment_confirmed": "contacted",
    "closed_not_suitable": "closed",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("human_review_tasks"):
        with op.batch_alter_table("human_review_tasks") as batch:
            batch.alter_column("workflow_run_id", existing_type=sa.String(length=36), nullable=True)

    if inspector.has_table("referrals"):
        for legacy, canonical in LEGACY_STATUS_MAP.items():
            op.execute(
                sa.text("UPDATE referrals SET status = :canonical WHERE status = :legacy").bindparams(
                    canonical=canonical,
                    legacy=legacy,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("referrals"):
        for canonical, legacy in DOWN_STATUS_MAP.items():
            op.execute(
                sa.text("UPDATE referrals SET status = :legacy WHERE status = :canonical").bindparams(
                    canonical=canonical,
                    legacy=legacy,
                )
            )

    if inspector.has_table("human_review_tasks"):
        with op.batch_alter_table("human_review_tasks") as batch:
            batch.alter_column("workflow_run_id", existing_type=sa.String(length=36), nullable=False)
