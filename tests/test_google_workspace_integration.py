from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

import app as app_module
from backend.lumen_web import google_workspace
from backend.lumen_web import repositories as repository_module
from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import (
    AuditLog,
    Appointment,
    CommunicationDraft,
    ConsentRecord,
    Document,
    HumanReviewTask,
    IntakeChecklistItem,
    IntakeTemplate,
    QuestionnaireResponse,
    Referral,
    ScoreRecord,
    Tenant,
    Therapist,
    TherapistPrepBrief,
    WorkflowEvent,
    WorkflowRun,
)
from backend.lumen_web.repositories import (
    apply_review_action,
    continue_email_referral_workflow,
    create_intake_template_file,
    create_referral_for_request,
    deterministic_match_for_referral,
    draft_first_contact_message,
    draft_intake_packet,
    draft_missing_info_request,
    finish_workflow_run,
    generate_missing_intake_reminder,
    generate_prep_brief,
    ingest_gmail_message,
    list_intake_templates,
    prepare_email_referral_followup,
    propose_appointment_slots,
    referral_detail,
    record_missing_info_reply,
    request_appointment_reschedule,
    reset_clean_demo_referral,
    reset_gmail_patient_demo,
    start_intake_for_referral,
    therapist_calendar_capacity,
)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def _template_file_meta(name: str, content: bytes = b"Blank intake form") -> dict[str, str | int | dict]:
    return app_module.store_uploaded_document(_id("template-file"), name, "text/plain", content)


def _write_demo_intake_assets(directory) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for spec in repository_module.DEMO_GMAIL_INTAKE_REQUIRED_ITEMS:
        (directory / spec["demo_asset_file"]).write_bytes(f"Blank {spec['label']}".encode("utf-8"))


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


def test_parse_gmail_message_includes_attachment_metadata() -> None:
    message = {
        "id": "gmail-message-with-attachment",
        "threadId": "thread-attachments",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Completed intake"},
                {"name": "From", "value": "Demo Patient <lumenpatientdemo@gmail.com>"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "SGVsbG8="}},
                {
                    "filename": "intake.json",
                    "mimeType": "application/json",
                    "body": {"attachmentId": "attachment-1", "size": 42},
                },
            ],
        },
    }

    parsed = google_workspace.parse_gmail_message(message)

    assert parsed["message_id"] == "gmail-message-with-attachment"
    assert parsed["body"] == "Hello"
    assert parsed["attachments"] == [
        {
            "message_id": "gmail-message-with-attachment",
            "attachment_id": "attachment-1",
            "file_name": "intake.json",
            "mime_type": "application/json",
            "size_bytes": 42,
        }
    ]


def test_intake_template_file_upload_replaces_active_pointer() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Template Files", slug=_id("template-files"))
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Template file set",
            required_items=[{"key": "consent", "label": "Consent", "type": "consent"}],
        )
        session.add(tenant)
        session.flush()
        session.add(template)
        session.flush()

        first_meta = _template_file_meta("consent-v1.txt", b"version 1")
        first = create_intake_template_file(
            session,
            template_id=template.id,
            item_key="consent",
            title=str(first_meta["file_name"]),
            storage_uri=str(first_meta["storage_uri"]),
            metadata=first_meta,
        )
        second_meta = _template_file_meta("consent-v2.txt", b"version 2")
        second = create_intake_template_file(
            session,
            template_id=template.id,
            item_key="consent",
            title=str(second_meta["file_name"]),
            storage_uri=str(second_meta["storage_uri"]),
            metadata=second_meta,
        )

        first_doc = session.get(Document, first["id"])
        listed = list_intake_templates(session, tenant_id=tenant.id)[0]

        assert first_doc.metadata_json["active"] is False
        assert second["metadata"]["active"] is True
        assert listed["required_items"][0]["template_file"]["document_id"] == second["id"]
        assert listed["missing_template_files"] == []
    finally:
        session.rollback()
        session.close()


def test_gmail_sync_falls_back_to_recent_demo_patient_messages(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    message_id = f"recent-read-{uuid4()}"
    list_calls = []
    marked_read = []

    def fake_list(**kwargs):
        list_calls.append(kwargs)
        return [] if kwargs.get("unread_only", True) else [{"id": message_id}]

    monkeypatch.setattr(google_workspace, "list_unread_gmail_messages", fake_list)
    monkeypatch.setattr(google_workspace, "get_gmail_message", lambda **kwargs: {"id": kwargs["message_id"]})
    monkeypatch.setattr(
        google_workspace,
        "parse_gmail_message",
        lambda raw: {
            "message_id": raw["id"],
            "thread_id": "thread-recent-read",
            "from": "Sync Fallback Patient <sync-fallback@example.test>",
            "subject": "Appointment request",
            "body": "My name is Sync Fallback Patient. I need an appointment.",
            "attachments": [],
        },
    )
    monkeypatch.setattr(google_workspace, "mark_gmail_message_read", lambda **kwargs: marked_read.append(kwargs["message_id"]))
    monkeypatch.setattr(google_workspace, "gmail_profile_email", lambda: "clinic-admin@example.test")

    client = TestClient(app_module.app)
    response = client.post("/api/integrations/gmail-sync", json={"max_results": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["unread_seen"] == 0
    assert payload["recent_seen"] == 1
    assert payload["processed"][0]["message_id"] == message_id
    assert "from:lumenpatientdemo@gmail.com" in payload["recent_query"]
    assert list_calls[0]["unread_only"] is True
    assert list_calls[1]["unread_only"] is False
    assert marked_read == [message_id]
    session = SessionLocal()
    try:
        document = session.scalar(select(Document).where(Document.storage_uri == f"gmail:message:{message_id}"))
        assert document is not None
        assert document.document_type == "inbound_email_unmatched"
    finally:
        session.close()


def test_new_gmail_from_existing_patient_email_stays_unmatched_without_reply_context() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Gmail Sender Collision", slug=_id("gmail-sender-collision"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Legacy demo referral.",
            status="needs_admin_review",
            patient_name="Clean Demo Patient",
            contact_email="lumenpatientdemo@gmail.com",
            insurer="Multicare",
            language_preference="Portuguese",
            missing_fields=["date_of_birth"],
        )
        session.add(tenant)
        session.flush()
        session.add(referral)
        session.flush()

        result = ingest_gmail_message(
            session,
            tenant_id=tenant.id,
            message={
                "message_id": f"simon-new-referral-{uuid4()}",
                "thread_id": "fresh-thread-without-lumen-draft",
                "from": "Simon Anderson <lumenpatientdemo@gmail.com>",
                "subject": "Appointment request",
                "body": (
                    "My name is Simon Anderson, I would like to book an appointment with one of your therapists. "
                    "I fear that my toes are slowly turning into mushy bananas. "
                    "You can use this email to contact me. Thanks, Simon"
                ),
            },
        )

        assert result["action"] == "unmatched"
        assert result["match_reason"] == "unmatched"
        session.refresh(referral)
        assert referral.patient_name == "Clean Demo Patient"
        assert referral.insurer == "Multicare"
        assert referral.language_preference == "Portuguese"
        assert session.scalar(
            select(Document).where(
                Document.storage_uri == f"gmail:message:{result['message_id']}",
                Document.document_type == "inbound_email_unmatched",
            )
        )
    finally:
        session.rollback()
        session.close()


def test_reply_like_gmail_without_thread_can_match_sent_draft_by_sender() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Gmail Reply Fallback", slug=_id("gmail-reply-fallback"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral waiting for date of birth.",
            status="waiting_for_missing_info",
            patient_name="Reply Patient",
            contact_email="lumenpatientdemo@gmail.com",
            missing_fields=["date_of_birth"],
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
                subject="Please confirm missing details",
                body="Please send your DOB.",
                status="sent",
                recipient_email="lumenpatientdemo@gmail.com",
                gmail_message_id="sent-without-thread",
            )
        )
        session.flush()

        result = ingest_gmail_message(
            session,
            tenant_id=tenant.id,
            message={
                "message_id": f"reply-no-thread-{uuid4()}",
                "thread_id": "gmail-lost-thread-id",
                "from": "Reply Patient <lumenpatientdemo@gmail.com>",
                "subject": "Re: Please confirm missing details",
                "body": "DOB: 1990-01-01.",
            },
        )

        assert result["referral_id"] == referral.id
        assert result["match_reason"] == "sender_email_reply"
        assert result["action"] == "missing_info_reply"
        session.refresh(referral)
        assert referral.date_of_birth == "1990-01-01"
    finally:
        session.rollback()
        session.close()


