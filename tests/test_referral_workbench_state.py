from __future__ import annotations

from backend.lumen_web.db import SessionLocal
from datetime import datetime, timedelta, timezone

from backend.lumen_web.models import Appointment, CommunicationDraft, HumanReviewTask, IntakeChecklistItem, Referral, Tenant, Therapist, TherapistPrepBrief
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
        assert any(item["title"] == "Review opened: clinical risk review" for item in state["activity"])
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

        assert state["primary_action"] == "review_prepared_email"
        assert state["primary_action_label"] == "Review prepared email"
        assert state["allowed_actions"] == ["review_gate"]
        assert state["primary_blocker"]["code"] == "changes_requested"
        assert state["changes_requested_gate"]["id"] == task.id
        assert state["agent_outputs"][0]["status"] == "changes_requested"
        assert state["agent_outputs"][0]["review_reason"] == task.rejection_reason
    finally:
        session.rollback()
        session.close()


def test_email_workbench_duplicate_blocker_overrides_sync_replies() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-email-sync-blocker", name="Email Sync Blocker", slug="test-email-sync-blocker")
        therapist = Therapist(id="email-sync-therapist", tenant_id=tenant.id, name="Email Sync Therapist")
        referral = Referral(
            id="email-sync-blocked-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral with stale duplicate blocker.",
            status="needs_admin_review",
            patient_name="Anna Anderson",
            contact_email="lumenpatientdemo@gmail.com",
            insurer="Mayhek",
            duplicate_candidates=["not-a-real-referral"],
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        appointment = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            starts_at=datetime.now(timezone.utc) + timedelta(days=3),
            ends_at=datetime.now(timezone.utc) + timedelta(days=3, hours=1),
            status="proposed",
        )
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="Appointment option",
            body="Option 1 works?",
            status="sent",
            proposed_slots=[],
            gmail_thread_id="thread-sync-blocked",
            gmail_message_id="message-sync-blocked",
        )
        session.add_all([appointment, draft])
        session.flush()
        draft.proposed_slots = [appointment.id]
        session.flush()

        state = referral_detail(session, referral.id)["workbench_state"]

        assert state["primary_action"] == "review_missing_info"
        assert state["primary_action_label"] == "Resolve duplicate candidate"
        assert state["email_workflow"]["next_action"] == "review_missing_info"
        assert state["primary_blocker"]["code"] == "duplicate_candidate"
    finally:
        session.rollback()
        session.close()


def test_email_workbench_treats_phone_as_optional_after_dob_and_email() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-email-phone-optional", name="Email Phone Optional", slug="test-email-phone-optional")
        therapist = Therapist(id="email-phone-therapist", tenant_id=tenant.id, name="Email Phone Therapist")
        referral = Referral(
            id="email-phone-optional-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral with email and DOB but no phone.",
            status="contact_sent",
            patient_name="Henrik Anderson",
            date_of_birth="1990-07-12",
            contact_email="lumenpatientdemo@gmail.com",
            insurer="Pancakes",
            referring_entity="Health Service",
            missing_fields=["contact_phone"],
            match_summary={"ranked_matches": [{"therapist_id": therapist.id, "name": therapist.name}]},
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        appointment = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            starts_at=datetime.now(timezone.utc) + timedelta(days=3),
            ends_at=datetime.now(timezone.utc) + timedelta(days=3, hours=1),
            status="proposed",
        )
        session.add(appointment)
        session.flush()
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="Appointment option",
            body="Option 1 works?",
            status="sent",
            proposed_slots=[appointment.id],
            gmail_thread_id="thread-phone-optional",
            gmail_message_id="message-phone-optional",
        )
        stale_task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="admin_missing_info_review",
            status="open",
            reason="Referral has missing phone.",
            payload_key="missing_information",
            source_payload={"missing_fields": ["contact_phone"]},
        )
        session.add_all([draft, stale_task])
        session.flush()

        state = referral_detail(session, referral.id)["workbench_state"]
        session.refresh(referral)
        session.refresh(stale_task)

        assert referral.missing_fields == []
        assert stale_task.status == "completed"
        assert state["primary_action"] == "sync_replies"
        assert state["primary_action_label"] == "Sync replies"
        assert state["primary_blocker"]["code"] == "awaiting_patient_reply"
        assert state["email_workflow"]["next_action"] == "sync_replies"
        assert state["email_workflow"]["review_tasks"] == []
    finally:
        session.rollback()
        session.close()


def test_email_workbench_shows_appointment_confirmation_gate_before_missing_info() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-email-appt-gate", name="Email Appointment Gate", slug="test-email-appt-gate")
        therapist = Therapist(id="email-appt-gate-therapist", tenant_id=tenant.id, name="Email Appointment Therapist")
        referral = Referral(
            id="email-appt-gate-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral with accepted slot.",
            status="awaiting_patient_reply",
            patient_name="Henrik Anderson",
            date_of_birth="1990-07-12",
            contact_email="lumenpatientdemo@gmail.com",
            insurer="Pancakes",
            referring_entity="Health Service",
            missing_fields=["contact_phone"],
            match_summary={"ranked_matches": [{"therapist_id": therapist.id, "name": therapist.name}]},
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        appointment = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            starts_at=datetime.now(timezone.utc) + timedelta(days=4),
            ends_at=datetime.now(timezone.utc) + timedelta(days=4, hours=1),
            status="proposed",
        )
        session.add(appointment)
        session.flush()
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="appointment_confirmation_approval",
            status="open",
            reason="Patient accepted a proposed slot.",
            payload_key=f"appointment_confirmation:{appointment.id[:8]}",
            source_payload={"appointment_id": appointment.id},
        )
        session.add(task)
        session.flush()

        state = referral_detail(session, referral.id)["workbench_state"]

        assert state["primary_action"] == "confirm_appointment"
        assert state["primary_action_label"] == "Create Google Calendar event"
        assert state["email_workflow"]["next_action"] == "confirm_appointment"
        assert [item["id"] for item in state["email_workflow"]["review_tasks"]] == [task.id]
        assert all(blocker["code"] != "missing_info" for blocker in state["blockers"])
    finally:
        session.rollback()
        session.close()


