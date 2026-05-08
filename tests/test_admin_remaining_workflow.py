from __future__ import annotations

from sqlalchemy import select

from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import HumanReviewTask, Referral, Tenant
from backend.lumen_web.repositories import (
    apply_review_action,
    create_review_task,
    list_escalation_queue,
    reset_clean_demo_referral,
)


def test_escalation_queue_includes_escalated_task_and_referral() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-escalation-queue", name="Escalation Queue", slug="test-escalation-queue")
        session.add(tenant)
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral needing escalation.",
            status="needs_clinical_review",
            patient_name="Escalation Patient",
        )
        session.add(referral)
        session.flush()
        task = create_review_task(
            session,
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="clinical_risk_review",
            reason="Clinical review needs escalation.",
            payload_key="risk_review",
        )

        apply_review_action(session, task_id=task.id, action="escalate", rejection_reason="Needs director view.")
        queue = list_escalation_queue(session, tenant_id=tenant.id)

        assert referral.status == "clinical_escalation_review"
        assert queue
        assert queue[0]["task"]["id"] == task.id
        assert queue[0]["referral"]["id"] == referral.id
        assert "director" in queue[0]["reason"]
    finally:
        session.rollback()
        session.close()


def test_clean_demo_reset_is_idempotent_and_actionable() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        first = reset_clean_demo_referral(session)
        second = reset_clean_demo_referral(session)

        referral = second["referral"]
        open_missing_tasks = list(
            session.scalars(
                select(HumanReviewTask).where(
                    HumanReviewTask.referral_id == referral["id"],
                    HumanReviewTask.task_type == "admin_missing_info_review",
                    HumanReviewTask.status == "open",
                )
            )
        )

        assert first["referral"]["id"] == second["referral"]["id"]
        assert referral["status"] == "needs_admin_review"
        assert referral["contact_email"] == "lumenpatientdemo@gmail.com"
        assert referral["missing_fields"] == ["date_of_birth"]
        assert second["therapist"]["availability_blocks"]
        assert second["intake_template"]["required_items"]
        assert len(open_missing_tasks) == 1
    finally:
        session.rollback()
        session.close()
