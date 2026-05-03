"""Repository helpers for Lumen web records."""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    Appointment,
    AuditLog,
    ClinicalLibraryRecord,
    CommunicationDraft,
    ConsentRecord,
    Document,
    DocumentChunk,
    DraftFeedback,
    HumanReviewTask,
    IntakeChecklistItem,
    IntakeTemplate,
    Patient,
    QuestionnaireResponse,
    Referral,
    ReferralImportBatch,
    ReferralImportError,
    ReportDraft,
    ScoreRecord,
    SessionNote,
    Tenant,
    TherapistPrepBrief,
    Therapist,
    User,
    WorkflowEvent,
    WorkflowRun,
    new_id,
)
from .seed import DEMO_THERAPIST_USER_ID, DEMO_USER_ID
from .workflow_state import (
    canonical_referral_status,
    next_action_for_referral,
    next_action_label,
    secondary_flags_for_referral,
    status_filter_values,
    status_label,
    transition_referral_status,
)


ROLE_PERMISSIONS: dict[str, list[str]] = {
    "admin": [
        "referral:read",
        "referral:write",
        "review:admin",
        "intake:write",
        "import:write",
        "audit:read",
    ],
    "therapist": [
        "patient:read_assigned",
        "session_note:write",
        "report:edit",
        "report:sign_off",
        "feedback:write",
    ],
    "clinic_director": [
        "referral:read",
        "clinical_review:approve",
        "report:sign_off",
        "audit:read",
        "metrics:read",
    ],
    "compliance_owner": [
        "audit:read",
        "retention:read",
        "export:read",
        "security:read",
    ],
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump(mode="json"))
    return value


def iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def ensure_tenant(session: Session, tenant_id: str) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        tenant = Tenant(id=tenant_id, name=f"Clinic {tenant_id}", slug=tenant_id[:80])
        session.add(tenant)
        session.flush()
    return tenant


def ensure_patient(session: Session, tenant_id: str, patient_id: str | None) -> Patient | None:
    if not patient_id:
        return None
    patient = session.get(Patient, patient_id)
    if patient is not None:
        return patient
    patient = Patient(id=patient_id, tenant_id=tenant_id, display_name=f"Patient {patient_id[:8]}")
    session.add(patient)
    session.flush()
    write_audit(
        session,
        tenant_id=tenant_id,
        action="create_placeholder",
        entity_type="patient",
        entity_id=patient.id,
        after={"id": patient.id, "tenant_id": patient.tenant_id, "display_name": patient.display_name},
    )
    return patient


def create_referral_for_request(session: Session, request: Any) -> Referral | None:
    if request.workflow_type != "new_referral":
        return None

    raw_input = request.raw_input or {}
    referral = Referral(
        tenant_id=request.tenant_id,
        patient_id=request.patient_id,
        source_channel=str(raw_input.get("source_channel") or "webform"),
        raw_text=str(raw_input.get("raw_text") or ""),
        uploaded_file_name=raw_input.get("uploaded_file_name"),
        status="normalising",
        missing_fields=_deterministic_missing_fields(raw_input),
    )
    session.add(referral)
    session.flush()
    _ensure_admin_missing_info_task(session, referral)
    write_audit(
        session,
        tenant_id=request.tenant_id,
        action="create",
        entity_type="referral",
        entity_id=referral.id,
        after=referral_summary(referral),
    )
    return referral


def create_workflow_run(
    session: Session,
    *,
    job_id: str,
    request: Any,
    input_summary: str,
    referral_id: str | None,
) -> WorkflowRun:
    run = WorkflowRun(
        id=job_id,
        tenant_id=request.tenant_id,
        patient_id=request.patient_id,
        referral_id=referral_id,
        workflow_type=request.workflow_type,
        status="queued",
        input_summary=input_summary,
        request_payload={"raw_input": json_safe(request.raw_input)},
        approvals=json_safe(request.approvals),
    )
    session.add(run)
    session.flush()
    if referral_id:
        referral = session.get(Referral, referral_id)
        if referral:
            referral.workflow_run_id = job_id
    write_audit(
        session,
        tenant_id=request.tenant_id,
        action="create",
        entity_type="workflow_run",
        entity_id=job_id,
        after=workflow_run_summary(run),
    )
    return run


def update_workflow_status(session: Session, job_id: str, status: str) -> None:
    run = get_workflow_run(session, job_id)
    before = workflow_run_summary(run)
    run.status = status
    run.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=run.tenant_id,
        action="update_status",
        entity_type="workflow_run",
        entity_id=job_id,
        before=before,
        after=workflow_run_summary(run),
    )


def finish_workflow_run(
    session: Session,
    *,
    job_id: str,
    status: str,
    result: dict[str, Any] | None,
    error: str | None,
) -> WorkflowRun:
    run = get_workflow_run(session, job_id)
    before = workflow_run_summary(run)
    run.status = status
    run.result = json_safe(result)
    run.error = error
    run.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=run.tenant_id,
        action="finish",
        entity_type="workflow_run",
        entity_id=job_id,
        before=before,
        after=workflow_run_summary(run),
    )
    if run.referral_id:
        update_referral_from_result(session, run)
    persist_human_review_tasks(session, run)
    return run


def append_workflow_event(
    session: Session,
    *,
    job_id: str,
    event_type: str,
    status: str,
    message: str,
    node: str,
    agent: str | None = None,
    confidence: float | None = None,
    tools: list[str] | None = None,
    payload: Any | None = None,
) -> dict[str, Any]:
    run = get_workflow_run(session, job_id)
    next_index = session.scalar(select(func.max(WorkflowEvent.index)).where(WorkflowEvent.workflow_run_id == job_id))
    event = WorkflowEvent(
        tenant_id=run.tenant_id,
        workflow_run_id=job_id,
        index=0 if next_index is None else int(next_index) + 1,
        type=event_type,
        status=status,
        message=message,
        node=node,
        agent=agent,
        confidence=confidence,
        tools=tools or [],
        payload=json_safe(payload),
    )
    run.updated_at = utc_now()
    session.add(event)
    session.flush()
    write_audit(
        session,
        tenant_id=run.tenant_id,
        action="workflow_event",
        entity_type="workflow_run",
        entity_id=job_id,
        after=event_to_dict(event),
    )
    return event_to_dict(event)


def get_workflow_run(session: Session, job_id: str) -> WorkflowRun:
    run = session.scalar(
        select(WorkflowRun).where(WorkflowRun.id == job_id).options(selectinload(WorkflowRun.events))
    )
    if run is None:
        raise KeyError(f"Unknown workflow job: {job_id}")
    return run


def workflow_snapshot(session: Session, job_id: str) -> dict[str, Any]:
    run = get_workflow_run(session, job_id)
    return workflow_run_to_dict(run)


def workflow_events_since(session: Session, job_id: str, cursor: int) -> tuple[list[dict[str, Any]], str]:
    run = get_workflow_run(session, job_id)
    events = [
        event_to_dict(event)
        for event in session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == job_id, WorkflowEvent.index >= cursor)
            .order_by(WorkflowEvent.index)
        )
    ]
    return events, run.status


