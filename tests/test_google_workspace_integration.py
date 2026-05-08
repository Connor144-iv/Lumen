from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from backend.lumen_web import google_workspace
from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import (
    AuditLog,
    Appointment,
    CommunicationDraft,
    HumanReviewTask,
    IntakeChecklistItem,
    IntakeTemplate,
    Referral,
    Tenant,
    Therapist,
    TherapistPrepBrief,
    WorkflowRun,
)
from backend.lumen_web.repositories import (
    apply_review_action,
    draft_missing_info_request,
    finish_workflow_run,
    generate_missing_intake_reminder,
    generate_prep_brief,
    propose_appointment_slots,
    referral_detail,
    request_appointment_reschedule,
    start_intake_for_referral,
    therapist_calendar_capacity,
)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def test_google_status_shape_without_real_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("LUMEN_GOOGLE_TOKEN_PATH", str(tmp_path / "missing-token.json"))
    monkeypatch.setenv("LUMEN_GOOGLE_CLIENT_SECRET_PATH", str(tmp_path / "client-secret.json"))

    status = google_workspace.google_workspace_status(refresh=False)

    assert status["enabled"] is True
    assert status["authorized"] is False
    assert status["token_present"] is False
    assert status["configured_scopes"] == ["gmail.send", "calendar.readonly", "calendar.events", "gmail.modify"]
    assert status["calendar_id"] == "primary"


