"""Repository helpers for Lumen web records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    AuditLog,
    CommunicationDraft,
    HumanReviewTask,
    Patient,
    Referral,
    Tenant,
    Therapist,
    User,
    WorkflowEvent,
    WorkflowRun,
    new_id,
)
from .seed import DEMO_USER_ID


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
        status="normalizing",
        missing_fields=_deterministic_missing_fields(raw_input),
    )
    session.add(referral)
    session.flush()
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
        query = query.where(Referral.status == status)
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
        }
    )
    return detail


def list_review_tasks(session: Session, tenant_id: str | None = None, status: str | None = "open") -> list[dict[str, Any]]:
    query = select(HumanReviewTask).order_by(HumanReviewTask.created_at.desc())
    if tenant_id:
        query = query.where(HumanReviewTask.tenant_id == tenant_id)
    if status and status != "all":
        query = query.where(HumanReviewTask.status == status)
    return [review_task_to_dict(task) for task in session.scalars(query)]


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

    if task.referral_id:
        referral = session.get(Referral, task.referral_id)
        if referral:
            if action == "approve" and task.task_type == "send_approval":
                referral.status = "ready_to_contact"
            elif action == "approve" and task.task_type == "match_approval":
                referral.status = "outreach_draft_pending"
            elif action == "reject":
                referral.status = "needs_admin_review"

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


def list_therapists(session: Session, tenant_id: str | None = None) -> list[dict[str, Any]]:
    query = select(Therapist).order_by(Therapist.name)
    if tenant_id:
        query = query.where(Therapist.tenant_id == tenant_id)
    return [therapist_to_dict(therapist) for therapist in session.scalars(query)]


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


def update_referral_from_result(session: Session, run: WorkflowRun) -> None:
    referral = session.get(Referral, run.referral_id)
    if referral is None:
        return
    before = referral_summary(referral)
    result = run.result or {}
    outputs = result.get("outputs") or {}

    referral.status = _referral_status_from_result(result, run.status)
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
        payload_key = item.get("payload_key") or "workflow"
        exists = session.scalar(
            select(HumanReviewTask).where(
                HumanReviewTask.workflow_run_id == run.id,
                HumanReviewTask.task_type == task_type,
                HumanReviewTask.payload_key == payload_key,
                HumanReviewTask.status == "open",
            )
        )
        if exists is not None:
            continue
        source_payload = outputs.get(payload_key) or item
        task = HumanReviewTask(
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
        session.add(task)
        session.flush()
        write_audit(
            session,
            tenant_id=run.tenant_id,
            action="create",
            entity_type="human_review_task",
            entity_id=task.id,
            after=review_task_to_dict(task),
        )


def approval_payload_for_task(session: Session, task: HumanReviewTask) -> dict[str, Any] | None:
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
    return {
        "id": referral.id,
        "tenant_id": referral.tenant_id,
        "patient_id": referral.patient_id,
        "workflow_run_id": referral.workflow_run_id,
        "source_channel": referral.source_channel,
        "status": referral.status,
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
        "communication_draft_id": referral.communication_draft_id,
        "created_at": iso_or_none(referral.created_at),
        "updated_at": iso_or_none(referral.updated_at),
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


def _referral_status_from_result(result: dict[str, Any], run_status: str) -> str:
    if run_status == "failed" or result.get("errors"):
        return "needs_admin_review"
    review_gates = {item.get("gate") for item in result.get("human_review_queue") or []}
    outputs = result.get("outputs") or {}
    risk = outputs.get("risk_review") or {}
    if "clinical_review" in review_gates or risk.get("required_handoff") in {"clinician_review", "director_review"}:
        return "needs_clinical_review"
    if "match_approval" in review_gates:
        return "match_pending_approval"
    if "send_approval" in review_gates:
        return "outreach_draft_pending"
    if run_status == "completed":
        return "ready_to_contact"
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