def test_gmail_sync_rejects_wrong_authorized_mailbox(monkeypatch) -> None:
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    monkeypatch.setattr(google_workspace, "gmail_profile_email", lambda: "clara.demo1234@gmail.com")

    client = TestClient(app_module.app)
    response = client.post("/api/integrations/gmail-sync", json={"max_results": 5})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "clara.demo1234@gmail.com" in detail
    assert "clinic-admin@example.test" in detail
    assert "google_workspace_auth.py" in detail


def test_gmail_inbox_convert_starts_referral_workflow(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    submitted = []

    def fake_submit(request):
        submitted.append(request)
        return {"job_id": "gmail-workflow-job", "referral_id": "gmail-referral-id", "status": "queued"}

    monkeypatch.setattr(app_module, "jobs", SimpleNamespace(submit=fake_submit))
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Gmail Convert Test", slug=_id("gmail-convert"))
        document = Document(
            tenant_id=tenant.id,
            document_type="inbound_email_unmatched",
            title="New therapy referral",
            storage_uri="gmail:message:convert-message-1",
            metadata_json={
                "gmail_message_id": "convert-message-1",
                "gmail_thread_id": "thread-convert-1",
                "from": "Demo Patient <lumenpatientdemo@gmail.com>",
                "sender_email": "lumenpatientdemo@gmail.com",
                "subject": "New therapy referral",
                "body": "Adult referral for online Portuguese therapy. Available Tuesday 10:00 to 12:00.",
            },
        )
        session.add(tenant)
        session.flush()
        session.add(document)
        session.commit()

        client = TestClient(app_module.app)
        response = client.post(
            "/api/integrations/gmail-inbox/convert",
            json={"document_id": document.id, "tenant_id": tenant.id},
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["conversion_status"] == "workflow_started"
        assert payload["job_id"] == "gmail-workflow-job"
        assert payload["referral_id"] == "gmail-referral-id"
        assert payload["events_url"] == "/api/events/gmail-workflow-job"
        assert submitted[0].workflow_type == "new_referral"
        assert submitted[0].raw_input["source_channel"] == "email"
        assert submitted[0].raw_input["contact_email"] == "lumenpatientdemo@gmail.com"

        refreshed = session.get(Document, document.id)
        session.refresh(refreshed)
        assert refreshed.metadata_json["referral_id"] == "gmail-referral-id"
        assert refreshed.metadata_json["workflow_job_id"] == "gmail-workflow-job"
    finally:
        session.close()


def test_email_referral_placeholder_does_not_create_premature_missing_info_task() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Email Placeholder Test", slug=_id("email-placeholder"))
        session.add(tenant)
        session.flush()

        referral = create_referral_for_request(
            session,
            SimpleNamespace(
                workflow_type="new_referral",
                tenant_id=tenant.id,
                patient_id=None,
                raw_input={
                    "source_channel": "email",
                    "raw_text": "My name is Ben. I am available Monday morning.",
                    "sender": "Ben <lumenpatientdemo@gmail.com>",
                },
            ),
        )

        assert referral is not None
        assert referral.status == "normalising"
        assert referral.patient_name is None
        assert referral.contact_email == "lumenpatientdemo@gmail.com"
        assert referral.missing_fields == []
        assert session.scalar(select(HumanReviewTask).where(HumanReviewTask.referral_id == referral.id)) is None
    finally:
        session.rollback()
        session.close()


def test_gmail_patient_demo_reset_removes_email_referral_and_repairs_inbound_doc() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Demo Clinic", slug=_id("demo-reset"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral.",
            status="needs_admin_review",
            patient_name="Anna Anderson",
            contact_email="lumenpatientdemo@gmail.com",
            duplicate_candidates=["gmail-doc-id"],
        )
        session.add(tenant)
        session.flush()
        session.add(referral)
        session.flush()
        workflow = WorkflowRun(
            id=_id("workflow"),
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="failed",
            input_summary="Old Gmail workflow.",
            request_payload={"raw_input": {"source_channel": "email"}},
            approvals={},
        )
        referral.workflow_run_id = workflow.id
        session.add(workflow)
        session.flush()
        event = WorkflowEvent(
            tenant_id=tenant.id,
            workflow_run_id=workflow.id,
            index=0,
            type="workflow",
            status="failed",
            message="Old failed run.",
            node="workflow",
        )
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_run_id=workflow.id,
            channel="email",
            subject="Old draft",
            body="Old body",
            status="draft_pending_review",
        )
        document = Document(
            tenant_id=tenant.id,
            document_type="inbound_email_unmatched",
            title="Therapy enquiry",
            storage_uri="gmail:message:demo-reset-message",
            metadata_json={
                "sender_email": "lumenpatientdemo@gmail.com",
                "referral_id": referral.id,
                "workflow_job_id": "old-job",
                "converted_to_referral": True,
            },
        )
        task = HumanReviewTask(
            tenant_id=tenant.id,
            workflow_run_id=workflow.id,
            referral_id=referral.id,
            task_type="inbound_reply_review",
            status="open",
            reason="Old task.",
            payload_key="old",
        )
        session.add_all([event, draft, document, task])
        session.flush()

        result = reset_gmail_patient_demo(session, tenant_id=tenant.id)

        assert referral.id in result["removed_referral_ids"]
        assert session.get(Referral, referral.id) is None
        assert session.get(HumanReviewTask, task.id) is None
        assert session.get(CommunicationDraft, draft.id) is None
        assert session.get(WorkflowEvent, event.id) is None
        assert session.get(WorkflowRun, workflow.id) is None
        session.refresh(document)
        assert "referral_id" not in document.metadata_json
        assert "workflow_job_id" not in document.metadata_json
        assert "converted_to_referral" not in document.metadata_json
        assert result["repaired_inbound_documents"] == 1
    finally:
        session.rollback()
        session.close()


def test_gmail_patient_demo_reset_registers_intake_packet_docx_assets(monkeypatch, tmp_path) -> None:
    Base.metadata.create_all(bind=engine)
    asset_dir = tmp_path / "intake-assets"
    _write_demo_intake_assets(asset_dir)
    monkeypatch.setattr(repository_module, "DEMO_GMAIL_INTAKE_PACKET_ASSET_DIR", asset_dir)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Demo Intake Assets", slug=_id("demo-intake-assets"))
        session.add(tenant)
        session.flush()

        result = reset_gmail_patient_demo(session, tenant_id=tenant.id)

        assert result["missing_intake_template_files"] == []
        assert len(result["intake_template_files"]) == 4
        assert {item["file_name"] for item in result["intake_template_files"]} == {
            "privacy_notice_acknowledged.docx",
            "telehealth_consent.docx",
            "clinical_intake_form.docx",
            "pre_session_screening_questionnaire.docx",
        }
        template = session.get(IntakeTemplate, result["intake_template"]["id"])
        assert template is not None
        assert template.source_channel == "email"
        assert [item["key"] for item in template.required_items] == [
            "privacy_notice",
            "telehealth_consent",
            "intake_form",
            "screening_questionnaire",
        ]
    finally:
        session.rollback()
        session.close()


def test_gmail_patient_demo_reset_reports_missing_intake_packet_asset(monkeypatch, tmp_path) -> None:
    Base.metadata.create_all(bind=engine)
    asset_dir = tmp_path / "partial-intake-assets"
    _write_demo_intake_assets(asset_dir)
    (asset_dir / "telehealth_consent.docx").unlink()
    monkeypatch.setattr(repository_module, "DEMO_GMAIL_INTAKE_PACKET_ASSET_DIR", asset_dir)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Missing Demo Intake Asset", slug=_id("missing-demo-intake"))
        session.add(tenant)
        session.flush()

        result = reset_gmail_patient_demo(session, tenant_id=tenant.id)

        assert any(item["file_name"] == "telehealth_consent.docx" for item in result["missing_intake_template_files"])
        assert len(result["intake_template_files"]) == 3
    finally:
        session.rollback()
        session.close()