def list_workflow_runs(session: Session, tenant_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    query = select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(limit)
    if tenant_id:
        query = query.where(WorkflowRun.tenant_id == tenant_id)
    return [workflow_run_to_dict(run, include_events=False) for run in session.scalars(query)]


def list_referrals(session: Session, tenant_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    query = select(Referral).order_by(Referral.updated_at.desc())
    if tenant_id:
        query = query.where(Referral.tenant_id == tenant_id)
    if status:
        values = status_filter_values(status) or [status]
        query = query.where(Referral.status.in_(values))
    return [referral_summary(referral) for referral in session.scalars(query)]


def referral_detail(session: Session, referral_id: str) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")

    drafts = list(
        session.scalars(select(CommunicationDraft).where(CommunicationDraft.referral_id == referral_id).order_by(CommunicationDraft.created_at.desc()))
    )
    tasks = list(
        session.scalars(select(HumanReviewTask).where(HumanReviewTask.referral_id == referral_id).order_by(HumanReviewTask.created_at.desc()))
    )
    workflows = list(
        session.scalars(select(WorkflowRun).where(WorkflowRun.referral_id == referral_id).order_by(WorkflowRun.created_at.desc()))
    )
    detail = referral_summary(referral)
    detail.update(
        {
            "raw_text": referral.raw_text,
            "communication_drafts": [communication_draft_to_dict(draft) for draft in drafts],
            "review_tasks": [review_task_to_dict(task) for task in tasks],
            "workflow_runs": [workflow_run_to_dict(run, include_events=False) for run in workflows],
            "patient_replies": _referral_documents(session, referral, "patient_reply"),
            "missing_info_replies": _referral_documents(session, referral, "missing_info_reply"),
            "readiness_blockers": _first_session_readiness_blockers(session, referral),
        }
    )
    return detail


def import_referral_batch(
    session: Session,
    *,
    tenant_id: str,
    file_name: str,
    content_text: str,
    source_channel: str = "csv_import",
    storage_uri: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor_user_id: str | None = DEMO_USER_ID,
) -> dict[str, Any]:
    ensure_tenant(session, tenant_id)
    clean_channel = source_channel.strip() or "csv_import"
    document = Document(
        tenant_id=tenant_id,
        document_type="referral_import",
        title=file_name.strip() or "referral-import.csv",
        storage_uri=storage_uri,
        metadata_json=json_safe(
            {
                "parser": "csv_dict_reader_mvp",
                "source_channel": clean_channel,
                **(metadata or {}),
            }
        ),
    )
    session.add(document)
    session.flush()
    batch = ReferralImportBatch(
        tenant_id=tenant_id,
        source_channel=clean_channel,
        file_name=document.title,
        source_document_id=document.id,
        metadata_json={"source_document_id": document.id},
    )
    session.add(batch)
    session.flush()

    imported_referrals: list[Referral] = []
    reader = csv.DictReader(io.StringIO(content_text))
    if not reader.fieldnames:
        _record_import_error(session, batch, 1, "CSV header row is required.", {})
    else:
        for row_number, row in enumerate(reader, start=2):
            normalized_row = {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
            try:
                referral = _referral_from_import_row(session, tenant_id, clean_channel, normalized_row)
            except ValueError as exc:
                _record_import_error(session, batch, row_number, str(exc), normalized_row)
                continue
            imported_referrals.append(referral)

    batch.total_rows = len(imported_referrals) + int(
        session.scalar(select(func.count(ReferralImportError.id)).where(ReferralImportError.batch_id == batch.id)) or 0
    )
    batch.imported_count = len(imported_referrals)
    batch.error_count = batch.total_rows - batch.imported_count
    if batch.error_count and batch.imported_count:
        batch.status = "partial"
    elif batch.error_count:
        batch.status = "failed"
    else:
        batch.status = "completed"
    batch.updated_at = utc_now()

    write_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id if session.get(User, actor_user_id or "") else None,
        action="import_referral_batch",
        entity_type="referral_import_batch",
        entity_id=batch.id,
        after=referral_import_batch_to_dict(batch),
    )
    return {
        "batch": referral_import_batch_to_dict(batch),
        "referrals": [referral_summary(referral) for referral in imported_referrals],
        "errors": list_referral_import_errors(session, tenant_id=tenant_id, batch_id=batch.id),
    }


def create_email_referral(
    session: Session,
    *,
    tenant_id: str,
    sender: str,
    subject: str,
    body: str,
    actor_user_id: str | None = DEMO_USER_ID,
) -> dict[str, Any]:
    ensure_tenant(session, tenant_id)
    clean_body = body.strip()
    if not clean_body:
        raise ValueError("Email body is required.")
    raw_text = "\n".join(
        part
        for part in [
            f"From: {sender.strip()}" if sender.strip() else "",
            f"Subject: {subject.strip()}" if subject.strip() else "",
            clean_body,
        ]
        if part
    )
    document = Document(
        tenant_id=tenant_id,
        document_type="email_referral",
        title=subject.strip() or f"Email referral {utc_now().date().isoformat()}",
        metadata_json={"sender": sender.strip(), "subject": subject.strip(), "parser": "email_body_mvp"},
    )
    referral = Referral(
        tenant_id=tenant_id,
        source_channel="email",
        raw_text=raw_text,
        contact_email=sender.strip() if "@" in sender else None,
        status="new_referral",
        missing_fields=_deterministic_missing_fields({"raw_text": raw_text}),
    )
    session.add_all([document, referral])
    session.flush()
    _ensure_admin_missing_info_task(session, referral)
    write_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id if session.get(User, actor_user_id or "") else None,
        action="ingest_email_referral",
        entity_type="referral",
        entity_id=referral.id,
        after={"referral": referral_summary(referral), "document": document_to_dict(document)},
    )
    return {"referral": referral_summary(referral), "document": document_to_dict(document)}


def list_referral_import_batches(
    session: Session,
    tenant_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    query = select(ReferralImportBatch).order_by(ReferralImportBatch.created_at.desc()).limit(max(1, min(limit, 100)))
    if tenant_id:
        query = query.where(ReferralImportBatch.tenant_id == tenant_id)
    return [referral_import_batch_to_dict(batch) for batch in session.scalars(query)]


def list_referral_import_errors(
    session: Session,
    tenant_id: str | None = None,
    batch_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = select(ReferralImportError).order_by(ReferralImportError.created_at.desc()).limit(max(1, min(limit, 100)))
    if tenant_id:
        query = query.where(ReferralImportError.tenant_id == tenant_id)
    if batch_id:
        query = query.where(ReferralImportError.batch_id == batch_id)
    return [referral_import_error_to_dict(error) for error in session.scalars(query)]


def integration_health(session: Session, tenant_id: str | None = None) -> dict[str, Any]:
    tenant = tenant_id or "demo-clinic"
    recent_import = session.scalar(
        select(ReferralImportBatch)
        .where(ReferralImportBatch.tenant_id == tenant)
        .order_by(ReferralImportBatch.created_at.desc())
        .limit(1)
    )
    return {
        "checks": [
            {
                "name": "CSV referral import",
                "status": "ok",
                "message": "CSV batch ingestion is available and creates queued referral records.",
                "last_seen": iso_or_none(recent_import.created_at) if recent_import else None,
            },
            {
                "name": "Email referral capture",
                "status": "configured" if os.getenv("LUMEN_EMAIL_INGESTION_ENABLED") else "manual",
                "message": "Manual email capture endpoint is available; provider webhook/polling can feed it.",
                "last_seen": None,
            },
            {
                "name": "Calendar availability",
                "status": "manual",
                "message": "Availability blocks are managed in therapist profiles for this MVP.",
                "last_seen": None,
            },
            {
                "name": "Outbound email",
                "status": "configured" if os.getenv("SMTP_HOST") else "not_configured",
                "message": "Outbound sending remains disabled until SMTP is configured and send approval is recorded.",
                "last_seen": None,
            },
        ]
    }


def security_context(session: Session, user_id: str | None = DEMO_USER_ID) -> dict[str, Any]:
    user = session.get(User, user_id or "") if user_id else None
    if user is None:
        user = session.get(User, DEMO_USER_ID)
    if user is None:
        return {"user": None, "permissions": [], "tenant_id": None}
    return {
        "user": user_to_dict(user),
        "permissions": ROLE_PERMISSIONS.get(user.role, []),
        "tenant_id": user.tenant_id,
    }


def governance_posture(session: Session, tenant_id: str | None = None) -> dict[str, Any]:
    tenant = tenant_id or "demo-clinic"
    audit_count = int(session.scalar(select(func.count(AuditLog.id)).where(AuditLog.tenant_id == tenant)) or 0)
    open_review_count = int(
        session.scalar(
            select(func.count(HumanReviewTask.id)).where(
                HumanReviewTask.tenant_id == tenant,
                HumanReviewTask.status == "open",
            )
        )
        or 0
    )
    signed_reports = int(
        session.scalar(
            select(func.count(ReportDraft.id)).where(
                ReportDraft.tenant_id == tenant,
                ReportDraft.status == "signed_off",
            )
        )
        or 0
    )
    return {
        "tenant_id": tenant,
        "audit_events": audit_count,
        "open_review_tasks": open_review_count,
        "signed_reports": signed_reports,
        "retention_policy": {
            "default_days": int(os.getenv("LUMEN_RETENTION_DAYS", "2555")),
            "delete_requires_review": True,
            "audit_log_policy": "append_only_application_policy",
        },
        "model_data_policy": {
            "provider": os.getenv("LUMEN_LLM_PROVIDER", "ollama"),
            "send_phi_to_external_provider": os.getenv("LUMEN_ALLOW_EXTERNAL_PHI", "false").lower() == "true",
        },
    }


def list_review_tasks(session: Session, tenant_id: str | None = None, status: str | None = "open") -> list[dict[str, Any]]:
    query = select(HumanReviewTask).order_by(HumanReviewTask.created_at.desc())
    if tenant_id:
        query = query.where(HumanReviewTask.tenant_id == tenant_id)
    if status and status != "all":
        query = query.where(HumanReviewTask.status == status)
    return [review_task_to_dict(task) for task in session.scalars(query)]


def create_review_task(
    session: Session,
    *,
    tenant_id: str,
    task_type: str,
    reason: str,
    payload_key: str,
    source_payload: Any | None = None,
    draft_text: str | None = None,
    referral_id: str | None = None,
    patient_id: str | None = None,
    workflow_run_id: str | None = None,
) -> HumanReviewTask:
    query = select(HumanReviewTask).where(
        HumanReviewTask.tenant_id == tenant_id,
        HumanReviewTask.task_type == task_type,
        HumanReviewTask.payload_key == payload_key,
        HumanReviewTask.status == "open",
    )
    if referral_id:
        query = query.where(HumanReviewTask.referral_id == referral_id)
    if workflow_run_id:
        query = query.where(HumanReviewTask.workflow_run_id == workflow_run_id)
    existing = session.scalar(query)
    if existing is not None:
        return existing

    task = HumanReviewTask(
        tenant_id=tenant_id,
        workflow_run_id=workflow_run_id,
        referral_id=referral_id,
        patient_id=patient_id,
        task_type=task_type,
        reason=reason,
        payload_key=payload_key,
        source_payload=json_safe(source_payload),
        draft_text=draft_text if draft_text is not None else _draft_text_for_payload(source_payload),
    )
    session.add(task)
    session.flush()
    write_audit(
        session,
        tenant_id=tenant_id,
        action="create",
        entity_type="human_review_task",
        entity_id=task.id,
        after=review_task_to_dict(task),
    )
    return task


def draft_missing_info_request(
    session: Session,
    referral_id: str,
    *,
    recipient: str = "patient",
    note: str = "",
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    missing_fields = list(referral.missing_fields or [])
    if not missing_fields:
        raise ValueError("Referral has no recorded missing fields.")

    patient = _ensure_patient_for_referral(session, referral)
    _ensure_admin_missing_info_task(session, referral)
    recipient_label = {
        "patient": "patient",
        "referrer": "referrer",
        "internal_admin": "clinic admin team",
    }.get(recipient, "patient")
    field_lines = [f"- {field.replace('_', ' ')}" for field in missing_fields]
    note_lines = ["", "Clinic note:", note.strip()] if note.strip() else []
    body = "\n".join(
        [
            f"Hello {referral.patient_name or patient.display_name or 'there'},",
            "",
            "We need a little more information before we can continue this referral.",
            "",
            "Missing information:",
            *field_lines,
            *note_lines,
            "",
            "This is a simulated draft. Clinic staff must review and send it manually in this prototype.",
        ]
    )
    draft = CommunicationDraft(
        tenant_id=referral.tenant_id,
        referral_id=referral.id,
        patient_id=patient.id,
        workflow_run_id=None,
        channel="email",
        subject=f"Missing information for {recipient_label} referral",
        body=body,
        status="draft_pending_review",
        proposed_slots=[],
        requires_human_send=True,
    )
    session.add(draft)
    session.flush()
    referral.communication_draft_id = draft.id
    transition_referral_status(
        session,
        referral,
        "needs_admin_review",
        reason="Missing-information message drafted for review.",
    )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="communication_draft",
        entity_id=draft.id,
        after=communication_draft_to_dict(draft),
    )
    create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=None,
        referral_id=referral.id,
        patient_id=patient.id,
        task_type="missing_info_message_approval",
        reason="Missing-information message requires staff approval before simulated send.",
        payload_key=f"missing_info_message:{draft.id[:8]}",
        source_payload=communication_draft_to_dict(draft),
        draft_text=draft.body,
    )
    return communication_draft_to_dict(draft)


def create_clinical_escalation_review(
    session: Session,
    referral_id: str,
    *,
    reason: str = "Clinical risk or suitability review is required before matching.",
) -> HumanReviewTask:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    transition_referral_status(
        session,
        referral,
        "clinical_escalation_review",
        reason=reason,
    )
    return create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=referral.id,
        patient_id=patient.id,
        task_type="clinical_risk_review",
        reason=reason,
        payload_key="risk_review",
        source_payload={
            "risk_category": referral.risk_category,
            "urgency": referral.urgency,
            "risk_present": referral.risk_present,
            "reason": reason,
        },
    )


def record_simulated_patient_reply(
    session: Session,
    referral_id: str,
    *,
    reply_type: str,
    appointment_id: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    allowed = {"accepted_slot", "declined", "alternative_requested", "asked_question", "unclear", "no_response"}
    if reply_type not in allowed:
        raise ValueError(f"Unsupported patient reply type: {reply_type}")

    appointment = None
    if appointment_id:
        appointment = session.get(Appointment, appointment_id)
        if appointment is None:
            raise KeyError(f"Unknown appointment: {appointment_id}")
        if appointment.referral_id != referral.id:
            raise ValueError("Appointment does not belong to this referral.")
    elif reply_type == "accepted_slot":
        raise ValueError("Accepted slot replies require an appointment_id.")

    document = Document(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        document_type="patient_reply",
        title=f"Simulated patient reply: {reply_type.replace('_', ' ')}",
        metadata_json={
            "simulation": True,
            "reply_type": reply_type,
            "appointment_id": appointment_id,
            "notes": notes.strip(),
            "referral_id": referral.id,
        },
    )
    session.add(document)
    session.flush()
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="record_simulated_patient_reply",
        entity_type="document",
        entity_id=document.id,
        after=document_to_dict(document),
    )

    task: HumanReviewTask | None = None
    if reply_type == "accepted_slot":
        transition_referral_status(
            session,
            referral,
            "awaiting_patient_reply",
            reason="Simulated patient accepted a proposed slot; confirmation requires review.",
        )
        task = create_review_task(
            session,
            tenant_id=referral.tenant_id,
            workflow_run_id=referral.workflow_run_id,
            referral_id=referral.id,
            patient_id=patient.id,
            task_type="appointment_confirmation_approval",
            reason="Patient accepted a proposed slot; approve to confirm the appointment record.",
            payload_key=f"appointment_confirmation:{appointment.id[:8] if appointment else 'manual'}",
            source_payload={
                "reply_type": reply_type,
                "appointment_id": appointment_id,
                "patient_reply_document_id": document.id,
                "notes": notes.strip(),
            },
        )
    elif reply_type == "declined":
        transition_referral_status(session, referral, "closed_declined", reason="Simulated patient declined offered slots.")
    elif reply_type == "alternative_requested":
        transition_referral_status(session, referral, "slot_options_ready", reason="Simulated patient requested alternative slots.")
    elif reply_type == "no_response":
        transition_referral_status(session, referral, "closed_no_response", reason="Simulated no-response outcome recorded.")
    else:
        transition_referral_status(session, referral, "needs_admin_review", reason="Simulated patient reply needs admin review.")

    return {
        "reply": document_to_dict(document),
        "task": review_task_to_dict(task) if task else None,
        "referral": referral_summary(referral),
    }


def request_intake_item_exception(
    session: Session,
    item_id: str,
    *,
    reason: str = "Authorised exception requested by clinic admin.",
) -> HumanReviewTask:
    item = session.get(IntakeChecklistItem, item_id)
    if item is None:
        raise KeyError(f"Unknown intake checklist item: {item_id}")
    if _intake_done(item.status):
        raise ValueError("Completed or waived intake items do not need an exception.")
    return create_review_task(
        session,
        tenant_id=item.tenant_id,
        referral_id=item.referral_id,
        patient_id=item.patient_id,
        task_type="intake_exception_approval",
        reason=reason,
        payload_key=f"intake_exception_item:{item.id[:8]}",
        source_payload={
            "target_type": "intake_item",
            "item_id": item.id,
            "label": item.label,
            "reason": reason,
        },
        draft_text=reason,
    )


def request_consent_exception(
    session: Session,
    consent_id: str,
    *,
    reason: str = "Authorised exception requested by clinic admin.",
) -> HumanReviewTask:
    consent = session.get(ConsentRecord, consent_id)
    if consent is None:
        raise KeyError(f"Unknown consent record: {consent_id}")
    if _intake_done(consent.status):
        raise ValueError("Completed or waived consent records do not need an exception.")
    referral_id = _latest_referral_id_for_patient(session, consent.tenant_id, consent.patient_id)
    return create_review_task(
        session,
        tenant_id=consent.tenant_id,
        referral_id=referral_id,
        patient_id=consent.patient_id,
        task_type="intake_exception_approval",
        reason=reason,
        payload_key=f"intake_exception_consent:{consent.id[:8]}",
        source_payload={
            "target_type": "consent_record",
            "consent_id": consent.id,
            "scope": consent.scope,
            "reason": reason,
        },
        draft_text=reason,
    )


def record_missing_info_reply(
    session: Session,
    referral_id: str,
    *,
    source: str = "patient",
    updates: dict[str, Any] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    clean_updates = _clean_missing_info_updates(updates or {})
    if not clean_updates and not notes.strip():
        raise ValueError("Missing-information reply requires updates or notes.")
    before = referral_summary(referral)
    _apply_referral_updates(referral, clean_updates)
    referral.missing_fields = _remaining_missing_fields(referral.missing_fields, clean_updates)
    referral.updated_at = utc_now()
    patient = _ensure_patient_for_referral(session, referral)
    document = Document(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        document_type="missing_info_reply",
        title=f"Missing information reply from {source}",
        metadata_json={
            "referral_id": referral.id,
            "source": source,
            "updates": json_safe(clean_updates),
            "notes": notes.strip(),
            "remaining_missing_fields": list(referral.missing_fields or []),
        },
    )
    session.add(document)
    session.flush()
    transition_referral_status(
        session,
        referral,
        _next_admin_gate_status(referral),
        reason="Missing-information reply recorded.",
    )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="record_missing_info_reply",
        entity_type="referral",
        entity_id=referral.id,
        before=before,
        after=referral_summary(referral),
    )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="document",
        entity_id=document.id,
        after=document_to_dict(document),
    )
    return {"reply": document_to_dict(document), "referral": referral_summary(referral)}


def create_duplicate_resolution_review(
    session: Session,
    referral_id: str,
    *,
    candidate_referral_id: str | None = None,
    reason: str = "Potential duplicate referral requires admin resolution before matching.",
) -> HumanReviewTask:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    candidates = list(referral.duplicate_candidates or [])
    if candidate_referral_id and candidate_referral_id not in candidates:
        candidates.append(candidate_referral_id)
        referral.duplicate_candidates = candidates
        referral.updated_at = utc_now()
    if not candidates:
        raise ValueError("No duplicate candidates are recorded for this referral.")
    return create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=referral.id,
        patient_id=referral.patient_id,
        task_type="duplicate_resolution",
        reason=reason,
        payload_key="duplicate_candidates",
        source_payload={"duplicate_candidates": candidates, "reason": reason},
    )


def create_suitability_review(
    session: Session,
    referral_id: str,
    *,
    reason: str = "Suitability review is required before therapist matching.",
) -> HumanReviewTask:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    transition_referral_status(
        session,
        referral,
        "clinical_escalation_review",
        reason=reason,
    )
    return create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=referral.id,
        patient_id=patient.id,
        task_type="suitability_review",
        reason=reason,
        payload_key="suitability_review",
        source_payload={
            "risk_category": referral.risk_category,
            "urgency": referral.urgency,
            "reason": reason,
        },
    )


def draft_first_contact_message(
    session: Session,
    referral_id: str,
    *,
    note: str = "",
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    appointments = list(
        session.scalars(
            select(Appointment)
            .where(Appointment.referral_id == referral.id, Appointment.status == "proposed")
            .order_by(Appointment.starts_at)
        )
    )
    if not appointments:
        raise ValueError("First-contact draft requires proposed appointment slots.")
    slot_lines = [
        f"- {iso_or_none(appointment.starts_at)} to {iso_or_none(appointment.ends_at)}"
        for appointment in appointments[:3]
    ]
    note_lines = ["", "Clinic note:", note.strip()] if note.strip() else []
    body = "\n".join(
        [
            f"Hello {referral.patient_name or patient.display_name or 'there'},",
            "",
            "We have reviewed your referral and can offer the following first-session options:",
            "",
            *slot_lines,
            *note_lines,
            "",
            "Please reply with the option that works best. This prototype records replies manually or through simulation.",
        ]
    )
    draft = CommunicationDraft(
        tenant_id=referral.tenant_id,
        referral_id=referral.id,
        patient_id=patient.id,
        workflow_run_id=None,
        channel="email",
        subject="First appointment options",
        body=body,
        status="draft_pending_review",
        proposed_slots=[appointment.id for appointment in appointments[:3]],
        requires_human_send=True,
    )
    session.add(draft)
    session.flush()
    referral.communication_draft_id = draft.id
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="communication_draft",
        entity_id=draft.id,
        after=communication_draft_to_dict(draft),
    )
    create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=None,
        referral_id=referral.id,
        patient_id=patient.id,
        task_type="send_approval",
        reason="First-contact message requires staff approval before simulated/manual send.",
        payload_key=f"first_contact_draft:{draft.id[:8]}",
        source_payload=communication_draft_to_dict(draft),
        draft_text=draft.body,
    )
    transition_referral_status(
        session,
        referral,
        "awaiting_patient_contact",
        reason="First-contact message drafted for approval.",
    )
    return communication_draft_to_dict(draft)


