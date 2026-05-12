from __future__ import annotations

from backend.lumen_web.db import SessionLocal
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.lumen_web.models import AuditLog, Appointment, CommunicationDraft, HumanReviewTask, IntakeChecklistItem, Referral, Tenant, Therapist, TherapistPrepBrief, WorkflowEvent, WorkflowRun
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


def test_referral_detail_includes_workbench_advanced_trace_events_and_review_outputs() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-workbench-trace", name="Workbench Trace", slug="test-workbench-trace")
        referral = Referral(
            id="workbench-trace-referral",
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral with traceable agent activity.",
            status="awaiting_patient_contact",
            patient_name="Trace Workbench",
            contact_email="trace@example.com",
            insurer="Multicare",
        )
        session.add(tenant)
        session.flush()
        session.add(referral)
        session.flush()

        draft = CommunicationDraft(
            id="workbench-trace-draft",
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="Trace contact",
            body="Hello, here are next steps.",
            status="draft",
        )
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="send_approval",
            status="open",
            reason="Review patient-facing message.",
            payload_key=f"first_contact_draft:{draft.id[:8]}",
            source_payload={"id": draft.id},
            draft_text=draft.body,
        )
        run = WorkflowRun(
            id="workbench-trace-run",
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="needs_review",
            input_summary="Referral with traceable agent activity.",
            request_payload={"raw_input": {"source_channel": "webform"}},
            approvals={},
            result={"referral_id": referral.id, "next_action": "approve_contact"},
        )
        event = WorkflowEvent(
            tenant_id=tenant.id,
            workflow_run_id=run.id,
            index=1,
            type="agent",
            status="completed",
            message="Completeness extractor found no missing fields.",
            node="completeness_extractor",
            agent="Completeness Extractor",
            tools=["extract_referral_fields"],
            payload={"missing_fields": []},
        )
        session.add_all([draft, task, run, event])
        session.flush()

        detail = referral_detail(session, referral.id)
        state = detail["workbench_state"]
        trace = state["advanced_trace"]

        assert trace["workflow_runs"][0]["job_id"] == run.id
        assert trace["workflow_runs"][0]["result"]["next_action"] == "approve_contact"
        assert trace["workflow_runs"][0]["events"][0]["agent"] == "Completeness Extractor"
        assert trace["workflow_runs"][0]["events"][0]["node"] == "completeness_extractor"
        assert trace["workflow_runs"][0]["events"][0]["status"] == "completed"
        assert trace["workflow_runs"][0]["events"][0]["payload"] == {"missing_fields": []}
        assert detail["review_tasks"][0]["id"] == task.id
        assert state["agent_outputs"][0]["review_task_id"] == task.id
        assert state["agent_outputs"][0]["title"] == draft.subject
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