def test_gmail_demo_intake_packet_sends_all_seeded_docx_attachments(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    asset_dir = repository_module.REPO_ROOT / "storage" / "test-demo-intake-assets" / _id("assets")
    _write_demo_intake_assets(asset_dir)
    monkeypatch.setattr(repository_module, "DEMO_GMAIL_INTAKE_PACKET_ASSET_DIR", asset_dir)
    send_calls = []

    def fake_send(**kwargs):
        send_calls.append(kwargs)
        return {"message_id": "gmail-demo-intake-message", "thread_id": "gmail-demo-intake-thread"}

    monkeypatch.setattr(google_workspace, "send_approved_draft", fake_send)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Demo Intake Send", slug=_id("demo-intake-send"))
        session.add(tenant)
        session.flush()
        reset_gmail_patient_demo(session, tenant_id=tenant.id)
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral with confirmed appointment.",
            status="appointment_confirmed",
            patient_name="Demo Intake Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        session.add(referral)
        session.flush()

        draft = draft_intake_packet(session, referral.id)
        task = session.scalar(
            select(HumanReviewTask).where(
                HumanReviewTask.referral_id == referral.id,
                HumanReviewTask.task_type == "send_approval",
            )
        )
        apply_review_action(session, task_id=task.id, action="approve")

        draft_row = session.get(CommunicationDraft, draft["id"])
        assert len(draft["outbound_attachment_manifest"]) == 4
        assert len(send_calls) == 1
        assert [item["file_name"] for item in send_calls[0]["attachments"]] == [
            "privacy_notice_acknowledged.docx",
            "telehealth_consent.docx",
            "clinical_intake_form.docx",
            "pre_session_screening_questionnaire.docx",
        ]
        assert {item["content_type"] for item in send_calls[0]["attachments"]} == {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        }
        assert draft_row.gmail_message_id == "gmail-demo-intake-message"
        assert draft_row.gmail_thread_id == "gmail-demo-intake-thread"
        assert referral.status == "intake_packet_sent"
    finally:
        session.rollback()
        session.close()
        shutil.rmtree(asset_dir, ignore_errors=True)


def test_running_email_workflow_returns_active_canonical_state_without_deterministic_side_effects(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Continue Email Test", slug=_id("continue-email"))
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Email Continue Therapist",
            availability_blocks=[{"weekday": "Monday", "start": "08:00", "end": "12:00"}],
        )
        session.add(tenant)
        session.flush()
        session.add(therapist)
        session.flush()
        raw_text = "\n".join(
            [
                "From: Ben <lumenpatientdemo@gmail.com>",
                "Subject: Appointment request",
                "My name is Ben Anderson,",
                "",
                "I would like to book an appointment with one of your therapists.",
                "I am available on Monday morning, and all day Thursday, Friday and Saturday.",
            ]
        )
        referral = create_referral_for_request(
            session,
            SimpleNamespace(
                workflow_type="new_referral",
                tenant_id=tenant.id,
                patient_id=None,
                raw_input={"source_channel": "email", "raw_text": raw_text, "sender": "Ben <lumenpatientdemo@gmail.com>"},
            ),
        )
        assert referral is not None
        referral.duplicate_candidates = ["demo-duplicate-candidate"]
        run = WorkflowRun(
            id=_id("workflow"),
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="running",
            input_summary="Appointment request",
            request_payload={"raw_input": {"source_channel": "email", "raw_text": raw_text}},
            approvals={},
        )
        referral.workflow_run_id = run.id
        session.add(run)
        session.flush()

        detail = referral_detail(session, referral.id)
        packet = detail["workbench_state"]["email_workflow"]
        assert detail["patient_name"] is None
        assert packet["next_action"] == "wait_extraction"

        result = continue_email_referral_workflow(session, referral.id)

        assert result["status"] == "running"
        assert result["result"]["action"] == "wait"
        session.refresh(referral)
        assert referral.patient_name is None
        assert referral.duplicate_candidates == ["demo-duplicate-candidate"]
        appointments = list(session.scalars(select(Appointment).where(Appointment.referral_id == referral.id)))
        drafts = list(session.scalars(select(CommunicationDraft).where(CommunicationDraft.referral_id == referral.id)))
        tasks = list(session.scalars(select(HumanReviewTask).where(HumanReviewTask.referral_id == referral.id)))
        assert appointments == []
        assert drafts == []
        assert tasks == []
        assert session.scalar(select(AuditLog).where(AuditLog.entity_id == referral.id, AuditLog.action == "deterministic_email_extract")) is None
        assert session.scalar(
            select(AuditLog).where(AuditLog.entity_id == referral.id, AuditLog.action == "demo_bypass_email_duplicate_candidates")
        ) is None
    finally:
        session.rollback()
        session.close()


def test_stale_email_workflow_exposes_retry_action() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Stale Workflow Test", slug=_id("stale-workflow"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral text.",
            status="normalising",
            contact_email="lumenpatientdemo@gmail.com",
        )
        session.add(tenant)
        session.flush()
        session.add(referral)
        session.flush()
        run = WorkflowRun(
            id=_id("workflow"),
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="running",
            input_summary="Email referral text.",
            request_payload={"raw_input": {"source_channel": "email", "raw_text": referral.raw_text}},
            approvals={},
        )
        old_time = datetime.now(timezone.utc) - timedelta(minutes=31)
        run.created_at = old_time
        run.updated_at = old_time
        referral.workflow_run_id = run.id
        session.add(run)
        session.flush()

        detail = referral_detail(session, referral.id)

        session.refresh(run)
        session.refresh(referral)
        assert run.status == "failed"
        assert referral.status == "needs_admin_review"
        assert detail["workbench_state"]["email_workflow"]["next_action"] == "retry_extraction"
    finally:
        session.rollback()
        session.close()


def test_email_missing_fields_continue_on_canonical_workflow() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Email Missing Continue", slug=_id("email-missing-continue"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="My name is Simon Anderson. I need an appointment.",
            status="needs_admin_review",
            patient_name="Simon Anderson",
            contact_email="lumenpatientdemo@gmail.com",
            missing_fields=["date_of_birth", "insurer"],
        )
        run = WorkflowRun(
            id=_id("workflow"),
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="needs_review",
            input_summary="Email referral extracted missing fields.",
            request_payload={"raw_input": {"source_channel": "email", "raw_text": referral.raw_text}},
            approvals={},
        )
        session.add(tenant)
        session.flush()
        session.add_all([referral, run])
        session.flush()

        detail = referral_detail(session, referral.id)
        state = detail["workbench_state"]

        assert state["primary_action"] == "continue_email_workflow"
        assert state["primary_action_label"] == "Draft missing-info email"
        assert state["email_workflow"]["next_action"] == "continue_email_workflow"
    finally:
        session.rollback()
        session.close()


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


