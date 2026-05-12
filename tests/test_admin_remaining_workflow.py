from __future__ import annotations

from sqlalchemy import select

from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import Appointment, CommunicationDraft, Document, HumanReviewTask, Patient, Referral, Tenant, Therapist
from backend.lumen_web.repositories import (
    DEMO_CLEAN_PATIENT_ID,
    DEMO_CLEAN_THERAPIST_ID,
    DEMO_CLARA_EMAIL,
    apply_review_action,
    create_review_task,
    list_escalation_queue,
    reset_clean_demo_referral,
)
from backend.lumen_web.seed import DEMO_TENANT_ID


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


def test_demo_reset_clears_gmail_inbox_clean_patient_and_clara_appointments() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = session.get(Tenant, DEMO_TENANT_ID)
        if tenant is None:
            tenant = Tenant(id=DEMO_TENANT_ID, name="Demo Clinic", slug="demo-clinic")
            session.add(tenant)
        session.flush()
        clean_patient = session.get(Patient, DEMO_CLEAN_PATIENT_ID)
        if clean_patient is None:
            clean_patient = Patient(id=DEMO_CLEAN_PATIENT_ID, tenant_id=tenant.id)
            session.add(clean_patient)
        clean_patient.display_name = "Retired Legacy Demo Patient"
        clean_patient.contact_email = "clean.demo.patient@example.com"
        gmail_referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Old Gmail route referral.",
            status="awaiting_patient_reply",
            patient_name="Gmail Demo Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        unrelated_referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Unrelated referral.",
            status="needs_admin_review",
            patient_name="Unrelated Patient",
        )
        clara = session.get(Therapist, DEMO_CLEAN_THERAPIST_ID)
        if clara is None:
            clara = Therapist(id=DEMO_CLEAN_THERAPIST_ID, tenant_id=tenant.id)
            session.add(clara)
        clara.name = "Dr. Clara Demo"
        clara.email = DEMO_CLARA_EMAIL
        clara.availability_blocks = []
        session.add_all([gmail_referral, unrelated_referral])
        session.flush()
        clara_appointment = Appointment(
            tenant_id=tenant.id,
            therapist_id=clara.id,
            referral_id=gmail_referral.id,
            starts_at=gmail_referral.created_at,
            ends_at=gmail_referral.created_at,
            status="proposed",
        )
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=unrelated_referral.id,
            channel="email",
            subject="Slots",
            body="Choose a slot.",
            status="draft_pending_review",
        )
        inbox_document = Document(
            tenant_id=tenant.id,
            document_type="inbound_email_unmatched",
            title="Old Gmail message",
            storage_uri="gmail:message:test-reset-message",
            metadata_json={"sender_email": "someone@example.com"},
        )
        keep_task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=unrelated_referral.id,
            task_type="clinical_risk_review",
            status="open",
            reason="Keep this task.",
            payload_key="keep",
        )
        session.add_all([clara_appointment, draft, inbox_document, keep_task])
        session.flush()
        draft.proposed_slots = [clara_appointment.id, "other-slot"]
        appointment_task = HumanReviewTask(
            tenant_id=tenant.id,
            task_type="appointment_confirmation_approval",
            status="open",
            reason="Old Clara appointment task.",
            payload_key="old-clara-appointment",
            source_payload={"appointment_id": clara_appointment.id},
        )
        session.add(appointment_task)
        session.flush()

        first = reset_clean_demo_referral(session, tenant_id=tenant.id)
        second = reset_clean_demo_referral(session, tenant_id=tenant.id)

        assert session.get(Patient, DEMO_CLEAN_PATIENT_ID) is None
        assert session.get(Referral, gmail_referral.id) is None
        assert session.scalar(
            select(Document).where(Document.tenant_id == tenant.id, Document.storage_uri.like("gmail:message:%"))
        ) is None
        assert session.scalar(select(Appointment).where(Appointment.therapist_id == DEMO_CLEAN_THERAPIST_ID)) is None
        session.refresh(draft)
        session.refresh(appointment_task)
        session.refresh(keep_task)
        assert draft.proposed_slots == ["other-slot"]
        assert appointment_task.status == "superseded"
        assert keep_task.status == "open"
        assert first["deleted_gmail_inbox_documents"] >= 1
        assert second["deleted_gmail_inbox_documents"] == 0
        assert second["deleted_clean_demo_patient"] == 0
        assert second["therapist"]["availability_blocks"]
        assert second["intake_template"]["source_channel"] == "email"
    finally:
        session.rollback()
        session.close()
