from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from backend.lumen_web import google_workspace
from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import HumanReviewTask, IntakeTemplate, Referral, Tenant, Therapist
from backend.lumen_web.repositories import (
    apply_review_action,
    complete_consent_record,
    complete_intake_item,
    create_suitability_review,
    deterministic_match_for_referral,
    draft_first_contact_message,
    draft_intake_packet,
    draft_missing_info_request,
    generate_prep_brief,
    intake_workspace,
    propose_appointment_slots,
    record_missing_info_reply,
    record_simulated_patient_reply,
    referral_detail,
)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def _task(session, referral_id: str, task_type: str, payload_prefix: str | None = None) -> HumanReviewTask:
    query = select(HumanReviewTask).where(
        HumanReviewTask.referral_id == referral_id,
        HumanReviewTask.task_type == task_type,
        HumanReviewTask.status == "open",
    )
    if payload_prefix:
        query = query.where(HumanReviewTask.payload_key.startswith(payload_prefix))
    task = session.scalar(query.order_by(HumanReviewTask.created_at.desc()).limit(1))
    assert task is not None
    return task


def test_clean_referral_demo_path_reaches_first_session_ready(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    send_calls = []
    calendar_calls = []

    def fake_send(**kwargs):
        send_calls.append(kwargs)
        return {"message_id": f"gmail-{len(send_calls)}", "thread_id": f"thread-{len(send_calls)}"}

    def fake_create_event(**kwargs):
        calendar_calls.append(kwargs)
        return {
            "calendar_id": "primary",
            "event_id": f"event-{len(calendar_calls)}",
            "event_link": f"https://calendar.example/event-{len(calendar_calls)}",
        }

    monkeypatch.setattr(google_workspace, "send_approved_draft", fake_send)
    monkeypatch.setattr(google_workspace, "query_calendar_busy", lambda **kwargs: [])
    monkeypatch.setattr(google_workspace, "create_appointment_event", fake_create_event)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Clean Demo Tenant", slug=_id("clean-demo"))
        session.add(tenant)
        session.flush()
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Clean Demo Therapist",
            email="therapist@example.com",
            specialties=["anxiety", "work stress"],
            age_groups=["adult"],
            languages=["Portuguese"],
            modalities=["online"],
            insurers=["Multicare"],
            availability_blocks=[
                {"weekday": day, "start": "09:00", "end": "17:00", "modality": "online"}
                for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
            ],
        )
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Adult referral for anxiety and work stress. Portuguese online therapy. Insurer Multicare.",
            status="needs_admin_review",
            patient_name="Clean Demo Patient",
            contact_email="patient@example.com",
            insurer="Multicare",
            language_preference="Portuguese",
            modality_preference="online",
            missing_fields=["date_of_birth"],
        )
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Clean demo intake",
            required_items=[
                {"key": "privacy_notice", "label": "Privacy notice", "type": "consent", "consent_scope": "privacy_notice"},
                {"key": "intake_form", "label": "Clinical intake form", "type": "form"},
            ],
        )
        session.add_all([therapist, referral, template])
        session.flush()

        draft_missing_info_request(session, referral.id, note="Please confirm date of birth.")
        apply_review_action(
            session,
            task_id=_task(session, referral.id, "missing_info_message_approval").id,
            action="approve",
        )
        record_missing_info_reply(
            session,
            referral.id,
            updates={"date_of_birth": "1990-01-01"},
            notes="Patient confirmed DOB.",
        )
        assert referral.status == "ready_for_matching"

        suitability = create_suitability_review(session, referral.id, reason="Confirm standard adult outpatient fit.")
        apply_review_action(session, task_id=suitability.id, action="approve")
        deterministic_match_for_referral(session, referral.id)
        apply_review_action(session, task_id=_task(session, referral.id, "match_approval").id, action="approve")

        proposals = propose_appointment_slots(session, referral.id, limit=1)
        apply_review_action(session, task_id=_task(session, referral.id, "slot_offer_approval").id, action="approve")
        first_contact = draft_first_contact_message(session, referral.id, note="Please confirm this slot suits you.")
        apply_review_action(
            session,
            task_id=_task(session, referral.id, "send_approval", f"first_contact_draft:{first_contact['id'][:8]}").id,
            action="approve",
        )

        reply = record_simulated_patient_reply(
            session,
            referral.id,
            reply_type="accepted_slot",
            appointment_id=proposals[0]["id"],
        )
        apply_review_action(session, task_id=reply["task"]["id"], action="approve")

        intake_packet = draft_intake_packet(session, referral.id, note="Please complete before the first session.")
        apply_review_action(
            session,
            task_id=_task(session, referral.id, "send_approval", f"intake_packet_draft:{intake_packet['id'][:8]}").id,
            action="approve",
        )
        intake = intake_workspace(session, referral.id)
        for item in intake["items"]:
            complete_intake_item(session, item["id"], notes="Completed in clean demo smoke path.")
        for consent in intake["consents"]:
            complete_consent_record(session, consent["id"])
        assert referral.status == "intake_complete"

        generate_prep_brief(session, referral.id)
        detail = referral_detail(session, referral.id)

        assert referral.status == "first_session_ready"
        assert len(send_calls) == 3
        assert len(calendar_calls) == 1
        assert detail["readiness_blockers"] == []
        assert detail["workbench_state"]["progress"]["contacted"] is True
        assert detail["workbench_state"]["progress"]["appointment_confirmed"] is True
        assert detail["workbench_state"]["progress"]["intake_complete"] is True
        assert detail["workbench_state"]["progress"]["prep_brief_ready"] is True
        assert detail["workbench_state"]["progress"]["first_session_ready"] is True
    finally:
        session.rollback()
        session.close()