def test_email_workflow_persists_agent_draft_slots_and_canonical_send_gate(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Email Agent Draft Test", slug=_id("email-agent-draft"))
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Email Agent Therapist",
            specialties=["anxiety"],
            languages=["Portuguese"],
            modalities=["online"],
            insurers=["Multicare"],
            availability_blocks=[{"weekday": "Tuesday", "start": "10:00", "end": "13:00"}],
        )
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text=(
                "From: lumenpatientdemo@gmail.com\n"
                "Adult referral for anxiety. Portuguese online therapy. Insurer Multicare. "
                "Available Tuesday 10:00 to 12:00."
            ),
            status="needs_admin_review",
            patient_name="Email Agent Patient",
            contact_email="lumenpatientdemo@gmail.com",
            insurer="Multicare",
            language_preference="Portuguese",
            modality_preference="online",
            missing_fields=["date_of_birth"],
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        starts_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
        ends_at = starts_at + timedelta(minutes=60)
        slot_id = f"{therapist.id}:agent-slot-1"
        run = WorkflowRun(
            id=_id("workflow"),
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="running",
            input_summary="Agent email draft",
            request_payload={
                "raw_input": {
                    "source_channel": "email",
                    "raw_text": referral.raw_text,
                    "appointment_options": [
                        {
                            "slot_id": slot_id,
                            "option_code": "OPT1",
                            "option_number": 1,
                            "therapist_id": therapist.id,
                            "therapist_name": therapist.name,
                            "therapist_email": "therapist@example.com",
                            "starts_at": starts_at.isoformat(),
                            "ends_at": ends_at.isoformat(),
                            "weekday": starts_at.strftime("%A"),
                            "modality": "online",
                        }
                    ],
                }
            },
            approvals={"match_approval": True},
        )
        session.add(run)
        session.add(
            HumanReviewTask(
                tenant_id=tenant.id,
                referral_id=referral.id,
                task_type="match_approval",
                status="approved",
                reason="Previously approved match.",
                payload_key="match_recommendation",
                source_payload={},
            )
        )
        session.flush()

        finish_workflow_run(
            session,
            job_id=run.id,
            status="needs_review",
            result={
                "outputs": {
                    "clinical_signals": {"missing_required_fields": ["date_of_birth"]},
                    "match_recommendation": {
                        "ranked_matches": [{"therapist_id": therapist.id, "name": therapist.name}],
                        "excluded_therapists": [],
                        "rationale": "Best bounded match.",
                        "hard_constraints_checked": ["availability"],
                        "requires_human_approval": True,
                    },
                    "communication_draft": {
                        "channel": "email",
                        "subject": "Appointment option",
                        "body": f"Can you attend option 1 (OPT1)? Please also send your date of birth. Slot {slot_id}.",
                        "proposed_slots": [slot_id],
                        "requires_human_send": True,
                        "prohibited_content_check_passed": True,
                    },
                },
                "human_review_queue": [
                    {
                        "gate": "send_approval",
                        "payload_key": "communication_draft",
                        "reason": "Approve prepared email.",
                    }
                ],
            },
            error=None,
        )

        appointments = session.scalars(select(Appointment).where(Appointment.referral_id == referral.id)).all()
        assert len(appointments) == 1
        assert appointments[0].status == "proposed"
        assert appointments[0].google_calendar_event_id is None
        tasks = list(session.scalars(select(HumanReviewTask).where(HumanReviewTask.referral_id == referral.id)))
        task_types = [task.task_type for task in tasks]
        assert task_types.count("send_approval") == 1
        assert "slot_offer_approval" not in task_types
        draft = session.get(CommunicationDraft, referral.communication_draft_id)
        assert draft is not None
        assert draft.proposed_slots == [appointments[0].id]
        assert "date of birth" in draft.body

        detail = referral_detail(session, referral.id)
        packet = detail["workbench_state"]["email_workflow"]
        assert packet["next_action"] == "send_email"
        assert packet["held_appointment"]["id"] == appointments[0].id
        assert packet["draft"]["id"] == draft.id
        assert packet["facts"]["missing_fields"] == ["date_of_birth"]
    finally:
        session.rollback()
        session.close()


def test_workflow_filters_untrusted_dedupe_candidates() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Dedupe Filter Test", slug=_id("dedupe-filter"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral.",
            status="normalising",
            patient_name="Current Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        valid_duplicate = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Earlier email referral.",
            status="needs_admin_review",
            patient_name="Earlier Patient",
            contact_email="earlier@example.com",
        )
        document = Document(
            tenant_id=tenant.id,
            document_type="inbound_email_unmatched",
            title="Inbound Gmail",
            metadata_json={},
        )
        session.add(tenant)
        session.flush()
        session.add_all([referral, valid_duplicate, document])
        session.flush()
        run = WorkflowRun(
            id=_id("workflow"),
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="running",
            input_summary="Dedupe output",
            request_payload={"raw_input": {"source_channel": "email"}},
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
                    "referral": {
                        "patient_name": "Current Patient",
                        "dedupe_candidates": [referral.id, document.id, valid_duplicate.id],
                    }
                },
                "human_review_queue": [],
            },
            error=None,
        )

        session.refresh(referral)
        assert referral.duplicate_candidates == [valid_duplicate.id]
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.entity_id == referral.id,
                AuditLog.action == "discard_untrusted_dedupe_candidates",
            )
        )
        assert audit is not None
        assert referral.id in audit.after["discarded_candidates"]
        assert document.id in audit.after["discarded_candidates"]
    finally:
        session.rollback()
        session.close()


def test_email_slot_offer_body_is_normalized_to_one_slot_per_option(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Slot Body Test", slug=_id("slot-body"))
        therapist = Therapist(tenant_id=tenant.id, name="Dr. Clara Demo", availability_blocks=[])
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral.",
            status="match_approved",
            patient_name="Anna Anderson",
            contact_email="lumenpatientdemo@gmail.com",
            missing_fields=["date_of_birth", "insurer"],
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        start_one = datetime(2026, 6, 4, 9, 0, tzinfo=timezone.utc)
        start_two = datetime(2026, 6, 4, 10, 10, tzinfo=timezone.utc)
        options = []
        for index, starts_at in enumerate([start_one, start_two], start=1):
            options.append(
                {
                    "slot_id": f"{therapist.id}:slot-{index}",
                    "option_code": f"OPT{index}",
                    "option_number": index,
                    "therapist_id": therapist.id,
                    "starts_at": starts_at.isoformat(),
                    "ends_at": (starts_at + timedelta(minutes=60)).isoformat(),
                    "modality": "online",
                }
            )
        run = WorkflowRun(
            id=_id("workflow"),
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="running",
            input_summary="Grouped slots",
            request_payload={"raw_input": {"source_channel": "email", "appointment_options": options}},
            approvals={"match_approval": True},
        )
        session.add(run)
        session.flush()

        finish_workflow_run(
            session,
            job_id=run.id,
            status="needs_review",
            result={
                "outputs": {
                    "communication_draft": {
                        "channel": "email",
                        "subject": "Appointment availability",
                        "body": "Option 1: Dr. Clara Demo, Thursday at 09:00 or 10:10. Slot OPT1 / OPT2.",
                        "proposed_slots": ["OPT1", "OPT2"],
                        "requires_human_send": True,
                        "prohibited_content_check_passed": True,
                    }
                },
                "human_review_queue": [],
            },
            error=None,
        )

        draft = session.get(CommunicationDraft, referral.communication_draft_id)
        assert draft is not None
        assert len(draft.proposed_slots) == 2
        assert "Option 1: Dr. Clara Demo, Thursday, 2026-06-04 at 09:00." in draft.body
        assert "Option 2: Dr. Clara Demo, Thursday, 2026-06-04 at 10:10." in draft.body
        assert "09:00 or 10:10" not in draft.body
        assert "date of birth" in draft.body
        assert "insurer" in draft.body
    finally:
        session.rollback()
        session.close()


def test_email_referrals_reject_parallel_deterministic_actions(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Email Deterministic Guard", slug=_id("email-deterministic-guard"))
        therapist = Therapist(tenant_id=tenant.id, name="Guard Therapist", availability_blocks=[])
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral that must stay on LangGraph.",
            status="needs_admin_review",
            patient_name="Guard Patient",
            contact_email="lumenpatientdemo@gmail.com",
            missing_fields=["date_of_birth"],
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        session.add(
            Appointment(
                tenant_id=tenant.id,
                referral_id=referral.id,
                therapist_id=therapist.id,
                starts_at=datetime.now(timezone.utc) + timedelta(days=2),
                ends_at=datetime.now(timezone.utc) + timedelta(days=2, hours=1),
                status="proposed",
            )
        )
        session.flush()

        guarded = [
            lambda: prepare_email_referral_followup(session, referral.id),
            lambda: draft_missing_info_request(session, referral.id),
            lambda: deterministic_match_for_referral(session, referral.id),
            lambda: propose_appointment_slots(session, referral.id),
            lambda: draft_first_contact_message(session, referral.id),
        ]
        for action in guarded:
            with pytest.raises(ValueError, match="canonical LangGraph workflow"):
                action()
    finally:
        session.rollback()
        session.close()


def test_slot_contact_send_approval_waits_for_match_and_slot_approval(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Send Guard Test", slug=_id("send-guard"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral with a held slot.",
            status="awaiting_patient_contact",
            patient_name="Guard Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        session.add(tenant)
        session.flush()
        session.add(referral)
        session.flush()
        appointment = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            starts_at=datetime.now(timezone.utc) + timedelta(days=3),
            ends_at=datetime.now(timezone.utc) + timedelta(days=3, hours=1),
            status="proposed",
        )
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="First appointment options",
            body="Please confirm the held slot.",
            proposed_slots=[appointment.id],
            status="draft_pending_review",
        )
        session.add_all([appointment, draft])
        session.flush()
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            task_type="send_approval",
            status="open",
            reason="Approve patient contact.",
            payload_key=f"first_contact_draft:{draft.id[:8]}",
            source_payload={"id": draft.id},
            draft_text=draft.body,
        )
        session.add(task)
        session.flush()

        apply_review_action(session, task_id=task.id, action="approve")

        assert task.status == "open"
        assert "therapist match is approved" in task.source_payload["provider_error"]
        assert draft.status == "draft_pending_review"
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