def draft_intake_packet(
    session: Session,
    referral_id: str,
    *,
    note: str = "",
    template_id: str | None = None,
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    if canonical_referral_status(referral.status) not in {
        "appointment_confirmed",
        "intake_packet_sent",
        "intake_incomplete",
        "intake_complete",
        "prep_brief_ready",
        "first_session_ready",
    }:
        raise ValueError("Intake packet can only be drafted after appointment confirmation.")
    workspace = intake_workspace(session, referral_id)
    if workspace["status"] == "not_started":
        workspace = start_intake_for_referral(session, referral_id, template_id)
    patient = _ensure_patient_for_referral(session, referral)
    item_lines = [f"- {item['label']}" for item in workspace["items"] if not _intake_done(item["status"])]
    consent_lines = [f"- {consent['scope'].replace('_', ' ')} consent" for consent in workspace["consents"] if not _intake_done(consent["status"])]
    note_lines = ["", "Clinic note:", note.strip()] if note.strip() else []
    body = "\n".join(
        [
            f"Hello {referral.patient_name or patient.display_name or 'there'},",
            "",
            "Before your first session, please complete the intake items below.",
            "",
            "Required forms and documents:",
            *(item_lines or ["- No document checklist items are outstanding."]),
            "",
            "Required consents:",
            *(consent_lines or ["- No consent records are outstanding."]),
            *note_lines,
            "",
            "This intake packet is a draft and must be approved by clinic staff before sending.",
        ]
    )
    draft = CommunicationDraft(
        tenant_id=referral.tenant_id,
        referral_id=referral.id,
        patient_id=patient.id,
        workflow_run_id=None,
        channel="email",
        subject="Intake packet for your first session",
        body=body,
        status="draft_pending_review",
        proposed_slots=[],
        requires_human_send=True,
    )
    session.add(draft)
    session.flush()
    referral.communication_draft_id = draft.id
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="communication_draft",
        entity_id=draft.id,
        after=communication_draft_to_dict(draft),
    )
    create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=None,
        referral_id=referral.id,
        patient_id=patient.id,
        task_type="send_approval",
        reason="Intake packet requires staff approval before simulated/manual send.",
        payload_key=f"intake_packet_draft:{draft.id[:8]}",
        source_payload=communication_draft_to_dict(draft),
        draft_text=draft.body,
    )
    return communication_draft_to_dict(draft)


def list_intake_tracker(session: Session, tenant_id: str | None = None) -> list[dict[str, Any]]:
    query = select(Referral).order_by(Referral.updated_at.desc())
    if tenant_id:
        query = query.where(Referral.tenant_id == tenant_id)
    rows: list[dict[str, Any]] = []
    for referral in session.scalars(query):
        status = canonical_referral_status(referral.status)
        if status in {"closed_declined", "closed_no_response", "closed_not_suitable"}:
            continue
        items = list(session.scalars(select(IntakeChecklistItem).where(IntakeChecklistItem.referral_id == referral.id)))
        consents = []
        if referral.patient_id:
            consents = list(
                session.scalars(
                    select(ConsentRecord).where(
                        ConsentRecord.tenant_id == referral.tenant_id,
                        ConsentRecord.patient_id == referral.patient_id,
                    )
                )
            )
        if not items and not consents and status not in {
            "appointment_confirmed",
            "intake_packet_sent",
            "intake_incomplete",
            "intake_complete",
            "prep_brief_ready",
            "first_session_ready",
        }:
            continue
        missing_count = len([item for item in items if not _intake_done(item.status)]) + len(
            [consent for consent in consents if not _intake_done(consent.status)]
        )
        rows.append(
            {
                "referral": referral_summary(referral),
                "intake_status": _intake_status(items, consents),
                "missing_count": missing_count,
                "waived_count": len([item for item in items if item.status == "waived"])
                + len([consent for consent in consents if consent.status == "waived"]),
                "completed_count": len([item for item in items if item.status == "completed"])
                + len([consent for consent in consents if consent.status == "completed"]),
                "readiness_blockers": _first_session_readiness_blockers(session, referral),
            }
        )
    return rows


def apply_review_action(
    session: Session,
    *,
    task_id: str,
    action: str,
    final_text: str | None = None,
    rejection_reason: str | None = None,
    reviewer_id: str | None = DEMO_USER_ID,
) -> HumanReviewTask:
    task = session.get(HumanReviewTask, task_id)
    if task is None:
        raise KeyError(f"Unknown review task: {task_id}")

    before = review_task_to_dict(task)
    task.reviewer_id = reviewer_id if session.get(User, reviewer_id or "") else None
    task.reviewed_at = utc_now()
    task.updated_at = utc_now()

    if action == "approve":
        task.status = "approved"
        task.final_text = final_text or task.draft_text
    elif action == "reject":
        task.status = "rejected"
        task.rejection_reason = rejection_reason or "Rejected by reviewer."
    elif action == "request_changes":
        task.status = "changes_requested"
        task.rejection_reason = rejection_reason or "Reviewer requested changes."
    elif action == "escalate":
        task.status = "escalated"
        task.rejection_reason = rejection_reason or "Escalated for director review."
    else:
        raise ValueError("action must be approve, reject, request_changes, or escalate.")

    if task.task_type in {"intake_reminder_approval", "missing_info_message_approval", "send_approval"}:
        _update_reviewed_draft(session, task, action)

    if task.referral_id:
        referral = session.get(Referral, task.referral_id)
        if referral:
            if action == "approve" and task.task_type == "send_approval":
                _approve_send_task(session, task, referral)
            elif action == "approve" and task.task_type == "match_approval":
                transition_referral_status(
                    session,
                    referral,
                    "match_approved",
                    actor_user_id=task.reviewer_id,
                    reason="Therapist match approved in review inbox.",
                )
            elif action == "approve" and task.task_type == "slot_offer_approval":
                transition_referral_status(
                    session,
                    referral,
                    "awaiting_patient_contact",
                    actor_user_id=task.reviewer_id,
                    reason="Slot options approved for patient contact.",
                )
            elif action == "approve" and task.task_type == "clinical_risk_review":
                transition_referral_status(
                    session,
                    referral,
                    "ready_for_matching" if not referral.missing_fields else "needs_admin_review",
                    actor_user_id=task.reviewer_id,
                    reason="Clinical risk review approved.",
                )
            elif action == "approve" and task.task_type == "suitability_review":
                transition_referral_status(
                    session,
                    referral,
                    "ready_for_matching" if not referral.missing_fields and not referral.duplicate_candidates else "needs_admin_review",
                    actor_user_id=task.reviewer_id,
                    reason="Suitability review approved.",
                )
            elif action == "approve" and task.task_type == "duplicate_resolution":
                referral.duplicate_candidates = []
                transition_referral_status(
                    session,
                    referral,
                    _next_admin_gate_status(referral),
                    actor_user_id=task.reviewer_id,
                    reason="Duplicate candidates resolved.",
                )
            elif action == "approve" and task.task_type == "appointment_confirmation_approval":
                _approve_appointment_confirmation(session, task, referral)
            elif action == "approve" and task.task_type == "missing_info_message_approval":
                transition_referral_status(
                    session,
                    referral,
                    "waiting_for_missing_info",
                    actor_user_id=task.reviewer_id,
                    reason="Missing-information message approved for simulated manual send.",
                )
            elif action == "approve" and task.task_type == "intake_exception_approval":
                _approve_intake_exception(session, task)
            elif action == "escalate":
                transition_referral_status(
                    session,
                    referral,
                    "clinical_escalation_review",
                    actor_user_id=task.reviewer_id,
                    reason=task.rejection_reason,
                )
            elif action == "reject" and task.task_type == "suitability_review":
                transition_referral_status(
                    session,
                    referral,
                    "closed_not_suitable",
                    actor_user_id=task.reviewer_id,
                    reason=task.rejection_reason,
                )
            elif action == "reject":
                transition_referral_status(
                    session,
                    referral,
                    "needs_admin_review",
                    actor_user_id=task.reviewer_id,
                    reason=task.rejection_reason,
                )

    write_audit(
        session,
        tenant_id=task.tenant_id,
        actor_user_id=task.reviewer_id,
        action=f"review_{action}",
        entity_type="human_review_task",
        entity_id=task.id,
        before=before,
        after=review_task_to_dict(task),
    )
    return task


def _update_reviewed_draft(session: Session, task: HumanReviewTask, action: str) -> None:
    draft_id = (task.source_payload or {}).get("id") if isinstance(task.source_payload, dict) else None
    draft = session.get(CommunicationDraft, draft_id) if draft_id else None
    if draft is None:
        return
    draft.status = "approved_pending_send" if action == "approve" else task.status
    if action == "approve" and task.final_text:
        draft.body = task.final_text
    draft.updated_at = utc_now()


def _approve_send_task(session: Session, task: HumanReviewTask, referral: Referral) -> None:
    if task.payload_key.startswith("intake_packet_draft"):
        transition_referral_status(
            session,
            referral,
            "intake_packet_sent",
            actor_user_id=task.reviewer_id,
            reason="Intake packet approved for simulated/manual send.",
        )
        return
    transition_referral_status(
        session,
        referral,
        "contact_sent",
        actor_user_id=task.reviewer_id,
        reason="Patient-facing contact draft approved for simulated/manual send.",
    )


def _approve_appointment_confirmation(session: Session, task: HumanReviewTask, referral: Referral) -> None:
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    appointment_id = str(payload.get("appointment_id") or "").strip()
    if appointment_id:
        confirm_appointment(session, appointment_id)
        return
    transition_referral_status(
        session,
        referral,
        "appointment_confirmed",
        actor_user_id=task.reviewer_id,
        reason="Appointment confirmation approved.",
    )
    _maybe_mark_first_session_ready(session, referral)


def _approve_intake_exception(session: Session, task: HumanReviewTask) -> None:
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    target_type = payload.get("target_type")
    reason = task.final_text or payload.get("reason") or "Intake exception approved."

    if target_type == "intake_item":
        item = session.get(IntakeChecklistItem, payload.get("item_id"))
        if item is None:
            raise KeyError("Unknown intake checklist item for exception task.")
        before = intake_item_to_dict(item)
        item.status = "waived"
        item.completed_at = utc_now()
        item.notes = reason
        item.updated_at = utc_now()
        write_audit(
            session,
            tenant_id=item.tenant_id,
            actor_user_id=task.reviewer_id,
            action="waive",
            entity_type="intake_checklist_item",
            entity_id=item.id,
            before=before,
            after=intake_item_to_dict(item),
        )
        if item.referral_id:
            _refresh_referral_intake_status(session, item.referral_id)
        return

    if target_type == "consent_record":
        consent = session.get(ConsentRecord, payload.get("consent_id"))
        if consent is None:
            raise KeyError("Unknown consent record for exception task.")
        before = consent_record_to_dict(consent)
        consent.status = "waived"
        consent.updated_at = utc_now()
        touched_referrals: set[str] = set()
        for item in session.scalars(
            select(IntakeChecklistItem).where(
                IntakeChecklistItem.tenant_id == consent.tenant_id,
                IntakeChecklistItem.patient_id == consent.patient_id,
                IntakeChecklistItem.item_type == "consent",
            )
        ):
            if _intake_done(item.status):
                continue
            item_key = _normal(item.item_key)
            item_label = _normal(item.label)
            scope = _normal(consent.scope)
            if scope and (scope in item_key or scope in item_label or item_key in scope):
                item.status = "waived"
                item.completed_at = utc_now()
                item.notes = reason
                item.updated_at = utc_now()
                if item.referral_id:
                    touched_referrals.add(item.referral_id)
        write_audit(
            session,
            tenant_id=consent.tenant_id,
            actor_user_id=task.reviewer_id,
            action="waive",
            entity_type="consent_record",
            entity_id=consent.id,
            before=before,
            after=consent_record_to_dict(consent),
        )
        for referral_id in touched_referrals or ({task.referral_id} if task.referral_id else set()):
            _refresh_referral_intake_status(session, referral_id)
        return

    raise ValueError("Unsupported intake exception task target.")


def list_therapists(session: Session, tenant_id: str | None = None) -> list[dict[str, Any]]:
    query = select(Therapist).order_by(Therapist.name)
    if tenant_id:
        query = query.where(Therapist.tenant_id == tenant_id)
    return [therapist_to_dict(therapist) for therapist in session.scalars(query)]


def get_therapist(session: Session, therapist_id: str) -> dict[str, Any]:
    therapist = session.get(Therapist, therapist_id)
    if therapist is None:
        raise KeyError(f"Unknown therapist: {therapist_id}")
    return therapist_to_dict(therapist)


def create_therapist(session: Session, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
    ensure_tenant(session, tenant_id)
    therapist = Therapist(
        tenant_id=tenant_id,
        name=str(data.get("name") or "").strip(),
        email=data.get("email"),
        specialties=_list_value(data.get("specialties")),
        age_groups=_list_value(data.get("age_groups")),
        languages=_list_value(data.get("languages")),
        modalities=_list_value(data.get("modalities")),
        insurers=_list_value(data.get("insurers")),
        capacity_per_week=int(data.get("capacity_per_week") or 0),
        active=bool(data.get("active", True)),
        availability_blocks=_availability_blocks(data.get("availability_blocks")),
    )
    if not therapist.name:
        raise ValueError("Therapist name is required.")
    session.add(therapist)
    session.flush()
    write_audit(
        session,
        tenant_id=tenant_id,
        action="create",
        entity_type="therapist",
        entity_id=therapist.id,
        after=therapist_to_dict(therapist),
    )
    return therapist_to_dict(therapist)


def update_therapist(session: Session, therapist_id: str, data: dict[str, Any]) -> dict[str, Any]:
    therapist = session.get(Therapist, therapist_id)
    if therapist is None:
        raise KeyError(f"Unknown therapist: {therapist_id}")
    before = therapist_to_dict(therapist)
    for key in ("name", "email"):
        if key in data:
            setattr(therapist, key, str(data.get(key) or "").strip() or None)
    for key in ("specialties", "age_groups", "languages", "modalities", "insurers"):
        if key in data:
            setattr(therapist, key, _list_value(data.get(key)))
    if "capacity_per_week" in data:
        therapist.capacity_per_week = int(data.get("capacity_per_week") or 0)
    if "active" in data:
        therapist.active = bool(data.get("active"))
    if "availability_blocks" in data:
        therapist.availability_blocks = _availability_blocks(data.get("availability_blocks"))
    if not therapist.name:
        raise ValueError("Therapist name is required.")
    therapist.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=therapist.tenant_id,
        action="update",
        entity_type="therapist",
        entity_id=therapist.id,
        before=before,
        after=therapist_to_dict(therapist),
    )
    return therapist_to_dict(therapist)


def therapist_facts_for_tenant(session: Session, tenant_id: str) -> list[dict[str, Any]]:
    return [
        {
            "therapist_id": therapist.id,
            "name": therapist.name,
            "specialties": therapist.specialties,
            "age_groups": therapist.age_groups,
            "languages": therapist.languages,
            "modalities": therapist.modalities,
            "insurers": therapist.insurers,
            "capacity_per_week": therapist.capacity_per_week,
            "availability_blocks": therapist.availability_blocks,
        }
        for therapist in session.scalars(
            select(Therapist).where(Therapist.tenant_id == tenant_id, Therapist.active.is_(True)).order_by(Therapist.name)
        )
    ]


