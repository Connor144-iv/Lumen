from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.lumen_web import google_workspace
from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import Appointment, CommunicationDraft, HumanReviewTask, Referral, Tenant, Therapist
from backend.lumen_web.repositories import apply_review_action, propose_appointment_slots


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
    assert status["configured_scopes"] == ["gmail.send", "calendar.readonly", "calendar.events"]
    assert status["calendar_id"] == "primary"


def test_approved_draft_sends_once_and_progresses_referral(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
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
        assert task.status == "approved"
        assert referral.status == "contact_sent"
        assert draft.status == "sent"
        assert draft.body == "Approved body"
        assert draft.gmail_message_id == "gmail-message-1"
        assert draft.gmail_thread_id == "gmail-thread-1"
        assert draft.last_provider_error is None
    finally:
        session.rollback()
        session.close()


def test_gmail_failure_leaves_review_gate_open(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")

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
        assert "simulated Gmail outage" in (draft.last_provider_error or "")
        assert "provider_error" in task.source_payload
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
            ends_at=starts_at + timedelta(minutes=50),
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
            ends_at=starts_at + timedelta(minutes=50),
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


def test_slot_proposal_filters_google_busy_intervals(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")

    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    weekday = tomorrow.strftime("%A")
    busy_start = datetime.combine(tomorrow.date(), datetime.min.time(), tzinfo=timezone.utc).replace(hour=9)
    busy_end = busy_start + timedelta(minutes=50)
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
        assert proposed_start != busy_start
    finally:
        session.rollback()
        session.close()