def test_send_approval_route_does_not_submit_resume_workflow(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")
    submitted = []

    def fake_submit(request):
        submitted.append(request)
        return {"job_id": "unexpected-resume-job", "status": "queued"}

    monkeypatch.setattr(app_module, "jobs", SimpleNamespace(submit=fake_submit))

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="No Resume Send Test", slug=_id("no-resume-send"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Canonical email referral.",
            status="awaiting_patient_contact",
            patient_name="No Resume Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        run = WorkflowRun(
            id=_id("workflow"),
            tenant_id=tenant.id,
            workflow_type="new_referral",
            status="needs_review",
            input_summary="Send approval run",
            request_payload={"raw_input": {"source_channel": "email"}},
            approvals={"match_approval": True},
        )
        session.add(tenant)
        session.flush()
        session.add_all([referral, run])
        session.flush()
        run.referral_id = referral.id
        referral.workflow_run_id = run.id
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_run_id=run.id,
            channel="email",
            subject="Appointment option",
            body="Please confirm option 1.",
            status="draft_pending_review",
            proposed_slots=["canonical-slot-id"],
            recipient_email="lumenpatientdemo@gmail.com",
        )
        match_task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_run_id=run.id,
            task_type="match_approval",
            status="approved",
            reason="Match approved.",
            payload_key="match_recommendation",
            source_payload={},
        )
        session.add_all([draft, match_task])
        session.flush()
        task = HumanReviewTask(
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_run_id=run.id,
            task_type="send_approval",
            status="open",
            reason="Approve prepared email.",
            payload_key="communication_draft",
            source_payload={"id": draft.id},
            draft_text=draft.body,
        )
        session.add(task)
        session.commit()

        client = TestClient(app_module.app)
        response = client.post(f"/api/review-tasks/{task.id}/actions", json={"action": "approve", "final_text": "Approved body"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["resumed_job"] is None
        assert submitted == []

        refreshed = session.get(Referral, referral.id)
        refreshed_draft = session.get(CommunicationDraft, draft.id)
        refreshed_task = session.get(HumanReviewTask, task.id)
        session.refresh(refreshed)
        session.refresh(refreshed_draft)
        session.refresh(refreshed_task)
        assert refreshed.status == "contact_sent"
        assert refreshed_draft.status == "approved_pending_send"
        assert refreshed_draft.body == "Approved body"
        assert refreshed_task.status == "approved"
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


def test_approved_intake_packet_sends_with_gmail_and_stores_thread(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    send_calls = []

    def fake_send(**kwargs):
        send_calls.append(kwargs)
        return {"message_id": "intake-message-1", "thread_id": "intake-thread-1"}

    monkeypatch.setattr(google_workspace, "send_approved_draft", fake_send)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Intake Packet Send", slug=_id("intake-packet-send"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral with appointment confirmed.",
            status="appointment_confirmed",
            patient_name="Intake Packet Patient",
            contact_email="patient@example.com",
        )
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Intake packet template",
            required_items=[{"key": "intake_form", "label": "Intake form", "type": "form"}],
        )
        session.add(tenant)
        session.flush()
        session.add_all([referral, template])
        session.flush()
        file_meta = _template_file_meta("intake-form.txt")
        create_intake_template_file(
            session,
            template_id=template.id,
            item_key="intake_form",
            title=str(file_meta["file_name"]),
            storage_uri=str(file_meta["storage_uri"]),
            metadata=file_meta,
        )

        draft = draft_intake_packet(session, referral.id)
        task = session.scalar(
            select(HumanReviewTask).where(
                HumanReviewTask.referral_id == referral.id,
                HumanReviewTask.task_type == "send_approval",
            )
        )

        apply_review_action(session, task_id=task.id, action="approve")

        draft_row = session.get(CommunicationDraft, draft["id"])
        assert len(send_calls) == 1
        assert "reply to this same email thread" in send_calls[0]["body"]
        assert send_calls[0]["attachments"][0]["file_name"] == "intake-form.txt"
        assert task.status == "approved"
        assert draft_row.status == "sent"
        assert draft_row.gmail_message_id == "intake-message-1"
        assert draft_row.gmail_thread_id == "intake-thread-1"
        assert referral.status == "intake_packet_sent"
    finally:
        session.rollback()
        session.close()


def test_intake_packet_draft_lists_missing_template_files_and_send_blocks(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    send_calls = []
    monkeypatch.setattr(google_workspace, "send_approved_draft", lambda **kwargs: send_calls.append(kwargs))

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Missing Template Send", slug=_id("missing-template-send"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Referral with appointment confirmed.",
            status="appointment_confirmed",
            patient_name="Missing Template Patient",
            contact_email="patient@example.com",
        )
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Missing file template",
            required_items=[{"key": "intake_form", "label": "Intake form", "type": "form"}],
        )
        session.add(tenant)
        session.flush()
        session.add_all([referral, template])
        session.flush()

        draft = draft_intake_packet(session, referral.id)
        task = session.scalar(
            select(HumanReviewTask).where(
                HumanReviewTask.referral_id == referral.id,
                HumanReviewTask.task_type == "send_approval",
            )
        )

        apply_review_action(session, task_id=task.id, action="approve")

        draft_row = session.get(CommunicationDraft, draft["id"])
        assert draft["missing_template_files"][0]["item_key"] == "intake_form"
        assert send_calls == []
        assert task.status == "open"
        assert draft_row.status == "draft_pending_review"
        assert "Upload the missing files" in task.source_payload["provider_error"]
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


def test_demo_clara_confirmation_invites_clara_and_stays_visible_locally(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    create_calls = []

    def fake_create_event(**kwargs):
        create_calls.append(kwargs)
        return {"calendar_id": "primary", "event_id": "event-clara-1", "event_link": "https://calendar.example/clara"}

    monkeypatch.setattr(google_workspace, "create_appointment_event", fake_create_event)
    monkeypatch.setattr(google_workspace, "query_calendar_busy", lambda **kwargs: [])

    session = SessionLocal()
    try:
        payload = reset_clean_demo_referral(session)
        referral = session.get(Referral, payload["referral"]["id"])
        therapist = session.scalar(select(Therapist).where(Therapist.name == "Dr. Clara Demo"))
        assert therapist.email == "clara.demo1234@gmail.com"

        referral.status = "awaiting_patient_reply"
        starts_at = datetime.now(timezone.utc) + timedelta(days=5)
        appointment = Appointment(
            tenant_id=referral.tenant_id,
            referral_id=referral.id,
            patient_id=referral.patient_id,
            therapist_id=therapist.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=60),
            status="proposed",
        )
        session.add(appointment)
        session.flush()
        task = HumanReviewTask(
            tenant_id=referral.tenant_id,
            referral_id=referral.id,
            patient_id=referral.patient_id,
            task_type="appointment_confirmation_approval",
            status="open",
            reason="Approve accepted Clara slot.",
            payload_key=f"appointment_confirmation:{appointment.id[:8]}",
            source_payload={"appointment_id": appointment.id},
        )
        session.add(task)
        session.flush()

        apply_review_action(session, task_id=task.id, action="approve")

        assert create_calls[0]["therapist_email"] == "clara.demo1234@gmail.com"
        assert create_calls[0]["patient_email"] == "lumenpatientdemo@gmail.com"
        assert appointment.status == "confirmed"
        assert appointment.therapist_id == therapist.id
        assert appointment.google_calendar_event_id == "event-clara-1"
        capacity = therapist_calendar_capacity(session, tenant_id=referral.tenant_id)
        clara_summary = next(item for item in capacity["therapists"] if item["therapist_id"] == therapist.id)
        assert any(item["id"] == appointment.id and item["status"] == "confirmed" for item in clara_summary["active_appointments"])
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