def deterministic_match_for_referral(session: Session, referral_id: str) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    if referral.missing_fields:
        raise ValueError("Missing information must be resolved before matching.")
    if referral.duplicate_candidates:
        raise ValueError("Duplicate candidates must be resolved before matching.")
    current_status = canonical_referral_status(referral.status)
    unresolved_risk = (
        referral.risk_present
        or referral.urgency in {"elevated", "urgent"}
        or referral.risk_category == "unknown"
        or referral.urgency == "unknown"
    )
    if current_status in {"needs_clinical_review", "clinical_escalation_review"} or (
        unresolved_risk and current_status != "ready_for_matching"
    ):
        raise ValueError("Clinical review must be resolved before matching.")
    therapists = list(
        session.scalars(
            select(Therapist).where(Therapist.tenant_id == referral.tenant_id).order_by(Therapist.name)
        )
    )
    included = []
    excluded = []
    for therapist in therapists:
        result = _score_therapist_for_referral(session, referral, therapist)
        if result["excluded"]:
            excluded.append(result)
        else:
            included.append(result)
    included.sort(key=lambda item: item["score"], reverse=True)
    match = {
        "referral_id": referral.id,
        "ranked_matches": included,
        "excluded_therapists": excluded,
        "hard_constraints_checked": ["active", "capacity", "insurance", "language", "modality", "age_group"],
        "requires_human_approval": True,
    }
    referral.match_summary = match
    target_status = "match_recommended" if included else "needs_admin_review"
    transition_referral_status(
        session,
        referral,
        target_status,
        reason="Deterministic therapist matching completed.",
    )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="deterministic_match",
        entity_type="referral",
        entity_id=referral.id,
        after=referral_summary(referral),
    )
    if included:
        create_review_task(
            session,
            tenant_id=referral.tenant_id,
            workflow_run_id=referral.workflow_run_id,
            referral_id=referral.id,
            patient_id=referral.patient_id,
            task_type="match_approval",
            reason="Therapist match recommendation requires admin or director approval.",
            payload_key="match_recommendation",
            source_payload=match,
        )
    return match


def propose_appointment_slots(
    session: Session,
    referral_id: str,
    therapist_id: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    target_therapist = therapist_id or _top_match_therapist_id(referral)
    if not target_therapist:
        match = deterministic_match_for_referral(session, referral_id)
        target_therapist = (match.get("ranked_matches") or [{}])[0].get("therapist_id")
    if not target_therapist:
        raise ValueError("No eligible therapist is available for appointment proposals.")

    therapist = session.get(Therapist, target_therapist)
    if therapist is None:
        raise KeyError(f"Unknown therapist: {target_therapist}")
    if therapist.tenant_id != referral.tenant_id:
        raise ValueError("Therapist and referral tenants do not match.")

    proposals = []
    existing = list(
        session.scalars(
            select(Appointment).where(
                Appointment.tenant_id == referral.tenant_id,
                Appointment.referral_id == referral.id,
                Appointment.therapist_id == therapist.id,
                Appointment.status == "proposed",
            )
        )
    )
    for appointment in existing:
        proposals.append(appointment_to_dict(appointment))
        if len(proposals) >= limit:
            break

    if len(proposals) < limit:
        for starts_at, ends_at, block in _generate_slots(therapist.availability_blocks, limit * 4):
            if len(proposals) >= limit:
                break
            if _appointment_conflicts(session, therapist.id, starts_at, ends_at):
                continue
            appointment = Appointment(
                tenant_id=referral.tenant_id,
                patient_id=referral.patient_id,
                therapist_id=therapist.id,
                referral_id=referral.id,
                starts_at=starts_at,
                ends_at=ends_at,
                status="proposed",
                source=f"availability:{block.get('weekday', 'manual')}",
            )
            session.add(appointment)
            session.flush()
            proposals.append(appointment_to_dict(appointment))
            write_audit(
                session,
                tenant_id=referral.tenant_id,
                action="create",
                entity_type="appointment",
                entity_id=appointment.id,
                after=appointment_to_dict(appointment),
            )
    if proposals:
        transition_referral_status(
            session,
            referral,
            "slot_options_ready",
            reason="Appointment slot options generated from therapist availability.",
        )
        create_review_task(
            session,
            tenant_id=referral.tenant_id,
            workflow_run_id=referral.workflow_run_id,
            referral_id=referral.id,
            patient_id=referral.patient_id,
            task_type="slot_offer_approval",
            reason="Proposed appointment slots require admin approval before being offered to the patient.",
            payload_key="slot_options",
            source_payload={"appointments": proposals},
        )
    return proposals


def list_appointments(
    session: Session,
    tenant_id: str | None = None,
    referral_id: str | None = None,
    therapist_id: str | None = None,
) -> list[dict[str, Any]]:
    query = select(Appointment).order_by(Appointment.starts_at.asc())
    if tenant_id:
        query = query.where(Appointment.tenant_id == tenant_id)
    if referral_id:
        query = query.where(Appointment.referral_id == referral_id)
    if therapist_id:
        query = query.where(Appointment.therapist_id == therapist_id)
    return [appointment_to_dict(item) for item in session.scalars(query)]


def confirm_appointment(session: Session, appointment_id: str) -> dict[str, Any]:
    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        raise KeyError(f"Unknown appointment: {appointment_id}")
    if not appointment.starts_at or not appointment.ends_at:
        raise ValueError("Appointment has no proposed time.")
    if _appointment_conflicts(
        session,
        appointment.therapist_id or "",
        appointment.starts_at,
        appointment.ends_at,
        exclude_appointment_id=appointment.id,
    ):
        raise ValueError("Appointment conflicts with an existing proposed or confirmed slot.")
    before = appointment_to_dict(appointment)
    appointment.status = "confirmed"
    appointment.updated_at = utc_now()
    if appointment.referral_id:
        referral = session.get(Referral, appointment.referral_id)
        if referral:
            transition_referral_status(
                session,
                referral,
                "appointment_confirmed",
                reason="Appointment record confirmed in Lumen.",
            )
            _maybe_mark_first_session_ready(session, referral)
    write_audit(
        session,
        tenant_id=appointment.tenant_id,
        action="confirm",
        entity_type="appointment",
        entity_id=appointment.id,
        before=before,
        after=appointment_to_dict(appointment),
    )
    return appointment_to_dict(appointment)


def list_intake_templates(session: Session, tenant_id: str | None = None) -> list[dict[str, Any]]:
    query = select(IntakeTemplate).order_by(IntakeTemplate.name)
    if tenant_id:
        query = query.where(IntakeTemplate.tenant_id == tenant_id)
    return [intake_template_to_dict(template) for template in session.scalars(query)]


def create_referral_document(
    session: Session,
    *,
    referral_id: str,
    title: str,
    document_type: str,
    storage_uri: str,
    metadata: dict[str, Any],
    item_id: str | None = None,
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    document = Document(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        document_type=document_type,
        title=title,
        storage_uri=storage_uri,
        metadata_json={
            **json_safe(metadata),
            "referral_id": referral.id,
            "patient_id": patient.id,
            "linked_intake_item_id": item_id,
        },
    )
    session.add(document)
    session.flush()
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="document",
        entity_id=document.id,
        after=document_to_dict(document),
    )
    extracted_text = str(metadata.get("extracted_text") or "")
    if extracted_text.strip():
        _index_text_chunks(
            session,
            tenant_id=referral.tenant_id,
            patient_id=patient.id,
            document_id=document.id,
            source_type=document.document_type,
            source_id=document.id,
            text=extracted_text,
            metadata={"title": title, "referral_id": referral.id},
        )

    if item_id:
        item = session.get(IntakeChecklistItem, item_id)
        if item is None:
            raise KeyError(f"Unknown intake checklist item: {item_id}")
        if item.tenant_id != referral.tenant_id or item.referral_id != referral.id:
            raise ValueError("Intake item does not belong to this referral.")
        before = intake_item_to_dict(item)
        item.source_document_id = document.id
        item.status = "completed"
        item.completed_at = utc_now()
        item.notes = f"Completed by document upload: {title}"
        item.updated_at = utc_now()
        write_audit(
            session,
            tenant_id=item.tenant_id,
            action="complete_by_upload",
            entity_type="intake_checklist_item",
            entity_id=item.id,
            before=before,
            after=intake_item_to_dict(item),
        )
        if item.item_type == "consent":
            _complete_matching_consent_for_item(session, item, document.id)
        if item.referral_id:
            _refresh_referral_intake_status(session, item.referral_id)

    return document_to_dict(document)


def start_intake_for_referral(session: Session, referral_id: str, template_id: str | None = None) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    template = _select_intake_template(session, referral, template_id)
    if template is None:
        raise ValueError("No active intake template is configured for this referral.")

    existing_keys = {
        item.item_key
        for item in session.scalars(
            select(IntakeChecklistItem).where(
                IntakeChecklistItem.tenant_id == referral.tenant_id,
                IntakeChecklistItem.referral_id == referral.id,
            )
        )
    }
    for spec in template.required_items or []:
        key = str(spec.get("key") or spec.get("label") or "").strip()
        if not key or key in existing_keys:
            continue
        item_type = str(spec.get("type") or "form")
        checklist_item = IntakeChecklistItem(
            tenant_id=referral.tenant_id,
            patient_id=patient.id,
            referral_id=referral.id,
            template_id=template.id,
            item_key=key,
            label=str(spec.get("label") or key.replace("_", " ").title()),
            item_type=item_type,
            status="missing",
            due_at=utc_now() + timedelta(days=int(spec.get("due_days") or 7)),
        )
        session.add(checklist_item)
        if item_type == "consent":
            scope = str(spec.get("consent_scope") or key)
            consent = session.scalar(
                select(ConsentRecord).where(
                    ConsentRecord.tenant_id == referral.tenant_id,
                    ConsentRecord.patient_id == patient.id,
                    ConsentRecord.scope == scope,
                )
            )
            if consent is None:
                session.add(
                    ConsentRecord(
                        tenant_id=referral.tenant_id,
                        patient_id=patient.id,
                        scope=scope,
                        status="missing",
                    )
                )

    transition_referral_status(
        session,
        referral,
        "intake_incomplete",
        reason="Intake checklist started for first-session readiness.",
    )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="start_intake",
        entity_type="referral",
        entity_id=referral.id,
        after=referral_summary(referral),
    )
    session.flush()
    return intake_workspace(session, referral_id)


def intake_workspace(session: Session, referral_id: str) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    template = _select_intake_template(session, referral, None)
    items = list(
        session.scalars(
            select(IntakeChecklistItem)
            .where(IntakeChecklistItem.tenant_id == referral.tenant_id, IntakeChecklistItem.referral_id == referral.id)
            .order_by(IntakeChecklistItem.created_at)
        )
    )
    responses = list(
        session.scalars(
            select(QuestionnaireResponse)
            .where(QuestionnaireResponse.tenant_id == referral.tenant_id, QuestionnaireResponse.referral_id == referral.id)
            .order_by(QuestionnaireResponse.created_at.desc())
        )
    )
    briefs = list(
        session.scalars(
            select(TherapistPrepBrief)
            .where(TherapistPrepBrief.tenant_id == referral.tenant_id, TherapistPrepBrief.referral_id == referral.id)
            .order_by(TherapistPrepBrief.created_at.desc())
        )
    )
    documents = []
    drafts = list(
        session.scalars(
            select(CommunicationDraft)
            .where(CommunicationDraft.tenant_id == referral.tenant_id, CommunicationDraft.referral_id == referral.id)
            .order_by(CommunicationDraft.created_at.desc())
        )
    )
    consents = []
    if referral.patient_id:
        consents = list(
            session.scalars(
                select(ConsentRecord)
                .where(ConsentRecord.tenant_id == referral.tenant_id, ConsentRecord.patient_id == referral.patient_id)
                .order_by(ConsentRecord.scope)
            )
        )
        documents = [
            document
            for document in session.scalars(
                select(Document)
                .where(Document.tenant_id == referral.tenant_id, Document.patient_id == referral.patient_id)
                .order_by(Document.created_at.desc())
            )
            if (document.metadata_json or {}).get("referral_id") == referral.id
        ]
    return {
        "referral": referral_summary(referral),
        "template": intake_template_to_dict(template) if template else None,
        "items": [intake_item_to_dict(item) for item in items],
        "consents": [consent_record_to_dict(consent) for consent in consents],
        "questionnaires": [questionnaire_response_to_dict(response) for response in responses],
        "documents": [document_to_dict(document) for document in documents],
        "communication_drafts": [communication_draft_to_dict(draft) for draft in drafts],
        "prep_briefs": [prep_brief_to_dict(brief) for brief in briefs],
        "status": _intake_status(items, consents),
    }


def generate_missing_intake_reminder(session: Session, referral_id: str) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    workspace = intake_workspace(session, referral_id)
    missing_items = [item for item in workspace["items"] if not _intake_done(item["status"])]
    missing_consents = [consent for consent in workspace["consents"] if not _intake_done(consent["status"])]
    if not missing_items and not missing_consents:
        raise ValueError("No missing intake items need a reminder.")

    item_lines = []
    for item in missing_items:
        due_text = f" (due {item['due_at']})" if item.get("due_at") else ""
        item_lines.append(f"- {item['label']}{due_text}")
    consent_lines = [f"- {consent['scope'].replace('_', ' ')} consent" for consent in missing_consents]
    body = "\n".join(
        [
            f"Hello {referral.patient_name or patient.display_name or 'there'},",
            "",
            "Before the first appointment, please send the remaining intake items listed below.",
            "",
            "Missing documents and forms:",
            *(item_lines or ["- No checklist documents are missing."]),
            "",
            "Missing consent records:",
            *(consent_lines or ["- No consent records are missing."]),
            "",
            "This message is a draft and must be reviewed by clinic staff before it is sent.",
        ]
    )
    draft = CommunicationDraft(
        tenant_id=referral.tenant_id,
        referral_id=referral.id,
        patient_id=patient.id,
        workflow_run_id=referral.workflow_run_id,
        channel="email",
        subject="Reminder: intake items before your first session",
        body=body,
        status="draft_pending_review",
        proposed_slots=[],
        requires_human_send=True,
    )
    session.add(draft)
    session.flush()
    referral.communication_draft_id = draft.id
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="communication_draft",
        entity_id=draft.id,
        after=communication_draft_to_dict(draft),
    )
    create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=referral.id,
        patient_id=patient.id,
        task_type="intake_reminder_approval",
        reason="Patient-facing intake reminder requires staff approval before sending.",
        payload_key="intake_reminder_draft",
        source_payload=communication_draft_to_dict(draft),
        draft_text=draft.body,
    )
    return communication_draft_to_dict(draft)


def complete_intake_item(session: Session, item_id: str, notes: str | None = None) -> dict[str, Any]:
    item = session.get(IntakeChecklistItem, item_id)
    if item is None:
        raise KeyError(f"Unknown intake checklist item: {item_id}")
    before = intake_item_to_dict(item)
    item.status = "completed"
    item.completed_at = utc_now()
    item.notes = notes
    item.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=item.tenant_id,
        action="complete",
        entity_type="intake_checklist_item",
        entity_id=item.id,
        before=before,
        after=intake_item_to_dict(item),
    )
    if item.referral_id:
        _refresh_referral_intake_status(session, item.referral_id)
    return intake_item_to_dict(item)