def test_google_dependency_status_reports_specific_missing_package(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("LUMEN_GOOGLE_TOKEN_PATH", str(tmp_path / "missing-token.json"))
    monkeypatch.setenv("LUMEN_GOOGLE_CLIENT_SECRET_PATH", str(tmp_path / "client-secret.json"))
    def fake_import(module_name):
        if module_name == "googleapiclient.discovery":
            raise ModuleNotFoundError("No module named 'googleapiclient'", name="googleapiclient")
        return _fake_google_import(module_name)

    monkeypatch.setattr(google_workspace.importlib, "import_module", fake_import)

    status = google_workspace.google_workspace_status(refresh=False)

    assert status["dependencies_available"] is False
    assert "Missing Google API dependency" in status["last_provider_error"]
    assert "google-api-python-client" in status["last_provider_error"]


def test_google_import_status_preserves_non_dependency_import_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("LUMEN_GOOGLE_TOKEN_PATH", str(tmp_path / "missing-token.json"))
    monkeypatch.setenv("LUMEN_GOOGLE_CLIENT_SECRET_PATH", str(tmp_path / "client-secret.json"))
    def fake_import(module_name):
        if module_name == "googleapiclient.discovery":
            raise ImportError("cannot import name 'build' from partially initialized module")
        return _fake_google_import(module_name)

    monkeypatch.setattr(google_workspace.importlib, "import_module", fake_import)

    status = google_workspace.google_workspace_status(refresh=False)

    assert status["dependencies_available"] is False
    assert "Google API import failed while loading 'googleapiclient.discovery'" in status["last_provider_error"]
    assert "partially initialized module" in status["last_provider_error"]
    assert "dependencies are not installed" not in status["last_provider_error"]


def test_google_status_clears_stale_provider_error_after_current_dependency_check(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("LUMEN_GOOGLE_TOKEN_PATH", str(tmp_path / "missing-token.json"))
    monkeypatch.setenv("LUMEN_GOOGLE_CLIENT_SECRET_PATH", str(tmp_path / "client-secret.json"))
    monkeypatch.setattr(google_workspace.importlib, "import_module", _fake_google_import)
    monkeypatch.setattr(google_workspace, "_LAST_PROVIDER_ERROR", "stale dependency error")

    status = google_workspace.google_workspace_status(refresh=False)

    assert status["dependencies_available"] is True
    assert status["token_present"] is False
    assert status["last_provider_error"] is None


def _fake_google_import(module_name: str):
    if module_name == "google.auth.transport.requests":
        return SimpleNamespace(Request=object)
    if module_name == "google.oauth2.credentials":
        return SimpleNamespace(Credentials=object)
    if module_name == "googleapiclient.discovery":
        return SimpleNamespace(build=lambda *args, **kwargs: None)
    if module_name in {"google_auth_httplib2", "google_auth_oauthlib.flow"}:
        return SimpleNamespace()
    raise ModuleNotFoundError(f"No module named {module_name!r}", name=module_name)


def test_workflow_contact_draft_creates_linked_send_approval(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.delenv("LUMEN_OUTBOUND_PATIENT_EMAIL_OVERRIDE", raising=False)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Workflow Send Link Test", slug=_id("workflow-send"))
        session.add(tenant)
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral ready for contact.",
            status="normalising",
            patient_name="Workflow Patient",
            contact_email="patient@example.com",
        )
        session.add(referral)
        session.flush()
        run = WorkflowRun(
            id=_id("workflow"),
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="running",
            input_summary="Workflow contact draft",
            request_payload={"raw_input": {}},
            approvals={},
        )
        session.add(run)
        session.flush()

        finish_workflow_run(
            session,
            job_id=run.id,
            status="needs_review",
            result={
                "outputs": {
                    "referral": {"patient_name": "Workflow Patient", "contact_email": "patient@example.com"},
                    "communication_draft": {
                        "channel": "email",
                        "subject": "Appointment options",
                        "body": "Please choose a slot.",
                        "requires_human_send": True,
                    },
                },
                "human_review_queue": [
                    {
                        "gate": "send_approval",
                        "payload_key": "communication_draft",
                        "reason": "Approve patient contact.",
                    }
                ],
            },
            error=None,
        )

        draft = session.scalar(select(CommunicationDraft).where(CommunicationDraft.workflow_run_id == run.id))
        task = session.scalar(select(HumanReviewTask).where(HumanReviewTask.workflow_run_id == run.id, HumanReviewTask.task_type == "send_approval"))

        assert draft is not None
        assert task is not None
        assert draft.referral_id == referral.id
        assert draft.channel == "email"
        assert draft.subject == "Appointment options"
        assert draft.body == "Please choose a slot."
        assert draft.status == "draft_pending_review"
        assert draft.recipient_email == "lumenpatientdemo@gmail.com"
        assert task.source_payload["id"] == draft.id
        assert task.source_payload["referral_id"] == referral.id
        assert task.source_payload["recipient_email"] == "lumenpatientdemo@gmail.com"
    finally:
        session.rollback()
        session.close()


def test_approved_draft_sends_once_and_progresses_referral(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    monkeypatch.delenv("LUMEN_OUTBOUND_PATIENT_EMAIL_OVERRIDE", raising=False)
    send_calls = []

    def fake_send(**kwargs):
        send_calls.append(kwargs)
        return {"message_id": "gmail-message-1", "thread_id": "gmail-thread-1"}

    monkeypatch.setattr(google_workspace, "send_approved_draft", fake_send)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Google Send Test", slug=_id("google-send"))
        session.add(tenant)
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral ready for contact.",
            status="awaiting_patient_contact",
            patient_name="Google Patient",
            contact_email="patient@example.com",
        )
        session.add(referral)
        session.flush()
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="Appointment options",
            body="Please choose a slot.",
            status="draft_pending_review",
            recipient_email="patient@example.com",
        )
        session.add(draft)
        session.flush()
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="send_approval",
            status="open",
            reason="Approve contact.",
            payload_key=f"first_contact_draft:{draft.id[:8]}",
            source_payload={"id": draft.id},
            draft_text=draft.body,
        )
        session.add(task)
        session.flush()

        apply_review_action(session, task_id=task.id, action="approve", final_text="Approved body")
        apply_review_action(session, task_id=task.id, action="approve", final_text="Approved body")

        assert len(send_calls) == 1
        assert send_calls[0]["recipient_email"] == "lumenpatientdemo@gmail.com"
        assert task.status == "approved"
        assert referral.status == "contact_sent"
        assert draft.status == "sent"
        assert draft.body == "Approved body"
        assert draft.gmail_message_id == "gmail-message-1"
        assert draft.gmail_thread_id == "gmail-thread-1"
        assert draft.last_provider_error is None
        session.flush()
        actions = [row.action for row in session.scalars(select(AuditLog).where(AuditLog.tenant_id == tenant.id))]
        assert "provider_send" in actions
        assert "draft_review_approve" in actions
    finally:
        session.rollback()
        session.close()