def test_gmail_missing_info_reply_updates_referral_and_is_idempotent(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Gmail Missing Reply", slug=_id("gmail-missing-reply"))
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Gmail Missing Therapist",
            insurers=["Multicare"],
            availability_blocks=[{"weekday": "Tuesday", "start": "10:00", "end": "13:00"}],
        )
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral missing DOB and insurer.",
            status="waiting_for_missing_info",
            patient_name="Gmail Missing Patient",
            contact_email="lumenpatientdemo@gmail.com",
            missing_fields=["date_of_birth", "insurer"],
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        session.add(
            CommunicationDraft(
                tenant_id=tenant.id,
                referral_id=referral.id,
                channel="email",
                subject="Missing info",
                body="Please confirm DOB and insurer.",
                status="sent",
                recipient_email="lumenpatientdemo@gmail.com",
                gmail_thread_id="thread-missing-1",
                gmail_message_id="sent-missing-info-1",
            )
        )
        session.flush()

        message = {
            "message_id": "missing-reply-1",
            "thread_id": "thread-missing-1",
            "from": "Demo Patient <lumenpatientdemo@gmail.com>",
            "subject": "Re: missing info",
            "body": "DOB: 1990-01-01. Insurer: Multicare.",
        }
        first = ingest_gmail_message(session, tenant_id=tenant.id, message=message)
        second = ingest_gmail_message(session, tenant_id=tenant.id, message=message)

        assert first["action"] == "missing_info_reply"
        assert first["missing_updates"] == {"date_of_birth": "1990-01-01", "insurer": "Multicare"}
        assert second["status"] == "skipped"
        assert referral.date_of_birth == "1990-01-01"
        assert referral.insurer == "Multicare"
        assert referral.missing_fields == []
        assert referral.status == "ready_for_matching"
    finally:
        session.rollback()
        session.close()


def test_gmail_reply_extracts_referring_entity_from_reply_text(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Referrer Reply Test", slug=_id("referrer-reply"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral.",
            status="waiting_for_missing_info",
            patient_name="Ben Anderson",
            contact_email="lumenpatientdemo@gmail.com",
            missing_fields=["referring_entity"],
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
                subject="Missing information",
                body="Please provide referring entity.",
                status="sent",
                gmail_thread_id="thread-referrer-1",
                gmail_message_id="sent-referrer-1",
            )
        )
        session.flush()

        result = ingest_gmail_message(
            session,
            tenant_id=tenant.id,
            message={
                "message_id": "referrer-reply-1",
                "thread_id": "thread-referrer-1",
                "from": "Demo Patient <lumenpatientdemo@gmail.com>",
                "subject": "Re: missing information",
                "body": "Hello,\n\nReferring entity is \"National Health Service\"\n\nOn Sat, clinic wrote:\n> Please provide referring entity.",
            },
        )

        assert result["action"] == "missing_info_reply"
        assert result["missing_updates"] == {"referring_entity": "National Health Service"}
        assert referral.referring_entity == "National Health Service"
        assert referral.missing_fields == []
    finally:
        session.rollback()
        session.close()


def test_manual_missing_info_rejects_invalid_values() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Invalid Missing Info", slug=_id("invalid-missing"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Email referral.",
            status="waiting_for_missing_info",
            contact_email="lumenpatientdemo@gmail.com",
            missing_fields=["contact_email", "date_of_birth"],
        )
        session.add(tenant)
        session.flush()
        session.add(referral)
        session.flush()

        with pytest.raises(ValueError, match="valid email"):
            record_missing_info_reply(session, referral.id, updates={"contact_email": "Ben Anderson"})
        with pytest.raises(ValueError, match="valid past date"):
            record_missing_info_reply(session, referral.id, updates={"date_of_birth": "39/08/1987"})
        with pytest.raises(ValueError, match="actual value"):
            record_missing_info_reply(session, referral.id, updates={"insurer": "Already captured"})
    finally:
        session.rollback()
        session.close()


def test_gmail_reply_can_update_missing_info_and_auto_confirm_accepted_slot(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    calendar_calls = []

    def fake_create_event(**kwargs):
        calendar_calls.append(kwargs)
        return {"calendar_id": "primary", "event_id": "event-gmail-accept", "event_link": "https://calendar.example/gmail"}

    monkeypatch.setattr(google_workspace, "query_calendar_busy", lambda **kwargs: [])
    monkeypatch.setattr(google_workspace, "create_appointment_event", fake_create_event)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Gmail Accept Reply", slug=_id("gmail-accept-reply"))
        therapist = Therapist(tenant_id=tenant.id, name="Gmail Therapist", email="therapist@example.com")
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral with held slot.",
            status="contact_sent",
            patient_name="Gmail Accept Patient",
            contact_email="lumenpatientdemo@gmail.com",
            missing_fields=["date_of_birth"],
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=4)
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
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="First appointment options",
            body="Option 1 works?",
            status="sent",
            proposed_slots=[appointment.id],
            gmail_thread_id="thread-accepted-1",
            gmail_message_id="sent-accepted-1",
        )
        session.add(draft)
        session.flush()

        result = ingest_gmail_message(
            session,
            tenant_id=tenant.id,
            message={
                "message_id": "accepted-reply-1",
                "thread_id": "thread-accepted-1",
                "from": "Demo Patient <lumenpatientdemo@gmail.com>",
                "subject": "Re: first appointment",
                "body": "DOB: 1990-01-01. I can attend option 1.",
            },
        )

        assert result["action"] == "appointment_auto_confirmed"
        assert result["reply_type"] == "accepted_slot"
        assert result["missing_updates"] == {"date_of_birth": "1990-01-01"}
        assert referral.date_of_birth == "1990-01-01"
        assert referral.missing_fields == []
        assert appointment.status == "confirmed"
        assert appointment.google_calendar_event_id == "event-gmail-accept"
        assert len(calendar_calls) == 1
    finally:
        session.rollback()
        session.close()


def test_gmail_ambiguous_slot_reply_requires_admin_choice_then_confirms(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "true")
    calendar_calls = []

    def fake_create_event(**kwargs):
        calendar_calls.append(kwargs)
        return {"calendar_id": "primary", "event_id": "event-selected-slot", "event_link": "https://calendar.example/selected"}

    monkeypatch.setattr(google_workspace, "query_calendar_busy", lambda **kwargs: [])
    monkeypatch.setattr(google_workspace, "create_appointment_event", fake_create_event)

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Gmail Ambiguous Slot", slug=_id("gmail-ambiguous-slot"))
        therapist = Therapist(tenant_id=tenant.id, name="Dr. Clara Demo", email="clara.demo1234@gmail.com")
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral with two held slots.",
            status="contact_sent",
            patient_name="Anna Anderson",
            contact_email="lumenpatientdemo@gmail.com",
            missing_fields=[],
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        start_one = datetime(2026, 6, 4, 9, 0, tzinfo=timezone.utc)
        start_two = datetime(2026, 6, 4, 10, 10, tzinfo=timezone.utc)
        appointment_one = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            starts_at=start_one,
            ends_at=start_one + timedelta(minutes=60),
            status="proposed",
        )
        appointment_two = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            starts_at=start_two,
            ends_at=start_two + timedelta(minutes=60),
            status="proposed",
        )
        session.add_all([appointment_one, appointment_two])
        session.flush()
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="Appointment options",
            body="Option 1: 09:00\nOption 2: 10:10",
            status="sent",
            proposed_slots=[appointment_one.id, appointment_two.id],
            gmail_thread_id="thread-ambiguous-1",
            gmail_message_id="sent-ambiguous-1",
        )
        session.add(draft)
        session.flush()

        result = ingest_gmail_message(
            session,
            tenant_id=tenant.id,
            message={
                "message_id": "ambiguous-reply-1",
                "thread_id": "thread-ambiguous-1",
                "from": "Demo Patient <lumenpatientdemo@gmail.com>",
                "subject": "Re: appointment options",
                "body": "Can I proceed with Dr. Clara Demo, Thursday, online session at 09:00 or 10:10?\n\nOn Monday wrote:\n> Option 1: 09:00\n> Option 2: 10:10",
            },
        )

        assert result["action"] == "reply_resolution_required"
        assert result["reply_type"] == "ambiguous_slot"
        task = session.scalar(
            select(HumanReviewTask).where(
                HumanReviewTask.referral_id == referral.id,
                HumanReviewTask.task_type == "inbound_reply_review",
                HumanReviewTask.status == "open",
            )
        )
        assert task is not None
        assert task.source_payload["candidate_appointment_ids"] == [appointment_one.id, appointment_two.id]

        apply_review_action(session, task_id=task.id, action="approve", appointment_id=appointment_two.id)

        session.refresh(appointment_one)
        session.refresh(appointment_two)
        assert appointment_one.status == "proposed"
        assert appointment_two.status == "confirmed"
        assert appointment_two.google_calendar_event_id == "event-selected-slot"
        assert len(calendar_calls) == 1
    finally:
        session.rollback()
        session.close()