def complete_consent_record(session: Session, consent_id: str, expires_at: datetime | None = None) -> dict[str, Any]:
    consent = session.get(ConsentRecord, consent_id)
    if consent is None:
        raise KeyError(f"Unknown consent record: {consent_id}")
    before = consent_record_to_dict(consent)
    consent.status = "completed"
    consent.expires_at = expires_at
    consent.updated_at = utc_now()
    touched_referrals: set[str] = set()
    for item in session.scalars(
        select(IntakeChecklistItem).where(
            IntakeChecklistItem.tenant_id == consent.tenant_id,
            IntakeChecklistItem.patient_id == consent.patient_id,
            IntakeChecklistItem.item_type == "consent",
            IntakeChecklistItem.status.notin_(["completed", "waived"]),
        )
    ):
        if item.item_key == consent.scope or item.label.lower().startswith(consent.scope.lower()):
            item.status = "completed"
            item.completed_at = utc_now()
            if item.referral_id:
                touched_referrals.add(item.referral_id)
    write_audit(
        session,
        tenant_id=consent.tenant_id,
        action="complete",
        entity_type="consent_record",
        entity_id=consent.id,
        before=before,
        after=consent_record_to_dict(consent),
    )
    for referral_id in touched_referrals:
        _refresh_referral_intake_status(session, referral_id)
    return consent_record_to_dict(consent)


def save_questionnaire_response(
    session: Session,
    referral_id: str,
    questionnaire_name: str,
    answers: dict[str, Any],
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    template = _select_intake_template(session, referral, None)
    response = QuestionnaireResponse(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        referral_id=referral.id,
        template_id=template.id if template else None,
        questionnaire_name=questionnaire_name,
        answers=json_safe(answers),
        score_summary=_score_questionnaire(answers),
        status="completed",
    )
    session.add(response)
    session.flush()
    score = ScoreRecord(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        referral_id=referral.id,
        source_response_id=response.id,
        instrument_name=questionnaire_name,
        score_summary=response.score_summary,
    )
    session.add(score)
    session.flush()
    for item in session.scalars(
        select(IntakeChecklistItem).where(
            IntakeChecklistItem.tenant_id == referral.tenant_id,
            IntakeChecklistItem.referral_id == referral.id,
            IntakeChecklistItem.item_type == "questionnaire",
            IntakeChecklistItem.status.notin_(["completed", "waived"]),
        )
    ):
        item.status = "completed"
        item.completed_at = utc_now()
    _refresh_referral_intake_status(session, referral.id)
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="questionnaire_response",
        entity_id=response.id,
        after=questionnaire_response_to_dict(response),
    )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="score_record",
        entity_id=score.id,
        after=score_record_to_dict(score),
    )
    return questionnaire_response_to_dict(response)


def generate_prep_brief(session: Session, referral_id: str, therapist_id: str | None = None) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    chosen_therapist_id = therapist_id or _top_match_therapist_id(referral)
    items = list(
        session.scalars(select(IntakeChecklistItem).where(IntakeChecklistItem.referral_id == referral.id))
    )
    responses = list(
        session.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.referral_id == referral.id))
    )
    appointments = list(
        session.scalars(select(Appointment).where(Appointment.referral_id == referral.id).order_by(Appointment.starts_at))
    )
    missing_items = [item.label for item in items if not _intake_done(item.status)]
    completed_items = [item.label for item in items if _intake_done(item.status)]
    lines = [
        f"# Therapist Prep Brief: {referral.patient_name or patient.display_name or 'Referral'}",
        "",
        f"- Referral status: {referral.status}",
        f"- Presenting source: {referral.source_channel}",
        f"- Contact: {referral.contact_email or 'missing'} / {referral.contact_phone or 'missing'}",
        f"- Insurance: {referral.insurer or 'missing'}",
        f"- Language/modality: {referral.language_preference or 'unknown'} / {referral.modality_preference or 'unknown'}",
        f"- Risk: {referral.risk_category or 'pending'} ({referral.urgency or 'pending'})",
        f"- Completed intake: {', '.join(completed_items) if completed_items else 'none yet'}",
        f"- Missing intake: {', '.join(missing_items) if missing_items else 'none recorded'}",
    ]
    if responses:
        score_bits = [
            f"{response.questionnaire_name}: {response.score_summary.get('total_score', 0)}"
            for response in responses
        ]
        lines.append(f"- Questionnaire scores: {', '.join(score_bits)}")
    if appointments:
        slot_bits = [
            f"{iso_or_none(item.starts_at)} ({item.status})"
            for item in appointments[:3]
        ]
        lines.append(f"- Proposed slots: {', '.join(slot_bits)}")
    lines.extend(["", "## Source Referral", "", referral.raw_text.strip()])
    brief = TherapistPrepBrief(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        referral_id=referral.id,
        therapist_id=chosen_therapist_id,
        title=f"Prep brief for {referral.patient_name or patient.display_name or referral.id[:8]}",
        body="\n".join(lines).strip(),
        source_summary={
            "referral_id": referral.id,
            "completed_intake_count": len(completed_items),
            "missing_intake_count": len(missing_items),
            "questionnaire_count": len(responses),
            "appointment_count": len(appointments),
        },
    )
    session.add(brief)
    session.flush()
    if not _maybe_mark_first_session_ready(session, referral):
        transition_referral_status(
            session,
            referral,
            "prep_brief_ready",
            reason="Therapist prep brief generated.",
        )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="therapist_prep_brief",
        entity_id=brief.id,
        after=prep_brief_to_dict(brief),
    )
    return prep_brief_to_dict(brief)


def patient_workspace(session: Session, patient_id: str) -> dict[str, Any]:
    patient = session.get(Patient, patient_id)
    if patient is None:
        raise KeyError(f"Unknown patient: {patient_id}")
    referrals = list(
        session.scalars(
            select(Referral)
            .where(Referral.tenant_id == patient.tenant_id, Referral.patient_id == patient.id)
            .order_by(Referral.updated_at.desc())
        )
    )
    documents = list(
        session.scalars(
            select(Document)
            .where(Document.tenant_id == patient.tenant_id, Document.patient_id == patient.id)
            .order_by(Document.created_at.desc())
        )
    )
    notes = list(
        session.scalars(
            select(SessionNote)
            .where(SessionNote.tenant_id == patient.tenant_id, SessionNote.patient_id == patient.id)
            .order_by(SessionNote.created_at.desc())
        )
    )
    scores = list(
        session.scalars(
            select(ScoreRecord)
            .where(ScoreRecord.tenant_id == patient.tenant_id, ScoreRecord.patient_id == patient.id)
            .order_by(ScoreRecord.recorded_at.desc())
        )
    )
    reports = list(
        session.scalars(
            select(ReportDraft)
            .where(ReportDraft.tenant_id == patient.tenant_id, ReportDraft.patient_id == patient.id)
            .order_by(ReportDraft.created_at.desc())
        )
    )
    return {
        "patient": patient_to_dict(patient),
        "referrals": [referral_summary(referral) for referral in referrals],
        "documents": [document_to_dict(document) for document in documents],
        "session_notes": [session_note_to_dict(note) for note in notes],
        "scores": [score_record_to_dict(score) for score in scores],
        "report_drafts": [report_draft_to_dict(report) for report in reports],
    }


def create_session_note(
    session: Session,
    *,
    referral_id: str,
    therapist_id: str | None,
    title: str,
    body: str,
    status: str = "draft",
    appointment_id: str | None = None,
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    if therapist_id:
        therapist = session.get(Therapist, therapist_id)
        if therapist is None:
            raise KeyError(f"Unknown therapist: {therapist_id}")
        if therapist.tenant_id != referral.tenant_id:
            raise ValueError("Therapist and referral tenants do not match.")
    note_body = body.strip()
    if not note_body:
        raise ValueError("Session note body is required.")
    note = SessionNote(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        referral_id=referral.id,
        therapist_id=therapist_id or None,
        appointment_id=appointment_id,
        title=title.strip() or "Session note",
        body=note_body,
        status=status if status in {"draft", "pending_approval", "approved"} else "draft",
        approved_at=utc_now() if status == "approved" else None,
    )
    session.add(note)
    session.flush()
    _index_text_chunks(
        session,
        tenant_id=note.tenant_id,
        patient_id=note.patient_id,
        document_id=None,
        source_type="session_note",
        source_id=note.id,
        text=note.body,
        metadata={"title": note.title, "referral_id": referral.id, "therapist_id": therapist_id},
    )
    write_audit(
        session,
        tenant_id=note.tenant_id,
        action="create",
        entity_type="session_note",
        entity_id=note.id,
        after=session_note_to_dict(note),
    )
    return session_note_to_dict(note)


def approve_session_note(session: Session, note_id: str) -> dict[str, Any]:
    note = session.get(SessionNote, note_id)
    if note is None:
        raise KeyError(f"Unknown session note: {note_id}")
    before = session_note_to_dict(note)
    note.status = "approved"
    note.approved_at = utc_now()
    note.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=note.tenant_id,
        action="approve",
        entity_type="session_note",
        entity_id=note.id,
        before=before,
        after=session_note_to_dict(note),
    )
    return session_note_to_dict(note)