def test_missing_info_approval_sends_with_gmail_before_waiting_for_reply(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    monkeypatch.delenv("LUMEN_OUTBOUND_PATIENT_EMAIL_OVERRIDE", raising=False)
    send_calls = []

    def fake_send(**kwargs):
        send_calls.append(kwargs)
        return {"message_id": "gmail-missing-info-1", "thread_id": "gmail-thread-1"}

    monkeypatch.setattr(google_workspace, "send_approved_draft", fake_send)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Missing Info Gmail", slug=_id("missing-gmail"))
        session.add(tenant)
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral missing DOB.",
            status="needs_admin_review",
            patient_name="Missing Gmail Patient",
            missing_fields=["date_of_birth"],
        )
        session.add(referral)
        session.flush()

        draft = draft_missing_info_request(session, referral.id, note="Please confirm DOB.")
        task = session.scalar(
            select(HumanReviewTask).where(
                HumanReviewTask.referral_id == referral.id,
                HumanReviewTask.task_type == "missing_info_message_approval",
            )
        )

        apply_review_action(session, task_id=task.id, action="approve", final_text="Please confirm DOB.")

        draft_row = session.get(CommunicationDraft, draft["id"])
        assert len(send_calls) == 1
        assert send_calls[0]["recipient_email"] == "lumenpatientdemo@gmail.com"
        assert task.status == "approved"
        assert draft_row.status == "sent"
        assert draft_row.gmail_message_id == "gmail-missing-info-1"
        assert referral.status == "waiting_for_missing_info"
    finally:
        session.rollback()
        session.close()


def test_gmail_failure_leaves_review_gate_open(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    monkeypatch.delenv("LUMEN_OUTBOUND_PATIENT_EMAIL_OVERRIDE", raising=False)

    def fake_send(**kwargs):
        raise google_workspace.GoogleWorkspaceError("simulated Gmail outage")

    monkeypatch.setattr(google_workspace, "send_approved_draft", fake_send)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Google Failure Test", slug=_id("google-failure"))
        session.add(tenant)
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral ready for contact.",
            status="awaiting_patient_contact",
            patient_name="Google Patient",
            contact_email="patient@example.com",
        )
        session.add(referral)
        session.flush()
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="Appointment options",
            body="Please choose a slot.",
            status="draft_pending_review",
            recipient_email="patient@example.com",
        )
        session.add(draft)
        session.flush()
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="send_approval",
            status="open",
            reason="Approve contact.",
            payload_key=f"first_contact_draft:{draft.id[:8]}",
            source_payload={"id": draft.id},
            draft_text=draft.body,
        )
        session.add(task)
        session.flush()

        apply_review_action(session, task_id=task.id, action="approve")

        assert task.status == "open"
        assert referral.status == "awaiting_patient_contact"
        assert draft.status == "draft_pending_review"
        assert draft.gmail_message_id is None
        assert draft.sent_at is None
        assert "simulated Gmail outage" in (draft.last_provider_error or "")
        assert "provider_error" in task.source_payload
        session.flush()
        actions = [row.action for row in session.scalars(select(AuditLog).where(AuditLog.tenant_id == tenant.id))]
        assert "provider_failure" in actions
    finally:
        session.rollback()
        session.close()