def test_gmail_alternative_reply_supersedes_hold_and_prepares_rebooking(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Gmail Alternative Reply", slug=_id("gmail-alt-reply"))
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Alternative Therapist",
            availability_blocks=[{"weekday": "Wednesday", "start": "10:00", "end": "13:00"}],
        )
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral for online therapy. Patient prefers Wednesday 10:00 to 12:00.",
            status="contact_sent",
            patient_name="Alternative Patient",
            contact_email="lumenpatientdemo@gmail.com",
            match_summary={"ranked_matches": [{"therapist_id": ""}]},
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral])
        session.flush()
        referral.match_summary = {"ranked_matches": [{"therapist_id": therapist.id}]}
        session.add(
            HumanReviewTask(
                tenant_id=tenant.id,
                referral_id=referral.id,
                task_type="match_approval",
                status="approved",
                reason="Approved match.",
                payload_key="match_recommendation",
                source_payload=referral.match_summary,
            )
        )
        old_start = datetime.now(timezone.utc) + timedelta(days=2)
        old_appointment = Appointment(
            tenant_id=tenant.id,
            referral_id=referral.id,
            therapist_id=therapist.id,
            starts_at=old_start,
            ends_at=old_start + timedelta(minutes=60),
            status="proposed",
        )
        session.add(old_appointment)
        session.flush()
        old_draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="First appointment options",
            body="Option 1 works?",
            status="sent",
            proposed_slots=[old_appointment.id],
            gmail_thread_id="thread-alt-1",
            gmail_message_id="sent-alt-1",
        )
        session.add(old_draft)
        session.flush()

        result = ingest_gmail_message(
            session,
            tenant_id=tenant.id,
            message={
                "message_id": "alternative-reply-1",
                "thread_id": "thread-alt-1",
                "from": "Demo Patient <lumenpatientdemo@gmail.com>",
                "subject": "Re: first appointment",
                "body": "I cannot do that time. I can do Wednesday 10:00 to 12:00.",
            },
        )

        appointments = list(session.scalars(select(Appointment).where(Appointment.referral_id == referral.id)))
        assert result["action"] == "rebooking_requested"
        assert result["replacement"] is None
        assert old_appointment.status == "cancelled"
        assert not any(item.status == "proposed" and item.id != old_appointment.id for item in appointments)
        assert session.scalar(
            select(HumanReviewTask).where(
                HumanReviewTask.referral_id == referral.id,
                HumanReviewTask.task_type == "inbound_reply_review",
                HumanReviewTask.status == "open",
            )
        )
    finally:
        session.rollback()
        session.close()


def test_gmail_intake_reply_with_attachment_creates_document_review_and_is_idempotent(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Intake Attachment Test", slug=_id("intake-attachment"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral with intake packet sent.",
            status="appointment_confirmed",
            patient_name="Attachment Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Attachment template",
            required_items=[{"key": "intake_form", "label": "Intake form", "type": "form"}],
        )
        session.add(tenant)
        session.flush()
        session.add_all([referral, template])
        session.flush()
        start_intake_for_referral(session, referral.id)
        referral.status = "intake_packet_sent"
        draft = CommunicationDraft(
            tenant_id=tenant.id,
            referral_id=referral.id,
            channel="email",
            subject="Intake packet",
            body="Please reply to this same email thread with files attached.",
            status="sent",
            gmail_thread_id="intake-thread-attach",
            gmail_message_id="sent-intake-attach",
        )
        session.add(draft)
        session.flush()

        message = {
            "message_id": "intake-attachment-reply-1",
            "thread_id": "intake-thread-attach",
            "from": "Demo Patient <lumenpatientdemo@gmail.com>",
            "subject": "Re: Intake packet",
            "body": "Completed form attached.",
            "attachments": [
                {
                    "attachment_id": "gmail-attachment-1",
                    "file_name": "intake.txt",
                    "mime_type": "text/plain",
                    "content_type": "text/plain",
                    "size_bytes": 24,
                    "storage_uri": "storage/uploads/intake/test/intake.txt",
                    "sha256": "abc123",
                    "extracted_text": "Completed intake details",
                    "download_status": "stored",
                }
            ],
        }

        first = ingest_gmail_message(session, tenant_id=tenant.id, message=message)
        second = ingest_gmail_message(session, tenant_id=tenant.id, message=message)

        assert first["action"] == "intake_submission_review"
        assert len(first["document_ids"]) == 1
        assert second["status"] == "skipped"
        document = session.get(Document, first["document_ids"][0])
        assert document.document_type == "intake_submission"
        task = session.get(HumanReviewTask, first["task_id"])
        assert task.task_type == "intake_submission_review"
        assert task.source_payload["document_id"] == document.id
        assert task.source_payload["missing_intake_items"][0]["label"] == "Intake form"
    finally:
        session.rollback()
        session.close()


def test_gmail_demo_intake_reply_auto_maps_completed_forms_and_marks_ready(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Demo Intake Auto Map", slug=_id("demo-intake-auto"))
        therapist = Therapist(tenant_id=tenant.id, name="Demo Intake Therapist")
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral with the Gmail demo intake packet sent.",
            status="appointment_confirmed",
            patient_name="Demo Intake Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Demo auto-map template",
            required_items=repository_module.DEMO_GMAIL_INTAKE_REQUIRED_ITEMS,
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral, template])
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=4)
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
        session.flush()
        start_intake_for_referral(session, referral.id)
        referral.status = "intake_packet_sent"
        session.add(
            CommunicationDraft(
                tenant_id=tenant.id,
                referral_id=referral.id,
                channel="email",
                subject="Intake packet",
                body="Please reply to this same email thread with files attached.",
                status="sent",
                gmail_thread_id="intake-thread-demo-auto",
                gmail_message_id="sent-intake-demo-auto",
            )
        )
        session.flush()

        attachments = [
            "completed_privacy_notice_acknowledged.docx",
            "telehealth_consent_completed.docx",
            "completed_clinical_intake_form.docx",
            "pre_session_screening_questionnaire_completed.docx",
        ]
        result = ingest_gmail_message(
            session,
            tenant_id=tenant.id,
            message={
                "message_id": "intake-demo-auto-reply-1",
                "thread_id": "intake-thread-demo-auto",
                "from": "Demo Patient <lumenpatientdemo@gmail.com>",
                "subject": "Re: Intake packet",
                "body": "Completed forms attached.",
                "attachments": [
                    {
                        "file_name": file_name,
                        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "storage_uri": f"storage/uploads/intake/test/{file_name}",
                        "sha256": f"sha-{index}",
                        "extracted_text": "Completed demo form",
                        "download_status": "stored",
                    }
                    for index, file_name in enumerate(attachments, start=1)
                ],
            },
        )

        session.refresh(referral)
        items = list(session.scalars(select(IntakeChecklistItem).where(IntakeChecklistItem.referral_id == referral.id)))
        consents = list(session.scalars(select(ConsentRecord).where(ConsentRecord.patient_id == referral.patient_id)))
        review_tasks = list(session.scalars(select(HumanReviewTask).where(HumanReviewTask.referral_id == referral.id)))

        assert result["action"] == "intake_submission_review"
        assert len(result["document_ids"]) == 4
        assert result["auto_mapping"]["completed_all_required"] is True
        assert {item.item_key: item.status for item in items} == {
            "privacy_notice": "completed",
            "telehealth_consent": "completed",
            "intake_form": "completed",
            "screening_questionnaire": "completed",
        }
        assert {consent.scope: consent.status for consent in consents} == {
            "privacy_notice": "completed",
            "telehealth": "completed",
        }
        assert all(task.status == "completed" for task in review_tasks if task.task_type == "intake_submission_review")
        assert session.scalar(select(TherapistPrepBrief).where(TherapistPrepBrief.referral_id == referral.id)) is not None
        assert referral.status == "first_session_ready"
    finally:
        session.rollback()
        session.close()