def test_email_workbench_started_intake_without_packet_prompts_packet_draft() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-email-packet-next", name="Email Packet Next", slug="test-email-packet-next")
        therapist = Therapist(id="email-packet-next-therapist", tenant_id=tenant.id, name="Packet Therapist")
        referral = Referral(
            id="email-packet-next-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral with intake started.",
            status="intake_incomplete",
            patient_name="Packet Draft Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=4)
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

        assert state["primary_action"] == "draft_intake_packet"
        assert state["primary_action_label"] == "Draft intake packet"
        assert state["allowed_actions"] == ["draft_intake_packet"]
        assert state["email_workflow"]["intake_packet_state"]["state"] == "not_drafted"
    finally:
        session.rollback()
        session.close()


def test_email_workbench_pending_intake_packet_draft_prompts_packet_review() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-email-packet-review", name="Email Packet Review", slug="test-email-packet-review")
        therapist = Therapist(id="email-packet-review-therapist", tenant_id=tenant.id, name="Packet Review Therapist")
        referral = Referral(
            id="email-packet-review-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral with pending intake packet.",
            status="intake_incomplete",
            patient_name="Packet Review Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=4)
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
        draft = CommunicationDraft(
            id="email-packet-review-draft",
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="Intake packet for your first session",
            body="Please reply to this same email thread with the completed files attached.",
            status="draft_pending_review",
        )
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="send_approval",
            status="open",
            reason="Intake packet requires staff approval before sending.",
            payload_key=f"intake_packet_draft:{draft.id[:8]}",
            source_payload={"id": draft.id},
            draft_text=draft.body,
        )
        session.add_all([draft, task])
        session.flush()

        state = referral_detail(session, referral.id)["workbench_state"]

        assert state["primary_action"] == "review_prepared_email"
        assert state["primary_action_label"] == "Review intake packet"
        assert state["allowed_actions"] == ["review_gate"]
        assert state["open_review_gate"]["id"] == task.id
        assert state["email_workflow"]["intake_packet_state"]["state"] == "draft_pending_review"
    finally:
        session.rollback()
        session.close()


def test_email_workbench_packet_sent_allows_missing_intake_completion_and_reminder() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-email-packet-sent", name="Email Packet Sent", slug="test-email-packet-sent")
        therapist = Therapist(id="email-packet-sent-therapist", tenant_id=tenant.id, name="Packet Sent Therapist")
        referral = Referral(
            id="email-packet-sent-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral with sent intake packet.",
            status="intake_incomplete",
            patient_name="Packet Sent Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=4)
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
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="Intake packet for your first session",
            body="Please reply to this same email thread with the completed files attached.",
            status="sent",
            gmail_message_id="packet-sent-message",
            gmail_thread_id="packet-sent-thread",
            sent_at=datetime.now(timezone.utc),
        )
        session.add(draft)
        session.flush()

        state = referral_detail(session, referral.id)["workbench_state"]

        assert state["primary_action"] == "complete_intake"
        assert state["primary_action_label"] == "Complete intake"
        assert "draft_intake_reminder" in state["allowed_actions"]
        assert state["email_workflow"]["intake_packet_state"]["state"] == "sent"
    finally:
        session.rollback()
        session.close()


def test_email_workbench_supersedes_premature_intake_reminder_gate() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-email-premature-reminder", name="Premature Reminder", slug="test-email-premature-reminder")
        therapist = Therapist(id="email-premature-reminder-therapist", tenant_id=tenant.id, name="Reminder Therapist")
        referral = Referral(
            id="email-premature-reminder-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral with premature reminder.",
            status="intake_incomplete",
            patient_name="Premature Reminder Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=4)
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
        draft = CommunicationDraft(
            id="email-premature-reminder-draft",
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="Reminder: intake items before your first session",
            body="Please send remaining intake items.",
            status="draft_pending_review",
        )
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="intake_reminder_approval",
            status="open",
            reason="Patient-facing intake reminder requires staff approval before sending.",
            payload_key="intake_reminder_draft",
            source_payload={"id": draft.id},
            draft_text=draft.body,
        )
        session.add_all([draft, task])
        session.flush()

        state = referral_detail(session, referral.id)["workbench_state"]

        assert state["primary_action"] == "draft_intake_packet"
        assert state["primary_action_label"] == "Draft intake packet"
        assert state["open_review_gate"] is None
        assert task.status == "superseded"
        assert draft.status == "superseded"
        actions = [row.action for row in session.scalars(select(AuditLog).where(AuditLog.tenant_id == tenant.id))]
        assert "review_superseded" in actions
        assert "draft_review_superseded" in actions
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


def test_workbench_ready_action_displays_referral_complete() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-ready-action", name="Ready Action", slug="test-ready-action")
        therapist = Therapist(id="ready-action-therapist", tenant_id=tenant.id, name="Ready Action Therapist")
        referral = Referral(
            id="ready-action-referral",
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral ready for completion.",
            status="first_session_ready",
            patient_name="Ready Action Patient",
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=3)
        session.add_all(
            [
                Appointment(
                    tenant_id=tenant.id,
                    referral_id=referral.id,
                    therapist_id=therapist.id,
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(hours=1),
                    status="confirmed",
                ),
                IntakeChecklistItem(
                    tenant_id=tenant.id,
                    referral_id=referral.id,
                    item_key="intake_form",
                    label="Intake form",
                    item_type="form",
                    status="completed",
                ),
                TherapistPrepBrief(
                    tenant_id=tenant.id,
                    referral_id=referral.id,
                    therapist_id=therapist.id,
                    title="Prep brief",
                    body="Ready notes.",
                ),
            ]
        )
        session.flush()

        state = referral_detail(session, referral.id)["workbench_state"]

        assert state["primary_action"] == "ready"
        assert state["primary_action_label"] == "Referral complete"
    finally:
        session.rollback()
        session.close()
