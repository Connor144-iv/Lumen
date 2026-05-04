from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import app
from backend.lumen_web.db import SessionLocal
from backend.lumen_web.models import Appointment, ConsentRecord, HumanReviewTask, IntakeChecklistItem, Patient, Referral, Tenant, Therapist
from backend.lumen_web.repositories import referral_journey_dashboard


client = TestClient(app)


def test_referral_journey_groups_active_referrals_and_derives_blockers() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-journey", name="Journey Test", slug="test-journey")
        session.add(tenant)
        session.flush()
        patient = Patient(
            id="journey-patient",
            tenant_id=tenant.id,
            display_name="Journey Patient",
            contact_email="journey@example.com",
        )
        therapist = Therapist(
            id="journey-therapist",
            tenant_id=tenant.id,
            name="Journey Therapist",
            capacity_per_week=4,
            availability_blocks=[],
        )
        session.add_all([patient, therapist])
        session.flush()

        triage = Referral(
            id="journey-triage",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Missing email referral",
            status="new_referral",
            patient_name="Triage Patient",
            missing_fields=["contact_email"],
        )
        clinical = Referral(
            id="journey-clinical",
            tenant_id=tenant.id,
            patient_id=patient.id,
            source_channel="webform",
            raw_text="Elevated risk referral",
            status="needs_clinical_review",
            patient_name="Clinical Patient",
            risk_present=True,
            risk_category="moderate",
            urgency="elevated",
        )
        contact = Referral(
            id="journey-contact",
            tenant_id=tenant.id,
            patient_id=patient.id,
            source_channel="webform",
            raw_text="Patient contacted",
            status="contact_sent",
            patient_name="Contact Patient",
        )
        confirmation = Referral(
            id="journey-confirm",
            tenant_id=tenant.id,
            patient_id=patient.id,
            source_channel="webform",
            raw_text="Patient accepted a slot",
            status="awaiting_patient_reply",
            patient_name="Confirm Patient",
        )
        intake = Referral(
            id="journey-intake",
            tenant_id=tenant.id,
            patient_id=patient.id,
            source_channel="webform",
            raw_text="Intake outstanding",
            status="intake_incomplete",
            patient_name="Intake Patient",
        )
        conflict = Referral(
            id="journey-conflict",
            tenant_id=tenant.id,
            patient_id=patient.id,
            source_channel="webform",
            raw_text="Slot conflict",
            status="slot_options_ready",
            patient_name="Conflict Patient",
        )
        closed = Referral(
            id="journey-closed",
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Closed referral",
            status="closed_declined",
            patient_name="Closed Patient",
        )
        session.add_all([triage, clinical, contact, confirmation, intake, conflict, closed])
        session.flush()

        session.add_all(
            [
                HumanReviewTask(
                    tenant_id=tenant.id,
                    referral_id=clinical.id,
                    patient_id=patient.id,
                    task_type="clinical_risk_review",
                    status="open",
                    reason="Clinical review required.",
                    payload_key="risk_review",
                ),
                HumanReviewTask(
                    tenant_id=tenant.id,
                    referral_id=confirmation.id,
                    patient_id=patient.id,
                    task_type="appointment_confirmation_approval",
                    status="open",
                    reason="Patient accepted proposed slot.",
                    payload_key="appointment_confirmation:demo",
                ),
                IntakeChecklistItem(
                    tenant_id=tenant.id,
                    patient_id=patient.id,
                    referral_id=intake.id,
                    item_key="screening",
                    label="Screening",
                    item_type="questionnaire",
                    status="missing",
                ),
                ConsentRecord(
                    tenant_id=tenant.id,
                    patient_id=patient.id,
                    scope="privacy_notice",
                    status="waived",
                ),
            ]
        )
        starts_at = datetime.now(timezone.utc) + timedelta(days=2)
        session.add_all(
            [
                Appointment(
                    tenant_id=tenant.id,
                    patient_id=patient.id,
                    therapist_id=therapist.id,
                    referral_id=conflict.id,
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(minutes=50),
                    status="proposed",
                ),
                Appointment(
                    tenant_id=tenant.id,
                    patient_id=patient.id,
                    therapist_id=therapist.id,
                    referral_id=conflict.id,
                    starts_at=starts_at + timedelta(minutes=25),
                    ends_at=starts_at + timedelta(minutes=75),
                    status="confirmed",
                ),
            ]
        )
        session.flush()

        dashboard = referral_journey_dashboard(session, tenant_id=tenant.id)
        cards = {card["id"]: card for stage in dashboard["stages"] for card in stage["referrals"]}

        assert "journey-closed" not in cards
        assert cards["journey-triage"]["stage_id"] == "triage"
        assert cards["journey-contact"]["stage_id"] == "contact_scheduling"
        assert cards["journey-confirm"]["next_action"] == "confirm_appointment"
        assert "clinical_escalation" in cards["journey-clinical"]["blocker_codes"]
        assert "awaiting_patient_reply" in cards["journey-contact"]["blocker_codes"]
        assert "intake_incomplete" in cards["journey-intake"]["blocker_codes"]
        assert "intake_exception_recorded" in cards["journey-intake"]["blocker_codes"]
        assert "calendar_conflict" in cards["journey-conflict"]["blocker_codes"]
        assert dashboard["metrics"]["active_referrals"] == 6
        assert dashboard["metrics"]["blocked_referrals"] >= 5
    finally:
        session.rollback()
        session.close()


def test_referral_journey_endpoint_returns_dashboard_shape() -> None:
    response = client.get("/api/referral-journey")

    assert response.status_code == 200
    body = response.json()
    assert "metrics" in body
    assert "stages" in body
    assert [stage["id"] for stage in body["stages"]] == [
        "triage",
        "clinical",
        "matching",
        "contact_scheduling",
        "intake_prep",
        "ready",
    ]