def test_intake_submission_approval_completes_intake_generates_prep_and_ready(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Intake Ready Test", slug=_id("intake-ready"))
        therapist = Therapist(tenant_id=tenant.id, name="Ready Therapist")
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral ready after intake.",
            status="appointment_confirmed",
            patient_name="Ready Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Ready template",
            required_items=[{"key": "intake_form", "label": "Intake form", "type": "form"}],
        )
        session.add(tenant)
        session.flush()
        session.add_all([therapist, referral, template])
        session.flush()
        starts_at = datetime.now(timezone.utc) + timedelta(days=4)
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
        session.flush()
        start_intake_for_referral(session, referral.id)
        referral.status = "intake_packet_sent"
        item = session.scalar(select(IntakeChecklistItem).where(IntakeChecklistItem.referral_id == referral.id))
        session.add(
            CommunicationDraft(
                tenant_id=tenant.id,
                referral_id=referral.id,
                channel="email",
                subject="Intake packet",
                body="Please reply to this same email thread with files attached.",
                status="sent",
                gmail_thread_id="intake-thread-ready",
                gmail_message_id="sent-intake-ready",
            )
        )
        session.flush()

        result = ingest_gmail_message(
            session,
            tenant_id=tenant.id,
            message={
                "message_id": "intake-ready-reply-1",
                "thread_id": "intake-thread-ready",
                "from": "Demo Patient <lumenpatientdemo@gmail.com>",
                "subject": "Re: Intake packet",
                "body": "Completed form attached.",
                "attachments": [
                    {
                        "file_name": "intake.txt",
                        "mime_type": "text/plain",
                        "content_type": "text/plain",
                        "storage_uri": "storage/uploads/intake/test/intake-ready.txt",
                        "sha256": "ready123",
                        "extracted_text": "Completed intake details",
                        "download_status": "stored",
                    }
                ],
            },
        )
        task = session.get(HumanReviewTask, result["task_id"])
        document_id = result["document_ids"][0]

        apply_review_action(
            session,
            task_id=task.id,
            action="approve",
            document_id=document_id,
            intake_item_id=item.id,
        )

        session.refresh(item)
        session.refresh(referral)
        assert item.status == "completed"
        assert item.source_document_id == document_id
        assert session.scalar(select(TherapistPrepBrief).where(TherapistPrepBrief.referral_id == referral.id)) is not None
        assert referral.status == "first_session_ready"
    finally:
        session.rollback()
        session.close()


def test_json_questionnaire_intake_submission_records_response_and_score(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Questionnaire Attachment", slug=_id("questionnaire-attachment"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral with questionnaire.",
            status="appointment_confirmed",
            patient_name="Questionnaire Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Questionnaire template",
            required_items=[{"key": "screening", "label": "Screening questionnaire", "type": "questionnaire"}],
        )
        session.add(tenant)
        session.flush()
        session.add_all([referral, template])
        session.flush()
        start_intake_for_referral(session, referral.id)
        referral.status = "intake_packet_sent"
        item = session.scalar(select(IntakeChecklistItem).where(IntakeChecklistItem.referral_id == referral.id))
        session.add(
            CommunicationDraft(
                tenant_id=tenant.id,
                referral_id=referral.id,
                channel="email",
                subject="Intake packet",
                body="Please reply to this same email thread with files attached.",
                status="sent",
                gmail_thread_id="intake-thread-json",
                gmail_message_id="sent-intake-json",
            )
        )
        session.flush()

        result = ingest_gmail_message(
            session,
            tenant_id=tenant.id,
            message={
                "message_id": "intake-json-reply-1",
                "thread_id": "intake-thread-json",
                "from": "Demo Patient <lumenpatientdemo@gmail.com>",
                "subject": "Re: Intake packet",
                "body": "Questionnaire attached.",
                "attachments": [
                    {
                        "file_name": "screening.json",
                        "mime_type": "application/json",
                        "content_type": "application/json",
                        "storage_uri": "storage/uploads/intake/test/screening.json",
                        "sha256": "json123",
                        "extracted_text": "{\"answers\":{\"phq1\":2,\"phq2\":3}}",
                        "download_status": "stored",
                    }
                ],
            },
        )

        apply_review_action(
            session,
            task_id=result["task_id"],
            action="approve",
            document_id=result["document_ids"][0],
            intake_item_id=item.id,
            questionnaire_name="demo_screening",
        )

        response = session.scalar(select(QuestionnaireResponse).where(QuestionnaireResponse.referral_id == referral.id))
        score = session.scalar(select(ScoreRecord).where(ScoreRecord.referral_id == referral.id))
        assert response is not None
        assert response.questionnaire_name == "demo_screening"
        assert response.answers == {"phq1": 2, "phq2": 3}
        assert score.score_summary["total_score"] == 5
    finally:
        session.rollback()
        session.close()


def test_unsupported_intake_attachment_creates_reviewable_error(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")

    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Unsupported Attachment", slug=_id("unsupported-attachment"))
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="Referral with unsupported intake file.",
            status="intake_packet_sent",
            patient_name="Unsupported Patient",
            contact_email="lumenpatientdemo@gmail.com",
        )
        template = IntakeTemplate(
            tenant_id=tenant.id,
            name="Unsupported template",
            required_items=[{"key": "intake_form", "label": "Intake form", "type": "form"}],
        )
        session.add(tenant)
        session.flush()
        session.add_all([referral, template])
        session.flush()
        start_intake_for_referral(session, referral.id)
        referral.status = "intake_packet_sent"
        session.add(
            CommunicationDraft(
                tenant_id=tenant.id,
                referral_id=referral.id,
                channel="email",
                subject="Intake packet",
                body="Please reply to this same email thread with files attached.",
                status="sent",
                gmail_thread_id="intake-thread-bad",
                gmail_message_id="sent-intake-bad",
            )
        )
        session.flush()

        result = ingest_gmail_message(
            session,
            tenant_id=tenant.id,
            message={
                "message_id": "intake-bad-reply-1",
                "thread_id": "intake-thread-bad",
                "from": "Demo Patient <lumenpatientdemo@gmail.com>",
                "subject": "Re: Intake packet",
                "body": "File attached.",
                "attachments": [
                    {
                        "file_name": "intake.exe",
                        "mime_type": "application/octet-stream",
                        "download_status": "failed",
                        "error": "Unsupported file type.",
                    }
                ],
            },
        )

        assert result["action"] == "intake_submission_review"
        assert result["document_ids"] == []
        task = session.get(HumanReviewTask, result["task_id"])
        assert task.source_payload["attachment_errors"][0]["error"] == "Unsupported file type."
        assert not session.scalar(
            select(Document).where(
                Document.document_type == "intake_submission",
                Document.patient_id == referral.patient_id,
            )
        )
        item = session.scalar(select(IntakeChecklistItem).where(IntakeChecklistItem.referral_id == referral.id))
        assert item.status == "missing"
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
        for week in range(5):
            for index in range(20):
                starts_at = week_start + timedelta(days=7 * week + index // 4, hours=8 + (index % 4) * 2)
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
        appointment_start = datetime.now(timezone.utc) + timedelta(hours=2)
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
