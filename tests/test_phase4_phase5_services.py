from __future__ import annotations

import pytest

from backend.lumen_web.db import SessionLocal
from backend.lumen_web.models import Appointment, CommunicationDraft, HumanReviewTask, IntakeTemplate, Referral, Tenant, Therapist
from backend.lumen_web.repositories import (
    apply_review_action,
    create_clinical_escalation_review,
    create_duplicate_resolution_review,
    create_referral_document,
    create_suitability_review,
    draft_first_contact_message,
    draft_intake_packet,
    draft_missing_info_request,
    generate_missing_intake_reminder,
    generate_prep_brief,
    intake_workspace,
    list_intake_tracker,
    propose_appointment_slots,
    record_missing_info_reply,
    record_simulated_patient_reply,
    request_consent_exception,
    request_intake_item_exception,
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
                {"key": "supporting_doc", "label": "Supporting document", "type": "document"},
                {"key": "screening", "label": "Screening", "type": "questionnaire"},
                {"key": "id_document", "label": "ID document", "type": "document"},
            ],
        )
        session.add_all([therapist, referral, template])
        session.flush()

        missing_referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral missing contact details.",
            status="new_referral",
            patient_name="Missing Info Patient",
            missing_fields=["contact_email", "insurer"],
        )
        session.add(missing_referral)
        session.flush()
        draft = draft_missing_info_request(session, missing_referral.id, note="Ask for insurer and email.")
        assert draft["status"] == "draft_pending_review"
        assert session.query(HumanReviewTask).filter_by(
            referral_id=missing_referral.id,
            task_type="admin_missing_info_review",
        ).one()
        missing_message_task = session.query(HumanReviewTask).filter_by(
            referral_id=missing_referral.id,
            task_type="missing_info_message_approval",
        ).one()
        apply_review_action(session, task_id=missing_message_task.id, action="approve", final_text="Please send insurer and email.")
        assert missing_referral.status == "waiting_for_missing_info"
        assert session.get(CommunicationDraft, draft["id"]).status == "approved_pending_send"
        missing_reply = record_missing_info_reply(
            session,
            missing_referral.id,
            source="patient",
            updates={"contact_email": "missing@example.com", "insurer": "Multicare"},
            notes="Patient replied with missing fields.",
        )
        assert missing_reply["reply"]["document_type"] == "missing_info_reply"
        assert missing_referral.status == "ready_for_matching"

        duplicate_referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Possible duplicate referral. Insurer Multicare. Portuguese. Online.",
            status="needs_admin_review",
            patient_name="Duplicate Patient",
            contact_email="duplicate@example.com",
            insurer="Multicare",
            language_preference="Portuguese",
            modality_preference="online",
            duplicate_candidates=[referral.id],
        )
        session.add(duplicate_referral)
        session.flush()
        with pytest.raises(ValueError, match="Duplicate candidates"):
            deterministic_match_for_referral(session, duplicate_referral.id)
        duplicate_task = create_duplicate_resolution_review(session, duplicate_referral.id)
        apply_review_action(session, task_id=duplicate_task.id, action="approve")
        assert duplicate_referral.duplicate_candidates == []
        assert duplicate_referral.status == "ready_for_matching"

        risk_referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Adult referral with elevated risk signal. Insurer Multicare. Portuguese. Online.",
            status="needs_clinical_review",
            patient_name="Risk Patient",
            contact_email="risk@example.com",
            insurer="Multicare",
            language_preference="Portuguese",
            modality_preference="online",
            risk_present=True,
            risk_category="moderate",
            urgency="elevated",
        )
        session.add(risk_referral)
        session.flush()
        with pytest.raises(ValueError, match="Clinical review must be resolved"):
            deterministic_match_for_referral(session, risk_referral.id)
        clinical_task = create_clinical_escalation_review(session, risk_referral.id, reason="Director reviewed elevated risk.")
        apply_review_action(session, task_id=clinical_task.id, action="approve")
        assert risk_referral.status == "ready_for_matching"
        assert deterministic_match_for_referral(session, risk_referral.id)["ranked_matches"]

        suitability_referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Adult referral needing suitability review. Insurer Multicare. Portuguese. Online.",
            status="needs_clinical_review",
            patient_name="Suitability Patient",
            contact_email="suitability@example.com",
            insurer="Multicare",
            language_preference="Portuguese",
            modality_preference="online",
        )
        session.add(suitability_referral)
        session.flush()
        suitability_task = create_suitability_review(session, suitability_referral.id, reason="Confirm service fit.")
        apply_review_action(session, task_id=suitability_task.id, action="reject", rejection_reason="Out of clinic scope.")
        assert suitability_referral.status == "closed_not_suitable"

        match = deterministic_match_for_referral(session, referral.id)
        assert match["ranked_matches"][0]["therapist_id"] == therapist.id
        assert referral.status == "match_recommended"
        match_task = session.query(HumanReviewTask).filter_by(referral_id=referral.id, task_type="match_approval").one()
        apply_review_action(session, task_id=match_task.id, action="approve")
        assert referral.status == "match_approved"

        proposals = propose_appointment_slots(session, referral.id, limit=1)
        assert len(proposals) == 1
        assert proposals[0]["status"] == "proposed"
        assert referral.status == "slot_options_ready"
        slot_task = session.query(HumanReviewTask).filter_by(referral_id=referral.id, task_type="slot_offer_approval").one()
        apply_review_action(session, task_id=slot_task.id, action="approve")
        assert referral.status == "awaiting_patient_contact"

        contact = draft_first_contact_message(session, referral.id, note="Use simulated send.")
        contact_task = session.query(HumanReviewTask).filter_by(
            referral_id=referral.id,
            task_type="send_approval",
            payload_key=f"first_contact_draft:{contact['id'][:8]}",
        ).one()
        apply_review_action(session, task_id=contact_task.id, action="approve")
        assert session.get(CommunicationDraft, contact["id"]).status == "approved_pending_send"
        assert referral.status == "contact_sent"

        reply = record_simulated_patient_reply(
            session,
            referral.id,
            reply_type="accepted_slot",
            appointment_id=proposals[0]["id"],
        )
        assert reply["task"]["task_type"] == "appointment_confirmation_approval"
        apply_review_action(session, task_id=reply["task"]["id"], action="approve")
        assert referral.status == "appointment_confirmed"
        assert session.get(Appointment, proposals[0]["id"]).status == "confirmed"

        packet = draft_intake_packet(session, referral.id, note="Send forms manually after approval.")
        packet_task = session.query(HumanReviewTask).filter_by(
            referral_id=referral.id,
            task_type="send_approval",
            payload_key=f"intake_packet_draft:{packet['id'][:8]}",
        ).one()
        apply_review_action(session, task_id=packet_task.id, action="approve")
        assert session.get(CommunicationDraft, packet["id"]).status == "approved_pending_send"
        assert referral.status == "intake_packet_sent"

        intake = intake_workspace(session, referral.id)
        assert intake["status"] == "missing_items"
        assert len(intake["items"]) == 4
        assert len(intake["consents"]) == 1

        consent_exception = request_consent_exception(session, intake["consents"][0]["id"], reason="Consent obtained outside Lumen.")
        apply_review_action(session, task_id=consent_exception.id, action="approve")

        supporting_doc = next(item for item in intake["items"] if item["item_key"] == "supporting_doc")

        uploaded = create_referral_document(
            session,
            referral_id=referral.id,
            title="supporting-doc.txt",
            document_type="intake_document",
            storage_uri="storage/uploads/intake/test/supporting-doc.txt",
            metadata={"file_name": "privacy-notice.txt", "size_bytes": 12, "sha256": "abc"},
            item_id=supporting_doc["id"],
        )
        assert uploaded["document_type"] == "intake_document"

        reminder = generate_missing_intake_reminder(session, referral.id)
        assert reminder["status"] == "draft_pending_review"
        assert "draft" in reminder["body"].lower()
        reminder_task = session.query(HumanReviewTask).filter_by(
            referral_id=referral.id,
            task_type="intake_reminder_approval",
        ).one()
        assert reminder_task.workflow_run_id is None

        questionnaire = save_questionnaire_response(
            session,
            referral.id,
            "generic_screening",
            {"mood": 1, "anxiety": 2},
        )
        assert questionnaire["score_summary"]["total_score"] == 3

        id_item = session.query(HumanReviewTask).filter_by(
            referral_id=referral.id,
            task_type="intake_exception_approval",
            payload_key=f"intake_exception_consent:{intake['consents'][0]['id'][:8]}",
        ).one()
        assert id_item.status == "approved"
        id_document = next(item for item in intake["items"] if item["item_key"] == "id_document")
        item_exception = request_intake_item_exception(session, id_document["id"], reason="ID verified manually.")
        apply_review_action(session, task_id=item_exception.id, action="approve")
        assert referral.status == "intake_complete"

        brief = generate_prep_brief(session, referral.id)
        assert "Therapist Prep Brief" in brief["body"]
        assert referral.status == "first_session_ready"
        tracker = list_intake_tracker(session, tenant_id=tenant.id)
        assert any(row["referral"]["id"] == referral.id for row in tracker)
    finally:
        session.rollback()
        session.close()
