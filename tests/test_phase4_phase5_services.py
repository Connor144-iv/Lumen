from __future__ import annotations

from backend.lumen_web.db import SessionLocal
from backend.lumen_web.models import IntakeTemplate, Referral, Tenant, Therapist
from backend.lumen_web.repositories import (
    create_referral_document,
    generate_missing_intake_reminder,
    generate_prep_brief,
    propose_appointment_slots,
    save_questionnaire_response,
    start_intake_for_referral,
    deterministic_match_for_referral,
)


def test_phase4_matching_and_phase5_intake_services() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-phase45", name="Phase 45 Test", slug="test-phase45")
        session.add(tenant)
        session.flush()
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Test Therapist",
            specialties=["anxiety", "work stress"],
            age_groups=["adult"],
            languages=["Portuguese"],
            modalities=["online"],
            insurers=["Multicare"],
            capacity_per_week=4,
            availability_blocks=[{"weekday": "Tuesday", "start": "10:00", "end": "13:00", "modality": "online"}],
        )
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Adult referral for anxiety and work stress. Insurer Multicare. Portuguese. Online.",
            status="ready_for_matching",
            patient_name="Test Patient",
            contact_email="patient@example.com",
            insurer="Multicare",
            language_preference="Portuguese",
            modality_preference="online",
        )
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Test intake",
            required_items=[
                {"key": "privacy_notice", "label": "Privacy notice", "type": "consent", "consent_scope": "privacy_notice"},
                {"key": "screening", "label": "Screening", "type": "questionnaire"},
            ],
        )
        session.add_all([therapist, referral, template])
        session.flush()

        match = deterministic_match_for_referral(session, referral.id)
        assert match["ranked_matches"][0]["therapist_id"] == therapist.id

        proposals = propose_appointment_slots(session, referral.id, limit=1)
        assert len(proposals) == 1
        assert proposals[0]["status"] == "proposed"

        intake = start_intake_for_referral(session, referral.id)
        assert intake["status"] == "missing_items"
        assert len(intake["items"]) == 2
        assert len(intake["consents"]) == 1

        uploaded = create_referral_document(
            session,
            referral_id=referral.id,
            title="privacy-notice.txt",
            document_type="consent_document",
            storage_uri="storage/uploads/intake/test/privacy-notice.txt",
            metadata={"file_name": "privacy-notice.txt", "size_bytes": 12, "sha256": "abc"},
            item_id=intake["items"][0]["id"],
        )
        assert uploaded["document_type"] == "consent_document"

        reminder = generate_missing_intake_reminder(session, referral.id)
        assert reminder["status"] == "draft_pending_review"
        assert "draft" in reminder["body"].lower()

        questionnaire = save_questionnaire_response(
            session,
            referral.id,
            "generic_screening",
            {"mood": 1, "anxiety": 2},
        )
        assert questionnaire["score_summary"]["total_score"] == 3

        brief = generate_prep_brief(session, referral.id)
        assert "Therapist Prep Brief" in brief["body"]
    finally:
        session.rollback()
        session.close()