def create_clinical_library_record(
    session: Session,
    *,
    tenant_id: str,
    record_type: str,
    title: str,
    body: str,
    version: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_tenant(session, tenant_id)
    clean_type = record_type.strip().lower()
    if clean_type not in {"protocol", "template", "insurer_rule", "clinical_reference"}:
        raise ValueError("record_type must be protocol, template, insurer_rule, or clinical_reference.")
    clean_body = body.strip()
    if not clean_body:
        raise ValueError("Clinical library body is required.")
    record = ClinicalLibraryRecord(
        tenant_id=tenant_id,
        record_type=clean_type,
        title=title.strip() or clean_type.replace("_", " ").title(),
        version=version or None,
        body=clean_body,
        metadata_json=json_safe(metadata or {}),
    )
    session.add(record)
    session.flush()
    _index_text_chunks(
        session,
        tenant_id=tenant_id,
        patient_id=None,
        document_id=None,
        source_type=clean_type,
        source_id=record.id,
        text=record.body,
        metadata={"title": record.title, "version": record.version, "record_type": record.record_type},
    )
    write_audit(
        session,
        tenant_id=tenant_id,
        action="create",
        entity_type="clinical_library_record",
        entity_id=record.id,
        after=clinical_library_record_to_dict(record),
    )
    return clinical_library_record_to_dict(record)


def list_clinical_library_records(
    session: Session,
    tenant_id: str | None = None,
    record_type: str | None = None,
) -> list[dict[str, Any]]:
    query = select(ClinicalLibraryRecord).order_by(ClinicalLibraryRecord.updated_at.desc())
    if tenant_id:
        query = query.where(ClinicalLibraryRecord.tenant_id == tenant_id)
    if record_type:
        query = query.where(ClinicalLibraryRecord.record_type == record_type)
    return [clinical_library_record_to_dict(record) for record in session.scalars(query)]


def search_retrieval_chunks(
    session: Session,
    *,
    tenant_id: str,
    query_text: str,
    patient_id: str | None = None,
    document_types: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    terms = {term for term in _search_terms(query_text) if len(term) >= 3}
    if not terms:
        return []
    query = select(DocumentChunk).where(DocumentChunk.tenant_id == tenant_id)
    if patient_id:
        query = query.where((DocumentChunk.patient_id == patient_id) | (DocumentChunk.patient_id.is_(None)))
    if document_types:
        query = query.where(DocumentChunk.source_type.in_(document_types))
    ranked = []
    for chunk in session.scalars(query):
        chunk_terms = set(_search_terms(chunk.text))
        score = len(terms & chunk_terms)
        if score:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
    return [document_chunk_to_dict(chunk, score=score) for score, chunk in ranked[: max(1, min(limit, 20))]]


def generate_report_draft(
    session: Session,
    *,
    referral_id: str,
    report_type: str,
    title: str,
    request_text: str,
    therapist_id: str | None = None,
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    if therapist_id:
        therapist = session.get(Therapist, therapist_id)
        if therapist is None:
            raise KeyError(f"Unknown therapist: {therapist_id}")
        if therapist.tenant_id != referral.tenant_id:
            raise ValueError("Therapist and referral tenants do not match.")

    query_text = " ".join(
        part
        for part in [
            request_text,
            referral.raw_text,
            referral.risk_category or "",
            referral.urgency or "",
            referral.modality_preference or "",
        ]
        if part
    )
    chunks = search_retrieval_chunks(
        session,
        tenant_id=referral.tenant_id,
        query_text=query_text,
        patient_id=patient.id,
        limit=6,
    )
    if not chunks:
        raise ValueError("Report drafting is blocked because no retrieval evidence was found.")
    source_types = {chunk["source_type"] for chunk in chunks}
    if report_type != "session_summary" and not source_types.intersection({"protocol", "template", "insurer_rule"}):
        raise ValueError("Formal report drafting requires protocol, template, or insurer-rule evidence.")

    report_title = title.strip() or _default_report_title(report_type, referral, patient)
    claim_map = []
    evidence_lines = []
    for index, chunk in enumerate(chunks, start=1):
        evidence_label = f"E{index}"
        claim = _first_sentence(chunk["text"])
        claim_map.append(
            {
                "claim": claim,
                "evidence": [
                    {
                        "label": evidence_label,
                        "source_type": chunk["source_type"],
                        "source_id": chunk["source_id"],
                        "chunk_index": chunk["chunk_index"],
                    }
                ],
            }
        )
        evidence_lines.append(
            f"- [{evidence_label}] {chunk['source_type']} {chunk['source_id'][:8]}: {claim}"
        )

    body_lines = [
        f"# {report_title}",
        "",
        "Draft status: requires therapist review and sign-off.",
        "",
        "## Referral Context",
        f"- Patient: {referral.patient_name or patient.display_name or patient.id}",
        f"- Source: {referral.source_channel}",
        f"- Risk status: {referral.risk_category or 'not recorded'} / {referral.urgency or 'not recorded'}",
        "",
        "## Evidence-Grounded Summary",
    ]
    for item in claim_map:
        body_lines.append(f"- {item['claim']} [{item['evidence'][0]['label']}]")
    body_lines.extend(
        [
            "",
            "## Requested Output",
            request_text.strip() or "Create a concise clinical summary from approved source evidence.",
            "",
            "## Evidence References",
            *evidence_lines,
        ]
    )

    draft = ReportDraft(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        referral_id=referral.id,
        therapist_id=therapist_id or None,
        report_type=report_type.strip().lower() or "session_summary",
        title=report_title,
        body="\n".join(body_lines),
        claim_evidence_map=json_safe(claim_map),
        unsupported_claims=[],
        retrieval_summary={
            "query_text": query_text[:500],
            "chunk_count": len(chunks),
            "source_types": sorted(source_types),
        },
        status="pending_signoff",
    )
    draft.unsupported_claims = _validate_report_body(draft.body, draft.claim_evidence_map)
    session.add(draft)
    session.flush()
    write_audit(
        session,
        tenant_id=draft.tenant_id,
        action="create",
        entity_type="report_draft",
        entity_id=draft.id,
        after=report_draft_to_dict(draft),
    )
    return report_draft_to_dict(draft)


def update_report_draft(
    session: Session,
    report_id: str,
    *,
    title: str | None = None,
    body: str | None = None,
    claim_evidence_map: list[dict[str, Any]] | None = None,
    reviewer_id: str | None = DEMO_THERAPIST_USER_ID,
    usable_for_practice_memory: bool = False,
) -> dict[str, Any]:
    report = session.get(ReportDraft, report_id)
    if report is None:
        raise KeyError(f"Unknown report draft: {report_id}")
    if report.status == "signed_off":
        raise ValueError("Signed-off reports cannot be edited.")

    before = report_draft_to_dict(report)
    if title is not None:
        clean_title = title.strip()
        if clean_title:
            report.title = clean_title
    if claim_evidence_map is not None:
        report.claim_evidence_map = json_safe(claim_evidence_map)
    if body is not None:
        clean_body = body.strip()
        if not clean_body:
            raise ValueError("Report body cannot be empty.")
        report.body = clean_body

    report.unsupported_claims = _validate_report_body(report.body, report.claim_evidence_map)
    report.status = "pending_signoff"
    report.updated_at = utc_now()

    if body is not None and before["body"] != report.body:
        _create_draft_feedback(
            session,
            report=report,
            feedback_type="therapist_edit",
            original_text=before["body"],
            final_text=report.body,
            reviewer_id=reviewer_id,
            usable_for_practice_memory=usable_for_practice_memory,
        )

    write_audit(
        session,
        tenant_id=report.tenant_id,
        actor_user_id=reviewer_id if session.get(User, reviewer_id or "") else None,
        action="update",
        entity_type="report_draft",
        entity_id=report.id,
        before=before,
        after=report_draft_to_dict(report),
    )
    return report_draft_to_dict(report)


def export_report_draft(session: Session, report_id: str, export_format: str = "markdown") -> dict[str, str]:
    report = session.get(ReportDraft, report_id)
    if report is None:
        raise KeyError(f"Unknown report draft: {report_id}")
    if report.status != "signed_off":
        raise ValueError("Reports can only be exported after therapist sign-off.")
    if export_format not in {"markdown", "md"}:
        raise ValueError("Only Markdown export is available in this MVP.")
    safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", report.title).strip(".-") or report.id
    write_audit(
        session,
        tenant_id=report.tenant_id,
        action="export",
        entity_type="report_draft",
        entity_id=report.id,
        after={"format": "markdown", "file_name": f"{safe_title}.md"},
    )
    return {
        "file_name": f"{safe_title}.md",
        "media_type": "text/markdown; charset=utf-8",
        "content": report.body,
    }


def sign_off_report_draft(
    session: Session,
    report_id: str,
    reviewer_id: str | None = DEMO_THERAPIST_USER_ID,
) -> dict[str, Any]:
    report = session.get(ReportDraft, report_id)
    if report is None:
        raise KeyError(f"Unknown report draft: {report_id}")
    report.unsupported_claims = _validate_report_body(report.body, report.claim_evidence_map)
    if report.unsupported_claims:
        raise ValueError("Report draft has unsupported claims and cannot be signed off.")
    before = report_draft_to_dict(report)
    report.status = "signed_off"
    report.signed_off_at = utc_now()
    report.signed_off_by_id = reviewer_id if session.get(User, reviewer_id or "") else None
    report.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=report.tenant_id,
        actor_user_id=report.signed_off_by_id,
        action="sign_off",
        entity_type="report_draft",
        entity_id=report.id,
        before=before,
        after=report_draft_to_dict(report),
    )
    return report_draft_to_dict(report)


def record_draft_feedback(
    session: Session,
    report_id: str,
    *,
    feedback_type: str = "review_outcome",
    final_text: str | None = None,
    reviewer_id: str | None = DEMO_THERAPIST_USER_ID,
    usable_for_practice_memory: bool = False,
) -> dict[str, Any]:
    report = session.get(ReportDraft, report_id)
    if report is None:
        raise KeyError(f"Unknown report draft: {report_id}")
    feedback = _create_draft_feedback(
        session,
        report=report,
        feedback_type=feedback_type,
        original_text=report.body,
        final_text=final_text or report.body,
        reviewer_id=reviewer_id,
        usable_for_practice_memory=usable_for_practice_memory,
    )
    write_audit(
        session,
        tenant_id=report.tenant_id,
        actor_user_id=feedback.reviewer_id,
        action="record_feedback",
        entity_type="report_draft",
        entity_id=report.id,
        after=draft_feedback_to_dict(feedback),
    )
    return draft_feedback_to_dict(feedback)


def draft_feedback_metrics(session: Session, tenant_id: str | None = None) -> dict[str, Any]:
    feedback_query = select(DraftFeedback)
    report_query = select(ReportDraft)
    if tenant_id:
        feedback_query = feedback_query.where(DraftFeedback.tenant_id == tenant_id)
        report_query = report_query.where(ReportDraft.tenant_id == tenant_id)
    feedback_items = list(session.scalars(feedback_query))
    reports = list(session.scalars(report_query))
    by_type: dict[str, int] = {}
    for item in feedback_items:
        by_type[item.feedback_type] = by_type.get(item.feedback_type, 0) + 1
    return {
        "feedback_count": len(feedback_items),
        "practice_memory_eligible": len([item for item in feedback_items if item.usable_for_practice_memory]),
        "feedback_by_type": by_type,
        "signed_report_count": len([report for report in reports if report.status == "signed_off"]),
        "drafts_with_unsupported_claims": len([report for report in reports if report.unsupported_claims]),
    }


def update_referral_from_result(session: Session, run: WorkflowRun) -> None:
    referral = session.get(Referral, run.referral_id)
    if referral is None:
        return
    before = referral_summary(referral)
    result = run.result or {}
    outputs = result.get("outputs") or {}

    referral.status = _referral_status_from_result(result, run.status, run.approvals)
    referral.workflow_run_id = run.id
    referral.updated_at = utc_now()

    referral_output = outputs.get("referral") or {}
    referral.patient_name = referral_output.get("patient_name") or referral.patient_name
    referral.date_of_birth = referral_output.get("date_of_birth") or referral.date_of_birth
    referral.contact_email = referral_output.get("contact_email") or referral.contact_email
    referral.contact_phone = referral_output.get("contact_phone") or referral.contact_phone
    referral.insurer = referral_output.get("insurer") or referral.insurer
    referral.referring_entity = referral_output.get("referring_entity") or referral.referring_entity
    referral.duplicate_candidates = [str(item) for item in referral_output.get("dedupe_candidates") or referral.duplicate_candidates]

    signals = outputs.get("clinical_signals") or {}
    referral.language_preference = signals.get("language_preference") or referral.language_preference
    referral.modality_preference = signals.get("modality_preference") or referral.modality_preference
    referral.missing_fields = _merge_missing_fields(referral.missing_fields, signals.get("missing_required_fields") or [])

    risk = outputs.get("risk_review") or {}
    referral.risk_category = risk.get("risk_category") or referral.risk_category
    referral.urgency = risk.get("urgency") or referral.urgency
    referral.risk_present = bool(risk.get("risk_present", referral.risk_present))

    match = outputs.get("match_recommendation") or {}
    if match:
        referral.match_summary = json_safe(match)

    draft_data = outputs.get("communication_draft")
    if draft_data:
        draft = CommunicationDraft(
            tenant_id=run.tenant_id,
            referral_id=referral.id,
            patient_id=run.patient_id,
            workflow_run_id=run.id,
            channel=draft_data.get("channel") or "email",
            subject=draft_data.get("subject"),
            body=draft_data.get("body") or "",
            proposed_slots=draft_data.get("proposed_slots") or [],
            requires_human_send=bool(draft_data.get("requires_human_send", True)),
        )
        session.add(draft)
        session.flush()
        referral.communication_draft_id = draft.id
        write_audit(
            session,
            tenant_id=run.tenant_id,
            action="create",
            entity_type="communication_draft",
            entity_id=draft.id,
            after=communication_draft_to_dict(draft),
        )

    write_audit(
        session,
        tenant_id=run.tenant_id,
        action="update_from_workflow",
        entity_type="referral",
        entity_id=referral.id,
        before=before,
        after=referral_summary(referral),
    )


def persist_human_review_tasks(session: Session, run: WorkflowRun) -> None:
    result = run.result or {}
    outputs = result.get("outputs") or {}
    for item in result.get("human_review_queue") or []:
        task_type = item.get("gate") or "admin_review"
        if task_type == "clinical_review":
            task_type = "clinical_risk_review"
        payload_key = item.get("payload_key") or "workflow"
        source_payload = outputs.get(payload_key) or item
        create_review_task(
            session,
            tenant_id=run.tenant_id,
            workflow_run_id=run.id,
            referral_id=run.referral_id,
            patient_id=run.patient_id,
            task_type=task_type,
            reason=item.get("reason") or "Human review required.",
            payload_key=payload_key,
            source_payload=json_safe(source_payload),
            draft_text=_draft_text_for_payload(source_payload),
        )


def approval_payload_for_task(session: Session, task: HumanReviewTask) -> dict[str, Any] | None:
    if not task.workflow_run_id:
        return None
    run = session.get(WorkflowRun, task.workflow_run_id)
    if run is None:
        return None
    request_payload = run.request_payload or {}
    approvals = dict(run.approvals or {})
    approvals[task.task_type] = True
    return {
        "workflow_type": run.workflow_type,
        "tenant_id": run.tenant_id,
        "patient_id": run.patient_id,
        "raw_input": request_payload.get("raw_input") or {},
        "approvals": approvals,
        "referral_id": run.referral_id,
    }


def write_audit(
    session: Session,
    *,
    tenant_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    actor_user_id: str | None = None,
    before: Any | None = None,
    after: Any | None = None,
) -> None:
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=json_safe(before),
            after=json_safe(after),
        )
    )


def workflow_run_to_dict(run: WorkflowRun, include_events: bool = True) -> dict[str, Any]:
    data = workflow_run_summary(run)
    data.update(
        {
            "request_payload": json_safe(run.request_payload),
            "approvals": json_safe(run.approvals),
            "result": json_safe(run.result),
            "error": run.error,
        }
    )
    if include_events:
        data["events"] = [event_to_dict(event) for event in run.events]
    return data


def workflow_run_summary(run: WorkflowRun) -> dict[str, Any]:
    return {
        "job_id": run.id,
        "workflow_type": run.workflow_type,
        "tenant_id": run.tenant_id,
        "patient_id": run.patient_id,
        "referral_id": run.referral_id,
        "status": run.status,
        "created_at": iso_or_none(run.created_at),
        "updated_at": iso_or_none(run.updated_at),
        "input_summary": run.input_summary,
    }


def event_to_dict(event: WorkflowEvent) -> dict[str, Any]:
    return {
        "index": event.index,
        "type": event.type,
        "status": event.status,
        "message": event.message,
        "node": event.node,
        "agent": event.agent,
        "confidence": event.confidence,
        "tools": json_safe(event.tools or []),
        "payload": json_safe(event.payload),
        "created_at": iso_or_none(event.created_at),
    }


def referral_summary(referral: Referral) -> dict[str, Any]:
    status = canonical_referral_status(referral.status)
    next_action = next_action_for_referral(referral)
    return {
        "id": referral.id,
        "tenant_id": referral.tenant_id,
        "patient_id": referral.patient_id,
        "workflow_run_id": referral.workflow_run_id,
        "source_channel": referral.source_channel,
        "status": status,
        "status_label": status_label(status),
        "next_action": next_action,
        "next_action_label": next_action_label(next_action),
        "patient_name": referral.patient_name,
        "date_of_birth": referral.date_of_birth,
        "contact_email": referral.contact_email,
        "contact_phone": referral.contact_phone,
        "insurer": referral.insurer,
        "referring_entity": referral.referring_entity,
        "language_preference": referral.language_preference,
        "modality_preference": referral.modality_preference,
        "missing_fields": referral.missing_fields,
        "risk_category": referral.risk_category,
        "urgency": referral.urgency,
        "risk_present": referral.risk_present,
        "match_summary": json_safe(referral.match_summary),
        "duplicate_candidates": referral.duplicate_candidates,
        "secondary_flags": secondary_flags_for_referral(referral),
        "communication_draft_id": referral.communication_draft_id,
        "created_at": iso_or_none(referral.created_at),
        "updated_at": iso_or_none(referral.updated_at),
    }


def patient_to_dict(patient: Patient) -> dict[str, Any]:
    return {
        "id": patient.id,
        "tenant_id": patient.tenant_id,
        "display_name": patient.display_name,
        "date_of_birth": patient.date_of_birth,
        "contact_email": patient.contact_email,
        "contact_phone": patient.contact_phone,
        "language": patient.language,
        "created_at": iso_or_none(patient.created_at),
        "updated_at": iso_or_none(patient.updated_at),
    }


def user_to_dict(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "active": user.active,
        "created_at": iso_or_none(user.created_at),
        "updated_at": iso_or_none(user.updated_at),
    }


def review_task_to_dict(task: HumanReviewTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "tenant_id": task.tenant_id,
        "workflow_run_id": task.workflow_run_id,
        "referral_id": task.referral_id,
        "patient_id": task.patient_id,
        "task_type": task.task_type,
        "status": task.status,
        "reason": task.reason,
        "payload_key": task.payload_key,
        "source_payload": json_safe(task.source_payload),
        "draft_text": task.draft_text,
        "final_text": task.final_text,
        "rejection_reason": task.rejection_reason,
        "reviewer_id": task.reviewer_id,
        "reviewed_at": iso_or_none(task.reviewed_at),
        "created_at": iso_or_none(task.created_at),
        "updated_at": iso_or_none(task.updated_at),
    }


def communication_draft_to_dict(draft: CommunicationDraft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "tenant_id": draft.tenant_id,
        "referral_id": draft.referral_id,
        "patient_id": draft.patient_id,
        "workflow_run_id": draft.workflow_run_id,
        "channel": draft.channel,
        "subject": draft.subject,
        "body": draft.body,
        "status": draft.status,
        "proposed_slots": draft.proposed_slots,
        "requires_human_send": draft.requires_human_send,
        "created_at": iso_or_none(draft.created_at),
        "updated_at": iso_or_none(draft.updated_at),
    }


def document_to_dict(document: Document) -> dict[str, Any]:
    return {
        "id": document.id,
        "tenant_id": document.tenant_id,
        "patient_id": document.patient_id,
        "document_type": document.document_type,
        "title": document.title,
        "storage_uri": document.storage_uri,
        "metadata": json_safe(document.metadata_json),
        "created_at": iso_or_none(document.created_at),
        "updated_at": iso_or_none(document.updated_at),
    }


def session_note_to_dict(note: SessionNote) -> dict[str, Any]:
    return {
        "id": note.id,
        "tenant_id": note.tenant_id,
        "patient_id": note.patient_id,
        "referral_id": note.referral_id,
        "therapist_id": note.therapist_id,
        "appointment_id": note.appointment_id,
        "title": note.title,
        "body": note.body,
        "status": note.status,
        "source_document_id": note.source_document_id,
        "approved_at": iso_or_none(note.approved_at),
        "created_at": iso_or_none(note.created_at),
        "updated_at": iso_or_none(note.updated_at),
    }


def clinical_library_record_to_dict(record: ClinicalLibraryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "record_type": record.record_type,
        "title": record.title,
        "version": record.version,
        "body": record.body,
        "source_document_id": record.source_document_id,
        "metadata": json_safe(record.metadata_json),
        "status": record.status,
        "created_at": iso_or_none(record.created_at),
        "updated_at": iso_or_none(record.updated_at),
    }