def test_intake_reminder_gmail_failure_keeps_gate_open_and_draft_unsent(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")

    def fake_send(**kwargs):
        raise google_workspace.GoogleWorkspaceError("simulated reminder Gmail outage")

    monkeypatch.setattr(google_workspace, "send_approved_draft", fake_send)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Reminder Failure", slug=_id("reminder-failure"))
        session.add(tenant)
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral with intake outstanding.",
            status="intake_incomplete",
            patient_name="Reminder Patient",
        )
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Reminder template",
            required_items=[{"key": "intake_form", "label": "Intake form", "type": "form"}],
        )
        session.add_all([referral, template])
        session.flush()
        start_intake_for_referral(session, referral.id)
        draft = generate_missing_intake_reminder(session, referral.id)
        task = session.scalar(
            select(HumanReviewTask).where(
                HumanReviewTask.referral_id == referral.id,
                HumanReviewTask.task_type == "intake_reminder_approval",
            )
        )

        apply_review_action(session, task_id=task.id, action="approve")

        draft_row = session.get(CommunicationDraft, draft["id"])
        assert task.status == "open"
        assert draft_row.status == "draft_pending_review"
        assert draft_row.gmail_message_id is None
        assert draft_row.sent_at is None
        assert "simulated reminder Gmail outage" in (draft_row.last_provider_error or "")
        assert "simulated reminder Gmail outage" in task.source_payload["provider_error"]
    finally:
        session.rollback()
        session.close()


def test_accepted_slot_approval_creates_calendar_event_once(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    create_calls = []

    def fake_create_event(**kwargs):
        create_calls.append(kwargs)
        return {"calendar_id": "primary", "event_id": "event-1", "event_link": "https://calendar.example/event-1"}

    monkeypatch.setattr(google_workspace, "create_appointment_event", fake_create_event)
    monkeypatch.setattr(google_workspace, "query_calendar_busy", lambda **kwargs: [])

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Calendar Test", slug=_id("calendar"))
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Calendar Therapist",
            email="therapist@example.com",
            availability_blocks=[],
        )
        session.add_all([tenant, therapist])
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral accepted a slot.",
            status="awaiting_patient_reply",
            patient_name="Calendar Patient",
            contact_email="patient@example.com",
        )
        session.add(referral)
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=3)
        appointment = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=60),
            status="proposed",
        )
        session.add(appointment)
        session.flush()
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="appointment_confirmation_approval",
            status="open",
            reason="Approve accepted slot.",
            payload_key=f"appointment_confirmation:{appointment.id[:8]}",
            source_payload={"appointment_id": appointment.id},
        )
        session.add(task)
        session.flush()

        apply_review_action(session, task_id=task.id, action="approve")
        apply_review_action(session, task_id=task.id, action="approve")

        assert len(create_calls) == 1
        assert create_calls[0]["patient_email"] == "lumenpatientdemo@gmail.com"
        assert create_calls[0]["therapist_name"] == "Calendar Therapist"
        assert task.status == "approved"
        assert appointment.status == "confirmed"
        assert referral.status == "appointment_confirmed"
        assert appointment.google_calendar_event_id == "event-1"
        assert appointment.google_calendar_event_link == "https://calendar.example/event-1"
        assert appointment.last_provider_error is None
    finally:
        session.rollback()
        session.close()


def test_calendar_failure_leaves_appointment_unconfirmed(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")

    def fake_create_event(**kwargs):
        raise google_workspace.GoogleWorkspaceError("simulated Calendar outage")

    monkeypatch.setattr(google_workspace, "create_appointment_event", fake_create_event)
    monkeypatch.setattr(google_workspace, "query_calendar_busy", lambda **kwargs: [])

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Calendar Failure Test", slug=_id("calendar-failure"))
        therapist = Therapist(tenant_id=tenant.id, name="Calendar Therapist", availability_blocks=[])
        session.add_all([tenant, therapist])
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral accepted a slot.",
            status="awaiting_patient_reply",
            patient_name="Calendar Patient",
        )
        session.add(referral)
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=3)
        appointment = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=60),
            status="proposed",
        )
        session.add(appointment)
        session.flush()
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="appointment_confirmation_approval",
            status="open",
            reason="Approve accepted slot.",
            payload_key=f"appointment_confirmation:{appointment.id[:8]}",
            source_payload={"appointment_id": appointment.id},
        )
        session.add(task)
        session.flush()

        apply_review_action(session, task_id=task.id, action="approve")

        assert task.status == "open"
        assert appointment.status == "proposed"
        assert referral.status == "awaiting_patient_reply"
        assert "simulated Calendar outage" in (appointment.last_provider_error or "")
    finally:
        session.rollback()
        session.close()