def test_email_workbench_confirmed_appointment_starts_intake_even_with_optional_phone_missing() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-email-confirmed-intake", name="Email Confirmed Intake", slug="test-email-confirmed-intake")
        therapist = Therapist(id="email-confirmed-intake-therapist", tenant_id=tenant.id, name="Email Confirmed Therapist")
        referral = Referral(
            id="email-confirmed-intake-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral with confirmed appointment.",
            status="appointment_confirmed",
            patient_name="Henrik Anderson",
            date_of_birth="1990-07-12",
            contact_email="lumenpatientdemo@gmail.com",
            insurer="Pancakes",
            referring_entity="Health Service",
            missing_fields=["contact_phone"],
            match_summary={"ranked_matches": [{"therapist_id": therapist.id, "name": therapist.name}]},
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=5)
        session.add(
            Appointment(
                tenant_id=tenant.id,
                referral_id=referral.id,
                therapist_id=therapist.id,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=1),
                status="confirmed",
            )
        )
        session.flush()

        state = referral_detail(session, referral.id)["workbench_state"]

        assert state["primary_action"] == "start_intake"
        assert state["primary_action_label"] == "Start intake"
        assert state["email_workflow"]["next_action"] == "start_intake"
        assert state["email_workflow"]["facts"]["missing_fields"] == []
    finally:
        session.rollback()
        session.close()


def test_email_workbench_prioritizes_open_intake_submission_review() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-email-intake-review", name="Email Intake Review", slug="test-email-intake-review")
        referral = Referral(
            id="email-intake-review-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral with returned intake attachments.",
            status="intake_incomplete",
            patient_name="Returned Intake Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        session.add(tenant)
        session.flush()
        session.add(referral)
        session.flush()
        session.add(
            CommunicationDraft(
                tenant_id=tenant.id,
                referral_id=referral.id,
                channel="email",
                subject="Intake packet",
                body="Please reply to this same email thread with completed files.",
                status="sent",
                gmail_thread_id="thread-intake-review-workbench",
                gmail_message_id="sent-intake-review-workbench",
            )
        )
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="intake_submission_review",
            status="open",
            reason="Patient returned an intake attachment that needs mapping.",
            payload_key="intake_submission:test",
            source_payload={
                "received_attachment_filenames": ["clinical_intake_form_completed.docx"],
                "missing_intake_items": [{"label": "Clinical intake form"}],
                "missing_consents": [],
            },
        )
        session.add(task)
        session.flush()

        state = referral_detail(session, referral.id)["workbench_state"]

        assert state["stage_id"] == "intake_prep"
        assert state["primary_action"] == "complete_intake"
        assert state["primary_action_label"] == "Review intake submission"
        assert state["open_review_gate"]["id"] == task.id
        assert [item["id"] for item in state["email_workflow"]["review_tasks"]] == [task.id]
        assert state["email_workflow"]["next_action_label"] == "Review intake submission"
    finally:
        session.rollback()
        session.close()


def test_workbench_progress_uses_underlying_facts_not_status_labels() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-workbench-progress", name="Workbench Progress", slug="test-workbench-progress")
        session.add(tenant)
        session.flush()
        therapist = Therapist(id="progress-therapist", tenant_id=tenant.id, name="Progress Therapist")
        referral = Referral(
            id="workbench-progress-referral",
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral whose status should not imply completed facts.",
            status="first_session_ready",
            patient_name="Progress Patient",
        )
        session.add_all([therapist, referral])
        session.flush()

        progress = referral_detail(session, referral.id)["workbench_state"]["progress"]

        assert progress["contacted"] is False
        assert progress["appointment_confirmed"] is False
        assert progress["intake_complete"] is False
        assert progress["prep_brief_ready"] is False
        assert progress["first_session_ready"] is False

        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="First appointment options",
            body="Please choose a slot.",
            status="sent",
            proposed_slots=["slot-1"],
        )
        starts_at = datetime.now(timezone.utc) + timedelta(days=2)
        appointment = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=60),
            status="confirmed",
        )
        item = IntakeChecklistItem(
            tenant_id=tenant.id,
            referral_id=referral.id,
            item_key="intake_form",
            label="Intake form",
            item_type="form",
            status="completed",
        )
        brief = TherapistPrepBrief(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            title="Prep brief",
            body="Prep notes.",
        )
        session.add_all([draft, appointment, item, brief])
        session.flush()

        progress = referral_detail(session, referral.id)["workbench_state"]["progress"]

        assert progress["contacted"] is True
        assert progress["appointment_confirmed"] is True
        assert progress["intake_complete"] is True
        assert progress["prep_brief_ready"] is True
        assert progress["first_session_ready"] is True
    finally:
        session.rollback()
        session.close()