def score_record_to_dict(score: ScoreRecord) -> dict[str, Any]:
    return {
        "id": score.id,
        "tenant_id": score.tenant_id,
        "patient_id": score.patient_id,
        "referral_id": score.referral_id,
        "source_response_id": score.source_response_id,
        "instrument_name": score.instrument_name,
        "score_summary": json_safe(score.score_summary),
        "status": score.status,
        "recorded_at": iso_or_none(score.recorded_at),
        "created_at": iso_or_none(score.created_at),
        "updated_at": iso_or_none(score.updated_at),
    }


def document_chunk_to_dict(chunk: DocumentChunk, score: int | None = None) -> dict[str, Any]:
    data = {
        "id": chunk.id,
        "tenant_id": chunk.tenant_id,
        "patient_id": chunk.patient_id,
        "document_id": chunk.document_id,
        "source_type": chunk.source_type,
        "source_id": chunk.source_id,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "metadata": json_safe(chunk.metadata_json),
        "embedding_model": chunk.embedding_model,
        "vector_ref": chunk.vector_ref,
        "created_at": iso_or_none(chunk.created_at),
    }
    if score is not None:
        data["score"] = score
    return data


def report_draft_to_dict(report: ReportDraft) -> dict[str, Any]:
    return {
        "id": report.id,
        "tenant_id": report.tenant_id,
        "patient_id": report.patient_id,
        "referral_id": report.referral_id,
        "therapist_id": report.therapist_id,
        "report_type": report.report_type,
        "title": report.title,
        "body": report.body,
        "claim_evidence_map": json_safe(report.claim_evidence_map),
        "unsupported_claims": json_safe(report.unsupported_claims),
        "retrieval_summary": json_safe(report.retrieval_summary),
        "status": report.status,
        "signed_off_at": iso_or_none(report.signed_off_at),
        "signed_off_by_id": report.signed_off_by_id,
        "created_at": iso_or_none(report.created_at),
        "updated_at": iso_or_none(report.updated_at),
    }


def referral_import_batch_to_dict(batch: ReferralImportBatch) -> dict[str, Any]:
    return {
        "id": batch.id,
        "tenant_id": batch.tenant_id,
        "source_channel": batch.source_channel,
        "file_name": batch.file_name,
        "source_document_id": batch.source_document_id,
        "status": batch.status,
        "total_rows": batch.total_rows,
        "imported_count": batch.imported_count,
        "error_count": batch.error_count,
        "metadata": json_safe(batch.metadata_json),
        "created_at": iso_or_none(batch.created_at),
        "updated_at": iso_or_none(batch.updated_at),
    }


def referral_import_error_to_dict(error: ReferralImportError) -> dict[str, Any]:
    return {
        "id": error.id,
        "tenant_id": error.tenant_id,
        "batch_id": error.batch_id,
        "row_number": error.row_number,
        "message": error.message,
        "raw_row": json_safe(error.raw_row),
        "created_at": iso_or_none(error.created_at),
    }


def draft_feedback_to_dict(feedback: DraftFeedback) -> dict[str, Any]:
    return {
        "id": feedback.id,
        "tenant_id": feedback.tenant_id,
        "patient_id": feedback.patient_id,
        "referral_id": feedback.referral_id,
        "report_draft_id": feedback.report_draft_id,
        "reviewer_id": feedback.reviewer_id,
        "feedback_type": feedback.feedback_type,
        "original_text": feedback.original_text,
        "final_text": feedback.final_text,
        "edit_summary": json_safe(feedback.edit_summary),
        "usable_for_practice_memory": feedback.usable_for_practice_memory,
        "created_at": iso_or_none(feedback.created_at),
        "updated_at": iso_or_none(feedback.updated_at),
    }


def therapist_to_dict(therapist: Therapist) -> dict[str, Any]:
    return {
        "id": therapist.id,
        "tenant_id": therapist.tenant_id,
        "name": therapist.name,
        "email": therapist.email,
        "specialties": therapist.specialties,
        "age_groups": therapist.age_groups,
        "languages": therapist.languages,
        "modalities": therapist.modalities,
        "insurers": therapist.insurers,
        "capacity_per_week": therapist.capacity_per_week,
        "active": therapist.active,
        "availability_blocks": therapist.availability_blocks,
        "created_at": iso_or_none(therapist.created_at),
        "updated_at": iso_or_none(therapist.updated_at),
    }


def appointment_to_dict(appointment: Appointment) -> dict[str, Any]:
    return {
        "id": appointment.id,
        "tenant_id": appointment.tenant_id,
        "patient_id": appointment.patient_id,
        "therapist_id": appointment.therapist_id,
        "referral_id": appointment.referral_id,
        "starts_at": iso_or_none(appointment.starts_at),
        "ends_at": iso_or_none(appointment.ends_at),
        "status": appointment.status,
        "source": appointment.source,
        "created_at": iso_or_none(appointment.created_at),
        "updated_at": iso_or_none(appointment.updated_at),
    }


def intake_template_to_dict(template: IntakeTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "tenant_id": template.tenant_id,
        "name": template.name,
        "patient_type": template.patient_type,
        "insurer": template.insurer,
        "age_band": template.age_band,
        "modality": template.modality,
        "source_channel": template.source_channel,
        "required_items": json_safe(template.required_items),
        "questionnaire_schema": json_safe(template.questionnaire_schema),
        "active": template.active,
        "created_at": iso_or_none(template.created_at),
        "updated_at": iso_or_none(template.updated_at),
    }


def intake_item_to_dict(item: IntakeChecklistItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "tenant_id": item.tenant_id,
        "patient_id": item.patient_id,
        "referral_id": item.referral_id,
        "template_id": item.template_id,
        "item_key": item.item_key,
        "label": item.label,
        "item_type": item.item_type,
        "status": item.status,
        "due_at": iso_or_none(item.due_at),
        "completed_at": iso_or_none(item.completed_at),
        "source_document_id": item.source_document_id,
        "notes": item.notes,
        "created_at": iso_or_none(item.created_at),
        "updated_at": iso_or_none(item.updated_at),
    }


def consent_record_to_dict(consent: ConsentRecord) -> dict[str, Any]:
    return {
        "id": consent.id,
        "tenant_id": consent.tenant_id,
        "patient_id": consent.patient_id,
        "scope": consent.scope,
        "status": consent.status,
        "expires_at": iso_or_none(consent.expires_at),
        "source_document_id": consent.source_document_id,
        "created_at": iso_or_none(consent.created_at),
        "updated_at": iso_or_none(consent.updated_at),
    }


def questionnaire_response_to_dict(response: QuestionnaireResponse) -> dict[str, Any]:
    return {
        "id": response.id,
        "tenant_id": response.tenant_id,
        "patient_id": response.patient_id,
        "referral_id": response.referral_id,
        "template_id": response.template_id,
        "questionnaire_name": response.questionnaire_name,
        "answers": json_safe(response.answers),
        "score_summary": json_safe(response.score_summary),
        "status": response.status,
        "created_at": iso_or_none(response.created_at),
        "updated_at": iso_or_none(response.updated_at),
    }


def prep_brief_to_dict(brief: TherapistPrepBrief) -> dict[str, Any]:
    return {
        "id": brief.id,
        "tenant_id": brief.tenant_id,
        "patient_id": brief.patient_id,
        "referral_id": brief.referral_id,
        "therapist_id": brief.therapist_id,
        "title": brief.title,
        "body": brief.body,
        "source_summary": json_safe(brief.source_summary),
        "status": brief.status,
        "created_at": iso_or_none(brief.created_at),
        "updated_at": iso_or_none(brief.updated_at),
    }


def _record_import_error(
    session: Session,
    batch: ReferralImportBatch,
    row_number: int,
    message: str,
    raw_row: dict[str, Any],
) -> None:
    session.add(
        ReferralImportError(
            tenant_id=batch.tenant_id,
            batch_id=batch.id,
            row_number=row_number,
            message=message,
            raw_row=json_safe(raw_row),
        )
    )
    session.flush()


def _referral_from_import_row(
    session: Session,
    tenant_id: str,
    source_channel: str,
    row: dict[str, str],
) -> Referral:
    direct_text = _row_get(row, "raw_text", "referral_text", "notes", "message")
    raw_text = direct_text
    if not raw_text:
        raw_text = _compose_referral_text(row)
    if not direct_text and len([line for line in raw_text.splitlines() if line.strip()]) < 2:
        raise ValueError("Referral text is required.")
    if len(raw_text.strip()) < 12:
        raise ValueError("Referral text is required.")
    referral = Referral(
        tenant_id=tenant_id,
        source_channel=_row_get(row, "source_channel") or source_channel,
        raw_text=raw_text,
        status="new_referral",
        patient_name=_row_get(row, "patient_name", "name"),
        date_of_birth=_row_get(row, "date_of_birth", "dob"),
        contact_email=_row_get(row, "contact_email", "email"),
        contact_phone=_row_get(row, "contact_phone", "phone", "telephone"),
        insurer=_row_get(row, "insurer", "insurance", "payer"),
        referring_entity=_row_get(row, "referring_entity", "referrer", "source"),
        language_preference=_row_get(row, "language", "language_preference"),
        modality_preference=_row_get(row, "modality", "modality_preference"),
        missing_fields=_deterministic_missing_fields({"raw_text": raw_text}),
    )
    session.add(referral)
    session.flush()
    _ensure_admin_missing_info_task(session, referral)
    write_audit(
        session,
        tenant_id=tenant_id,
        action="create_from_import",
        entity_type="referral",
        entity_id=referral.id,
        after=referral_summary(referral),
    )
    return referral


def _row_get(row: dict[str, str], *keys: str) -> str | None:
    normalised = {_normal(key).replace(" ", "_"): value for key, value in row.items()}
    for key in keys:
        value = normalised.get(_normal(key).replace(" ", "_"))
        if value:
            return value.strip()
    return None


def _compose_referral_text(row: dict[str, str]) -> str:
    ordered_keys = [
        "patient_name",
        "name",
        "date_of_birth",
        "dob",
        "contact_email",
        "email",
        "contact_phone",
        "phone",
        "insurer",
        "insurance",
        "presenting_problem",
        "reason",
        "notes",
    ]
    lines = []
    for key in ordered_keys:
        value = _row_get(row, key)
        if value:
            lines.append(f"{key.replace('_', ' ').title()}: {value}")
    return "\n".join(lines)


def _validate_report_body(body: str, claim_evidence_map: list[dict[str, Any]]) -> list[str]:
    labels = _evidence_labels(claim_evidence_map)
    unsupported: list[str] = []
    if not claim_evidence_map:
        unsupported.append("Report has no claim-to-evidence map.")

    in_summary = False
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            heading = _normal(line.lstrip("# "))
            in_summary = heading in {
                "evidence-grounded summary",
                "clinical summary",
                "assessment summary",
                "treatment review",
                "discharge summary",
                "insurance evidence",
            }
            continue
        if not in_summary or not line.startswith("- "):
            continue
        refs = set(re.findall(r"\[([A-Za-z]\d+)\]", line))
        if not refs.intersection(labels):
            unsupported.append(line[2:].strip())

    for item in claim_evidence_map or []:
        claim = str(item.get("claim") or "").strip()
        if claim and not item.get("evidence"):
            unsupported.append(claim)

    deduped = []
    for claim in unsupported:
        if claim and claim not in deduped:
            deduped.append(claim)
    return deduped


def _evidence_labels(claim_evidence_map: list[dict[str, Any]]) -> set[str]:
    labels = set()
    for item in claim_evidence_map or []:
        for evidence in item.get("evidence") or []:
            label = str(evidence.get("label") or "").strip()
            if label:
                labels.add(label)
    return labels


def _create_draft_feedback(
    session: Session,
    *,
    report: ReportDraft,
    feedback_type: str,
    original_text: str | None,
    final_text: str | None,
    reviewer_id: str | None,
    usable_for_practice_memory: bool,
) -> DraftFeedback:
    feedback = DraftFeedback(
        tenant_id=report.tenant_id,
        patient_id=report.patient_id,
        referral_id=report.referral_id,
        report_draft_id=report.id,
        reviewer_id=reviewer_id if session.get(User, reviewer_id or "") else None,
        feedback_type=feedback_type.strip() or "review_outcome",
        original_text=original_text,
        final_text=final_text,
        edit_summary=_diff_summary(original_text or "", final_text or ""),
        usable_for_practice_memory=usable_for_practice_memory,
    )
    session.add(feedback)
    session.flush()
    return feedback


def _diff_summary(before: str, after: str) -> dict[str, Any]:
    before_words = before.split()
    after_words = after.split()
    return {
        "before_chars": len(before),
        "after_chars": len(after),
        "before_words": len(before_words),
        "after_words": len(after_words),
        "changed": before != after,
    }


def _score_therapist_for_referral(session: Session, referral: Referral, therapist: Therapist) -> dict[str, Any]:
    reasons = []
    exclusions = []
    score = 50

    if not therapist.active:
        exclusions.append("inactive therapist profile")
    active_count = _active_appointment_count_this_week(session, therapist.id)
    if therapist.capacity_per_week and active_count >= therapist.capacity_per_week:
        exclusions.append("weekly capacity is full")

    insurer = _normal(referral.insurer)
    if insurer and therapist.insurers:
        if insurer in {_normal(item) for item in therapist.insurers}:
            score += 20
            reasons.append("insurer accepted")
        else:
            exclusions.append("insurer mismatch")

    language = _normal(referral.language_preference)
    if language and therapist.languages:
        if language in {_normal(item) for item in therapist.languages}:
            score += 20
            reasons.append("language match")
        else:
            exclusions.append("language mismatch")

    modality = _normal(referral.modality_preference)
    if modality and modality != "unknown" and therapist.modalities:
        if modality in {_normal(item) for item in therapist.modalities}:
            score += 15
            reasons.append("modality match")
        else:
            exclusions.append("modality mismatch")

    raw_text = _normal(referral.raw_text)
    specialty_hits = [specialty for specialty in therapist.specialties if _normal(specialty) and _normal(specialty) in raw_text]
    if specialty_hits:
        score += min(25, 10 * len(specialty_hits))
        reasons.append(f"specialty match: {', '.join(specialty_hits)}")

    if therapist.availability_blocks:
        score += 10
        reasons.append("availability blocks recorded")

    return {
        "therapist_id": therapist.id,
        "name": therapist.name,
        "score": max(0, min(score, 100)),
        "excluded": bool(exclusions),
        "reasons": reasons or ["no preference matches beyond baseline availability"],
        "exclusion_reasons": exclusions,
        "capacity_used_this_week": active_count,
        "capacity_per_week": therapist.capacity_per_week,
        "availability_blocks": therapist.availability_blocks,
    }