def test_first_session_ready_requires_linked_google_event_when_enabled(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Readiness Google", slug=_id("readiness-google"))
        session.add(tenant)
        session.flush()
        therapist = Therapist(tenant_id=tenant.id, name="Readiness Therapist")
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral with local appointment only.",
            status="intake_complete",
            patient_name="Readiness Patient",
        )
        session.add_all([therapist, referral])
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=3)
        session.add(
            Appointment(
                tenant_id=tenant.id,
                referral_id=referral.id,
                therapist_id=therapist.id,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=60),
                status="confirmed",
            )
        )
        session.add(
            IntakeChecklistItem(
                tenant_id=tenant.id,
                referral_id=referral.id,
                item_key="intake_form",
                label="Intake form",
                item_type="form",
                status="completed",
            )
        )
        session.add(
            TherapistPrepBrief(
                tenant_id=tenant.id,
                referral_id=referral.id,
                therapist_id=therapist.id,
                title="Prep brief",
                body="Ready notes",
            )
        )
        session.flush()

        detail = referral_detail(session, referral.id)

        assert referral.status == "intake_complete"
        assert "Confirmed appointment is missing a linked Google Calendar event." in detail["readiness_blockers"]
        assert detail["workbench_state"]["progress"]["appointment_confirmed"] is False
        assert detail["workbench_state"]["progress"]["first_session_ready"] is False
    finally:
        session.rollback()
        session.close()


def test_slot_proposal_filters_google_busy_intervals(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")

    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    weekday = tomorrow.strftime("%A")
    busy_start = datetime.combine(tomorrow.date(), datetime.min.time(), tzinfo=timezone.utc).replace(hour=9)
    busy_end = busy_start + timedelta(minutes=60)
    monkeypatch.setattr(
        google_workspace,
        "query_calendar_busy",
        lambda **kwargs: [{"start": busy_start, "end": busy_end}],
    )

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Busy Filter Test", slug=_id("busy-filter"))
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Busy Therapist",
            availability_blocks=[{"weekday": weekday, "start": "09:00", "end": "12:00"}],
        )
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral ready for slots.",
            status="match_approved",
            patient_name="Busy Patient",
            match_summary={"ranked_matches": [{"therapist_id": ""}]},
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        referral.match_summary = {"ranked_matches": [{"therapist_id": therapist.id}]}

        proposals = propose_appointment_slots(session, referral.id, limit=1)

        assert len(proposals) == 1
        proposed_start = datetime.fromisoformat(proposals[0]["starts_at"])
        assert proposed_start >= busy_start + timedelta(minutes=70)
    finally:
        session.rollback()
        session.close()


