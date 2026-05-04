from __future__ import annotations

from backend.lumen_web.db import SessionLocal
from backend.lumen_web.models import CommunicationDraft, HumanReviewTask, Referral, Tenant
from backend.lumen_web.repositories import referral_detail


def test_referral_detail_includes_task_aware_workbench_state() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-workbench-state", name="Workbench Test", slug="test-workbench-state")
        referral = Referral(
            id="workbench-clinical-referral",
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral with elevated clinical concern.",
            status="needs_clinical_review",
            patient_name="Clinical Workbench",
            risk_present=True,
            risk_category="moderate",
            urgency="elevated",
        )
        session.add(tenant)
        session.flush()
        session.add(referral)
        session.flush()
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="clinical_risk_review",
            status="open",
            reason="Clinical risk requires review before matching.",
            payload_key="clinical_risk_review:test",
        )
        session.add(task)
        session.flush()

        detail = referral_detail(session, referral.id)
        state = detail["workbench_state"]

        assert state["stage_id"] == "clinical"
        assert state["owner"] == "Clinician"
        assert state["primary_action"] == "review_gate"
        assert state["primary_action_label"] == "Complete clinical review"
        assert state["open_review_gate"]["id"] == task.id
        assert state["primary_blocker"]["code"] == "risk_review"
        assert any(item["title"] == "Human review opened: clinical risk review" for item in state["activity"])
    finally:
        session.rollback()
        session.close()


def test_changes_requested_agent_output_becomes_visible_next_action() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-workbench-changes", name="Workbench Changes", slug="test-workbench-changes")
        referral = Referral(
            id="workbench-contact-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral ready for first contact.",
            status="awaiting_patient_contact",
            patient_name="Contact Workbench",
            contact_email="contact@example.com",
            insurer="Multicare",
        )
        session.add(tenant)
        session.flush()
        session.add(referral)
        session.flush()
        draft = CommunicationDraft(
            id="workbench-contact-draft",
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="First contact",
            body="Hello, here are some slots.",
            status="changes_requested",
        )
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="send_approval",
            status="changes_requested",
            reason="Review patient-facing message.",
            payload_key=f"first_contact_draft:{draft.id[:8]}",
            source_payload={"id": draft.id},
            draft_text=draft.body,
            rejection_reason="Use a warmer opening and remove one unavailable slot.",
        )
        session.add_all([draft, task])
        session.flush()

        state = referral_detail(session, referral.id)["workbench_state"]

        assert state["primary_action"] == "revise_agent_output"
        assert state["primary_action_label"] == "Revise patient contact"
        assert state["allowed_actions"] == ["draft_first_contact"]
        assert state["primary_blocker"]["code"] == "changes_requested"
        assert state["changes_requested_gate"]["id"] == task.id
        assert state["agent_outputs"][0]["status"] == "changes_requested"
        assert state["agent_outputs"][0]["review_reason"] == task.rejection_reason
    finally:
        session.rollback()
        session.close()