def _active_appointment_count_this_week(session: Session, therapist_id: str) -> int:
    now = utc_now()
    start = now - timedelta(days=now.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return int(
        session.scalar(
            select(func.count(Appointment.id)).where(
                Appointment.therapist_id == therapist_id,
                Appointment.status.in_(["proposed", "confirmed"]),
                Appointment.starts_at >= start,
                Appointment.starts_at < end,
            )
        )
        or 0
    )


def _top_match_therapist_id(referral: Referral) -> str | None:
    ranked = (referral.match_summary or {}).get("ranked_matches") or []
    if not ranked:
        return None
    return ranked[0].get("therapist_id")


def _generate_slots(blocks: list[dict], max_candidates: int) -> list[tuple[datetime, datetime, dict[str, Any]]]:
    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    now = utc_now()
    slots = []
    for day_offset in range(1, 29):
        candidate_date = (now + timedelta(days=day_offset)).date()
        for block in blocks or []:
            weekday = weekday_map.get(str(block.get("weekday") or "").strip().lower())
            if weekday is None or candidate_date.weekday() != weekday:
                continue
            start_time = _parse_time(block.get("start")) or time(9, 0)
            end_time = _parse_time(block.get("end")) or time(17, 0)
            starts_at = datetime.combine(candidate_date, start_time, tzinfo=timezone.utc)
            ends_at = min(
                datetime.combine(candidate_date, end_time, tzinfo=timezone.utc),
                starts_at + timedelta(minutes=50),
            )
            if ends_at <= starts_at:
                ends_at = starts_at + timedelta(minutes=50)
            slots.append((starts_at, ends_at, block))
            if len(slots) >= max_candidates:
                return slots
    return slots


def _appointment_conflicts(
    session: Session,
    therapist_id: str,
    starts_at: datetime,
    ends_at: datetime,
    exclude_appointment_id: str | None = None,
) -> bool:
    if not therapist_id:
        return False
    query = select(Appointment).where(
        Appointment.therapist_id == therapist_id,
        Appointment.status.in_(["proposed", "confirmed"]),
        Appointment.starts_at < ends_at,
        Appointment.ends_at > starts_at,
    )
    if exclude_appointment_id:
        query = query.where(Appointment.id != exclude_appointment_id)
    return session.scalar(query.limit(1)) is not None


def _parse_time(value: Any) -> time | None:
    if not value:
        return None
    try:
        hour, minute = str(value).split(":", 1)
        return time(int(hour), int(minute[:2]))
    except (ValueError, TypeError):
        return None


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _availability_blocks(value: Any) -> list[dict]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json_safe(json.loads(value))
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _index_text_chunks(
    session: Session,
    *,
    tenant_id: str,
    patient_id: str | None,
    document_id: str | None,
    source_type: str,
    source_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    for chunk in session.scalars(
        select(DocumentChunk).where(DocumentChunk.source_type == source_type, DocumentChunk.source_id == source_id)
    ):
        session.delete(chunk)
    session.flush()
    for index, chunk_text in enumerate(_chunk_text(text)):
        session.add(
            DocumentChunk(
                tenant_id=tenant_id,
                patient_id=patient_id,
                document_id=document_id,
                source_type=source_type,
                source_id=source_id,
                chunk_index=index,
                text=chunk_text,
                metadata_json=json_safe(metadata or {}),
                embedding_model="keyword-mvp",
            )
        )


def _chunk_text(text: str, max_chars: int = 1200) -> list[str]:
    clean = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not clean:
        return []
    chunks = []
    current = ""
    for paragraph in clean.split("\n"):
        next_value = f"{current}\n{paragraph}".strip() if current else paragraph
        if len(next_value) <= max_chars:
            current = next_value
            continue
        if current:
            chunks.append(current)
        current = paragraph[:max_chars]
    if current:
        chunks.append(current)
    return chunks


def _search_terms(text: str) -> list[str]:
    return [part for part in "".join(char.lower() if char.isalnum() else " " for char in text).split() if part]


def _first_sentence(text: str) -> str:
    clean = " ".join(str(text or "").split())
    if not clean:
        return "Source evidence recorded."
    for marker in (". ", "? ", "! "):
        if marker in clean:
            return clean.split(marker, 1)[0].strip() + marker.strip()
    return clean[:220]


def _default_report_title(report_type: str, referral: Referral, patient: Patient) -> str:
    label = str(report_type or "session_summary").replace("_", " ").title()
    patient_name = referral.patient_name or patient.display_name or "Patient"
    return f"{label}: {patient_name}"


def _complete_matching_consent_for_item(session: Session, item: IntakeChecklistItem, document_id: str) -> None:
    if not item.patient_id:
        return
    item_key = _normal(item.item_key)
    item_label = _normal(item.label)
    for consent in session.scalars(
        select(ConsentRecord).where(
            ConsentRecord.tenant_id == item.tenant_id,
            ConsentRecord.patient_id == item.patient_id,
            ConsentRecord.status != "completed",
        )
    ):
        scope = _normal(consent.scope)
        if scope and (scope in item_key or scope in item_label or item_key in scope):
            before = consent_record_to_dict(consent)
            consent.status = "completed"
            consent.source_document_id = document_id
            consent.updated_at = utc_now()
            write_audit(
                session,
                tenant_id=consent.tenant_id,
                action="complete_by_upload",
                entity_type="consent_record",
                entity_id=consent.id,
                before=before,
                after=consent_record_to_dict(consent),
            )


def _ensure_admin_missing_info_task(session: Session, referral: Referral) -> HumanReviewTask | None:
    missing_fields = list(referral.missing_fields or [])
    if not missing_fields:
        return None
    return create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=referral.id,
        patient_id=referral.patient_id,
        task_type="admin_missing_info_review",
        reason="Referral has missing information that must be resolved before the admin workflow can continue.",
        payload_key="missing_information",
        source_payload={
            "missing_fields": missing_fields,
            "patient_name": referral.patient_name,
            "contact_email": referral.contact_email,
            "contact_phone": referral.contact_phone,
        },
    )


def _referral_documents(session: Session, referral: Referral, document_type: str) -> list[dict[str, Any]]:
    if not referral.patient_id:
        return []
    documents = [
        document
        for document in session.scalars(
            select(Document)
            .where(
                Document.tenant_id == referral.tenant_id,
                Document.patient_id == referral.patient_id,
                Document.document_type == document_type,
            )
            .order_by(Document.created_at.desc())
        )
        if (document.metadata_json or {}).get("referral_id") == referral.id
    ]
    return [document_to_dict(document) for document in documents]


def _clean_missing_info_updates(updates: dict[str, Any]) -> dict[str, str]:
    allowed = {
        "patient_name",
        "date_of_birth",
        "dob",
        "contact_email",
        "email",
        "contact_phone",
        "phone",
        "insurer",
        "referring_entity",
        "language_preference",
        "modality_preference",
    }
    clean: dict[str, str] = {}
    for key, value in updates.items():
        normalised_key = str(key or "").strip()
        text = str(value or "").strip()
        if normalised_key in allowed and text:
            clean[normalised_key] = text
    return clean


def _apply_referral_updates(referral: Referral, updates: dict[str, str]) -> None:
    field_map = {
        "dob": "date_of_birth",
        "email": "contact_email",
        "phone": "contact_phone",
    }
    for key, value in updates.items():
        target = field_map.get(key, key)
        if hasattr(referral, target):
            setattr(referral, target, value)


def _remaining_missing_fields(existing: list[str], updates: dict[str, str]) -> list[str]:
    resolved = set(updates.keys())
    if "dob" in resolved:
        resolved.add("date_of_birth")
    if "email" in resolved:
        resolved.add("contact_email")
    if "phone" in resolved:
        resolved.add("contact_phone")
    if {"contact_phone", "date_of_birth", "phone", "dob"} & resolved:
        resolved.add("contact_phone_or_date_of_birth")
    remaining = [field for field in existing or [] if field not in resolved]
    return list(dict.fromkeys(remaining))


def _next_admin_gate_status(referral: Referral) -> str:
    if referral.missing_fields:
        return "needs_admin_review"
    if referral.duplicate_candidates:
        return "needs_admin_review"
    if referral.risk_present or referral.urgency in {"elevated", "urgent", "unknown"} or referral.risk_category == "unknown":
        return "needs_clinical_review"
    return "ready_for_matching"


def _latest_referral_id_for_patient(session: Session, tenant_id: str, patient_id: str) -> str | None:
    referral = session.scalar(
        select(Referral)
        .where(Referral.tenant_id == tenant_id, Referral.patient_id == patient_id)
        .order_by(Referral.updated_at.desc())
        .limit(1)
    )
    return referral.id if referral else None


def _normal(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ")


def _ensure_patient_for_referral(session: Session, referral: Referral) -> Patient:
    if referral.patient_id:
        patient = session.get(Patient, referral.patient_id)
        if patient:
            return patient
    patient = Patient(
        tenant_id=referral.tenant_id,
        display_name=referral.patient_name or f"Referral {referral.id[:8]}",
        date_of_birth=referral.date_of_birth,
        contact_email=referral.contact_email,
        contact_phone=referral.contact_phone,
        language=referral.language_preference,
    )
    session.add(patient)
    session.flush()
    referral.patient_id = patient.id
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create_from_referral",
        entity_type="patient",
        entity_id=patient.id,
        after={
            "id": patient.id,
            "display_name": patient.display_name,
            "referral_id": referral.id,
        },
    )
    return patient


def _select_intake_template(session: Session, referral: Referral, template_id: str | None) -> IntakeTemplate | None:
    if template_id:
        template = session.get(IntakeTemplate, template_id)
        if template is None:
            raise KeyError(f"Unknown intake template: {template_id}")
        if template.tenant_id != referral.tenant_id:
            raise ValueError("Intake template and referral tenants do not match.")
        return template

    candidates = list(
        session.scalars(
            select(IntakeTemplate).where(
                IntakeTemplate.tenant_id == referral.tenant_id,
                IntakeTemplate.active.is_(True),
            )
        )
    )
    if not candidates:
        return None

    def _score(template: IntakeTemplate) -> int:
        score = 0
        if template.insurer and _normal(template.insurer) == _normal(referral.insurer):
            score += 4
        if template.modality and _normal(template.modality) == _normal(referral.modality_preference):
            score += 3
        if template.source_channel and _normal(template.source_channel) == _normal(referral.source_channel):
            score += 2
        if not any([template.insurer, template.modality, template.source_channel, template.age_band]):
            score += 1
        return score

    return sorted(candidates, key=_score, reverse=True)[0]


def _intake_done(status: str | None) -> bool:
    return status in {"completed", "waived"}


def _intake_status(items: list[IntakeChecklistItem], consents: list[ConsentRecord]) -> str:
    if not items and not consents:
        return "not_started"
    missing_items = [item for item in items if not _intake_done(item.status)]
    missing_consents = [consent for consent in consents if not _intake_done(consent.status)]
    if not missing_items and not missing_consents:
        return "complete"
    if any(item.status == "expired" for item in items) or any(consent.status == "expired" for consent in consents):
        return "expired_items"
    return "missing_items"


def _first_session_readiness_blockers(session: Session, referral: Referral) -> list[str]:
    status = canonical_referral_status(referral.status)
    if status in {"closed_declined", "closed_no_response", "closed_not_suitable"}:
        return ["Referral is closed."]
    blockers: list[str] = []
    confirmed = session.scalar(
        select(func.count(Appointment.id)).where(
            Appointment.referral_id == referral.id,
            Appointment.status == "confirmed",
        )
    )
    if not confirmed:
        blockers.append("No confirmed appointment.")

    items = list(session.scalars(select(IntakeChecklistItem).where(IntakeChecklistItem.referral_id == referral.id)))
    consents = []
    if referral.patient_id:
        consents = list(
            session.scalars(
                select(ConsentRecord).where(
                    ConsentRecord.tenant_id == referral.tenant_id,
                    ConsentRecord.patient_id == referral.patient_id,
                )
            )
        )
    intake_status = _intake_status(items, consents)
    if intake_status != "complete":
        blockers.append("Required intake is not complete or waived.")

    prep_briefs = session.scalar(
        select(func.count(TherapistPrepBrief.id)).where(TherapistPrepBrief.referral_id == referral.id)
    )
    if not prep_briefs:
        blockers.append("Therapist prep brief is not generated.")
    return blockers


def _maybe_mark_first_session_ready(session: Session, referral: Referral) -> bool:
    if canonical_referral_status(referral.status) in {"closed_declined", "closed_no_response", "closed_not_suitable"}:
        return False
    blockers = _first_session_readiness_blockers(session, referral)
    if blockers:
        return False
    transition_referral_status(
        session,
        referral,
        "first_session_ready",
        reason="Appointment, intake, and prep brief gates are complete.",
    )
    return True


def _refresh_referral_intake_status(session: Session, referral_id: str) -> None:
    referral = session.get(Referral, referral_id)
    if referral is None:
        return
    status = canonical_referral_status(referral.status)
    if status in {"first_session_ready", "closed_declined", "closed_no_response", "closed_not_suitable"}:
        return
    items = list(session.scalars(select(IntakeChecklistItem).where(IntakeChecklistItem.referral_id == referral.id)))
    consents = []
    if referral.patient_id:
        consents = list(
            session.scalars(
                select(ConsentRecord).where(
                    ConsentRecord.tenant_id == referral.tenant_id,
                    ConsentRecord.patient_id == referral.patient_id,
                )
            )
        )
    intake_status = _intake_status(items, consents)
    if intake_status == "complete":
        if _maybe_mark_first_session_ready(session, referral):
            return
        if status != "prep_brief_ready":
            transition_referral_status(session, referral, "intake_complete", reason="Required intake is complete.")
    elif intake_status != "not_started":
        transition_referral_status(session, referral, "intake_incomplete", reason="Required intake remains incomplete.")


def _score_questionnaire(answers: dict[str, Any]) -> dict[str, Any]:
    numeric_values = []
    for value in answers.values():
        try:
            numeric_values.append(float(value))
        except (TypeError, ValueError):
            continue
    total = float(sum(numeric_values))
    return {
        "total_score": int(total) if total.is_integer() else total,
        "answered_items": len(answers),
        "numeric_items": len(numeric_values),
    }


def _deterministic_missing_fields(raw_input: dict[str, Any]) -> list[str]:
    text = str(raw_input.get("raw_text") or "").lower()
    missing = []
    if "@" not in text:
        missing.append("contact_email")
    if not any(char.isdigit() for char in text):
        missing.append("contact_phone_or_date_of_birth")
    if "segur" not in text and "insur" not in text and "advancecare" not in text and "multicare" not in text:
        missing.append("insurer")
    return missing


def _merge_missing_fields(existing: list[str], incoming: list[str]) -> list[str]:
    merged = list(existing or [])
    for field in incoming:
        if field not in merged:
            merged.append(str(field))
    return merged


def _referral_status_from_result(result: dict[str, Any], run_status: str, approvals: dict[str, Any] | None = None) -> str:
    if run_status == "failed" or result.get("errors"):
        return "needs_admin_review"
    review_gates = {item.get("gate") for item in result.get("human_review_queue") or []}
    outputs = result.get("outputs") or {}
    risk = outputs.get("risk_review") or {}
    if "clinical_review" in review_gates or risk.get("required_handoff") in {"clinician_review", "director_review"}:
        return "needs_clinical_review"
    if "match_approval" in review_gates:
        return "match_recommended"
    if "send_approval" in review_gates:
        return "awaiting_patient_contact"
    if run_status == "completed" and (approvals or {}).get("send_approval"):
        return "contact_sent"
    if run_status == "completed":
        return "awaiting_patient_contact"
    return "needs_admin_review"


def _draft_text_for_payload(payload: Any) -> str | None:
    if isinstance(payload, dict):
        if "body" in payload:
            return str(payload.get("body") or "")
        if "markdown_body" in payload:
            return str(payload.get("markdown_body") or "")
        if "rationale" in payload:
            return str(payload.get("rationale") or "")
    return None