def test_weekly_patient_contact_cap_blocks_slot_proposals(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    week_start = datetime.combine(
        tomorrow.date() - timedelta(days=tomorrow.weekday()),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Capacity Cap Test", slug=_id("capacity-cap"))
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Capacity Therapist",
            availability_blocks=[
                {"weekday": day, "start": "08:00", "end": "21:00"}
                for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
            ],
        )
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral ready for capacity check.",
            status="match_approved",
            patient_name="Capacity Patient",
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        referral.match_summary = {"ranked_matches": [{"therapist_id": therapist.id}]}
        for index in range(20):
            starts_at = week_start + timedelta(days=index // 4, hours=8 + (index % 4) * 2)
            session.add(
                Appointment(
                    tenant_id=tenant.id,
                    therapist_id=therapist.id,
                    starts_at=starts_at,
                    ends_at=starts_at + timedelta(minutes=60),
                    status="confirmed",
                )
            )
        session.flush()

        proposals = propose_appointment_slots(session, referral.id, limit=1)

        assert proposals == []
    finally:
        session.rollback()
        session.close()


def test_reschedule_approval_updates_google_calendar_event(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    monkeypatch.delenv("LUMEN_OUTBOUND_PATIENT_EMAIL_OVERRIDE", raising=False)
    update_calls = []

    def fake_update_event(**kwargs):
        update_calls.append(kwargs)
        return {"calendar_id": "primary", "event_id": kwargs["event_id"], "event_link": "https://calendar.example/event-1"}

    monkeypatch.setattr(google_workspace, "query_calendar_busy", lambda **kwargs: [])
    monkeypatch.setattr(google_workspace, "update_appointment_event", fake_update_event)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Calendar Reschedule Test", slug=_id("calendar-reschedule"))
        therapist = Therapist(tenant_id=tenant.id, name="Reschedule Therapist", email="therapist@example.com", availability_blocks=[])
        session.add_all([tenant, therapist])
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral needs a reschedule.",
            status="appointment_confirmed",
            patient_name="Reschedule Patient",
            contact_email="patient@example.com",
        )
        session.add(referral)
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=3)
        appointment = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=60),
            status="confirmed",
            google_calendar_id="primary",
            google_calendar_event_id="event-1",
        )
        session.add(appointment)
        session.flush()
        new_start = starts_at + timedelta(days=1, hours=1)
        task = request_appointment_reschedule(
            session,
            appointment_id=appointment.id,
            starts_at=new_start,
            reason="Move to the patient's alternate slot.",
        )
        session.flush()

        apply_review_action(session, task_id=task.id, action="approve")

        assert len(update_calls) == 1
        assert update_calls[0]["event_id"] == "event-1"
        assert update_calls[0]["patient_email"] == "lumenpatientdemo@gmail.com"
        assert task.status == "approved"
        assert appointment.starts_at == new_start
        assert appointment.ends_at == new_start + timedelta(minutes=60)
        assert appointment.google_calendar_event_id == "event-1"
        assert appointment.google_calendar_synced_at is not None
        assert appointment.last_provider_error is None
        session.flush()
        actions = [row.action for row in session.scalars(select(AuditLog).where(AuditLog.tenant_id == tenant.id))]
        assert "provider_calendar_event_update" in actions
        assert "reschedule" in actions
    finally:
        session.rollback()
        session.close()


def test_therapist_capacity_reflects_google_busy_and_local_contact_hours(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")

    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    weekday = tomorrow.strftime("%A")
    busy_start = datetime.combine(tomorrow.date(), datetime.min.time(), tzinfo=timezone.utc).replace(hour=9)
    busy_end = busy_start + timedelta(minutes=60)

    monkeypatch.setattr(google_workspace, "query_calendar_busy", lambda **kwargs: [{"start": busy_start, "end": busy_end}])

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Therapist Capacity Test", slug=_id("therapist-capacity"))
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Capacity UI Therapist",
            availability_blocks=[{"weekday": weekday, "start": "09:00", "end": "12:00"}],
        )
        session.add_all([tenant, therapist])
        session.flush()
        appointment_start = busy_start + timedelta(days=1)
        appointment = Appointment(
            tenant_id=tenant.id,
            therapist_id=therapist.id,
            starts_at=appointment_start,
            ends_at=appointment_start + timedelta(minutes=60),
            status="confirmed",
            google_calendar_id="primary",
            google_calendar_event_id="event-capacity-1",
        )
        session.add(appointment)
        session.flush()
        monkeypatch.setattr(
            google_workspace,
            "list_lumen_appointment_events",
            lambda **kwargs: [
                {
                    "id": "event-capacity-1",
                    "summary": "[Lumen] Therapist: Capacity UI Therapist | Patient: Sarah O'Connor",
                    "htmlLink": "https://calendar.example/event-capacity-1",
                    "start": appointment.starts_at,
                    "end": appointment.ends_at,
                    "lumen_appointment_id": appointment.id,
                    "lumen_therapist_id": therapist.id,
                }
            ],
        )

        payload = therapist_calendar_capacity(session, tenant_id=tenant.id)
        summary = payload["therapists"][0]

        assert payload["google_enabled"] is True
        assert summary["busy_periods"][0]["start"] == busy_start.isoformat()
        assert summary["weekly_patient_contact_hours_used"] == 1
        assert summary["weekly_patient_contact_hours_remaining"] == 19
        assert summary["active_appointments"][0]["google_calendar_event_id"] == "event-capacity-1"
        assert summary["sync_status"] == "ready"
        assert datetime.fromisoformat(summary["next_available_slot"]["starts_at"]) >= busy_start + timedelta(minutes=70)
    finally:
        session.rollback()
        session.close()
