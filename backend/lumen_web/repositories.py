"""Repository helpers for Lumen web records."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from .models import (
    Appointment,
    AuditLog,
    ClinicalLibraryRecord,
    CommunicationDraft,
    ConsentRecord,
    Document,
    DocumentChunk,
    DocumentationSession,
    DocumentationSessionNote,
    DocumentationProgressOverview,
    DocumentationSessionText,
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
from . import google_workspace
from .seed import DEMO_TENANT_ID, DEMO_THERAPIST_USER_ID, DEMO_USER_ID, seed_demo_data
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


CLOSED_REFERRAL_STATUSES = {"closed_declined", "closed_no_response", "closed_not_suitable"}

REFERRAL_JOURNEY_STAGES = (
    {
        "id": "triage",
        "label": "Triage",
        "description": "Captured referrals, normalisation, and missing information.",
        "statuses": ("new_referral", "normalising", "needs_admin_review", "waiting_for_missing_info"),
    },
    {
        "id": "clinical",
        "label": "Clinical review",
        "description": "Risk, suitability, and escalation gates before matching.",
        "statuses": ("needs_clinical_review", "clinical_escalation_review"),
    },
    {
        "id": "matching",
        "label": "Matching",
        "description": "Therapist recommendation and match approval.",
        "statuses": ("ready_for_matching", "match_recommended", "match_approved"),
    },
    {
        "id": "contact_scheduling",
        "label": "Contact & scheduling",
        "description": "Slot offers, patient contact, replies, and appointment confirmation.",
        "statuses": (
            "slot_options_ready",
            "awaiting_patient_contact",
            "contact_sent",
            "awaiting_patient_reply",
            "appointment_confirmed",
        ),
    },
    {
        "id": "intake_prep",
        "label": "Intake & prep",
        "description": "Intake packet, outstanding paperwork, and therapist prep brief.",
        "statuses": ("intake_packet_sent", "intake_incomplete", "intake_complete", "prep_brief_ready"),
    },
    {
        "id": "ready",
        "label": "Ready",
        "description": "First-session readiness reached.",
        "statuses": ("first_session_ready",),
    },
)

REFERRAL_STAGE_BY_STATUS = {
    status: stage["id"]
    for stage in REFERRAL_JOURNEY_STAGES
    for status in stage["statuses"]
}

REVIEW_TASK_NEXT_ACTIONS = {
    "admin_missing_info_review": ("review_missing_info", "Resolve missing information"),
    "missing_info_message_approval": ("approve_contact", "Approve missing-info message"),
    "duplicate_resolution": ("review_missing_info", "Resolve duplicate candidate"),
    "clinical_risk_review": ("clinical_review", "Complete clinical review"),
    "suitability_review": ("clinical_review", "Complete suitability review"),
    "match_approval": ("approve_match", "Approve therapist match"),
    "slot_offer_approval": ("approve_slots", "Approve slot options"),
    "send_approval": ("approve_contact", "Approve patient contact"),
    "appointment_confirmation_approval": ("confirm_appointment", "Confirm appointment"),
    "appointment_reschedule_approval": ("confirm_appointment", "Approve reschedule"),
    "intake_reminder_approval": ("complete_intake", "Approve intake reminder"),
    "intake_exception_approval": ("complete_intake", "Review intake exception"),
    "intake_submission_review": ("complete_intake", "Review intake submission"),
    "inbound_reply_review": ("review_gate", "Review inbound reply"),
}

REVIEW_TASK_PRIORITY = (
    "clinical_risk_review",
    "suitability_review",
    "appointment_confirmation_approval",
    "appointment_reschedule_approval",
    "intake_exception_approval",
    "intake_submission_review",
    "inbound_reply_review",
    "admin_missing_info_review",
    "duplicate_resolution",
    "missing_info_message_approval",
    "match_approval",
    "slot_offer_approval",
    "send_approval",
    "intake_reminder_approval",
)

ACTION_OWNER_LABELS = {
    "review_referral": "Admin",
    "review_missing_info": "Admin",
    "clinical_review": "Clinician",
    "run_matching": "Agent",
    "approve_match": "Admin",
    "approve_slots": "Admin",
    "approve_contact": "Admin",
    "wait_patient_reply": "Patient",
    "confirm_appointment": "Admin",
    "start_intake": "Admin",
    "draft_intake_packet": "Agent",
    "complete_intake": "Patient / admin",
    "generate_prep_brief": "Agent",
    "ready": "Complete",
    "closed": "Complete",
    "review_gate": "Admin",
    "revise_agent_output": "Agent / admin",
    "retry_extraction": "Agent",
    "wait_extraction": "Agent",
    "review_first_response": "Admin",
    "review_prepared_email": "Admin",
    "send_email": "Admin",
    "sync_replies": "Admin",
    "resolve_reply": "Admin",
    "resolve_match": "Admin",
    "continue_email_workflow": "Admin",
}

NEXT_ACTION_ALLOWED_ACTIONS = {
    "review_referral": ("draft_missing_info", "record_missing_reply", "duplicate_review", "clinical_review"),
    "review_missing_info": ("draft_missing_info", "record_missing_reply", "duplicate_review", "clinical_review"),
    "clinical_review": ("clinical_review", "suitability_review"),
    "run_matching": ("run_match",),
    "approve_match": ("review_gate", "run_match"),
    "approve_slots": ("review_gate", "propose_slots"),
    "approve_contact": ("review_gate", "draft_first_contact"),
    "wait_patient_reply": ("record_patient_reply",),
    "confirm_appointment": ("review_gate",),
    "start_intake": ("start_intake", "draft_intake_packet"),
    "draft_intake_packet": ("draft_intake_packet",),
    "complete_intake": ("draft_intake_reminder", "generate_prep_brief"),
    "generate_prep_brief": ("generate_prep_brief",),
    "ready": (),
    "closed": (),
    "review_gate": ("review_gate",),
    "revise_agent_output": ("revise_agent_output",),
    "retry_extraction": ("retry_extraction",),
    "wait_extraction": (),
    "review_first_response": ("review_gate",),
    "review_prepared_email": ("review_gate",),
    "send_email": ("review_gate",),
    "sync_replies": ("sync_replies", "record_patient_reply"),
    "resolve_reply": ("review_gate", "record_missing_reply"),
    "resolve_match": ("run_match", "suitability_review"),
    "continue_email_workflow": ("continue_email_workflow", "retry_extraction"),
}

REQUEST_CHANGES_ACTIONS = {
    "admin_missing_info_review": ("draft_missing_info", "record_missing_reply"),
    "missing_info_message_approval": ("draft_missing_info",),
    "duplicate_resolution": ("duplicate_review",),
    "clinical_risk_review": ("clinical_review",),
    "suitability_review": ("suitability_review",),
    "send_approval": ("draft_first_contact", "draft_intake_packet", "draft_intake_reminder"),
    "match_approval": ("run_match",),
    "slot_offer_approval": ("propose_slots",),
    "appointment_confirmation_approval": ("record_patient_reply", "propose_slots"),
    "appointment_reschedule_approval": ("review_gate",),
    "intake_reminder_approval": ("draft_intake_reminder",),
    "intake_exception_approval": ("complete_intake",),
    "intake_submission_review": ("complete_intake", "draft_intake_reminder"),
}

GMAIL_APPROVAL_TASK_TYPES = {
    "missing_info_message_approval",
    "send_approval",
    "intake_reminder_approval",
}

INBOUND_GMAIL_STORAGE_PREFIX = "gmail:message:"
INTAKE_TEMPLATE_FILE_DOCUMENT_TYPE = "intake_template_file"
INTAKE_REPLY_STATUSES = {"intake_packet_sent", "intake_incomplete"}
REPO_ROOT = Path(__file__).resolve().parents[2]

DEMO_OUTBOUND_PATIENT_EMAIL = "lumenpatientdemo@gmail.com"
DEMO_CLEAN_PATIENT_EMAIL = "clean.demo.patient@example.com"
DEMO_CLARA_EMAIL = "clara.demo1234@gmail.com"
DEMO_CLEAN_REFERRAL_ID = "demo-clean-referral-001"
DEMO_CLEAN_PATIENT_ID = "demo-clean-patient-001"
DEMO_CLEAN_THERAPIST_ID = "demo-clean-therapist-001"
DEMO_CLEAN_INTAKE_TEMPLATE_ID = "demo-clean-intake-template"
DEMO_CLARA_DOCUMENTATION_SESSION_PREFIX = "demo-clara-doc-session"
DEMO_CLARA_DOCUMENTATION_TEXT_PREFIX = "demo-clara-doc-text"
DEMO_STAGE_PREFIX = "demo-stage-"
DEMO_STAGE_SOURCE = "demo_stage"
DEMO_STAGE_SEED_SOURCE = "demo_seed"
DEMO_GMAIL_INTAKE_TEMPLATE_NAME = "Standard first-session intake"
DEMO_GMAIL_INTAKE_PACKET_ASSET_DIR = REPO_ROOT / "backend" / "lumen_web" / "demo_assets" / "intake_packet"
DEMO_GMAIL_INTAKE_REQUIRED_ITEMS = [
    {
        "key": "privacy_notice",
        "label": "Privacy notice acknowledged",
        "type": "consent",
        "consent_scope": "privacy_notice",
        "due_days": 3,
        "demo_asset_file": "privacy_notice_acknowledged.docx",
    },
    {
        "key": "telehealth_consent",
        "label": "Telehealth consent",
        "type": "consent",
        "consent_scope": "telehealth",
        "due_days": 3,
        "demo_asset_file": "telehealth_consent.docx",
    },
    {
        "key": "intake_form",
        "label": "Clinical intake form",
        "type": "form",
        "due_days": 5,
        "demo_asset_file": "clinical_intake_form.docx",
    },
    {
        "key": "screening_questionnaire",
        "label": "Pre-session screening questionnaire",
        "type": "questionnaire",
        "due_days": 5,
        "demo_asset_file": "pre_session_screening_questionnaire.docx",
    },
]
DEMO_GMAIL_INTAKE_FILENAME_HINTS = {
    "privacy_notice": ("privacy_notice", "privacy_notice_acknowledged"),
    "telehealth_consent": ("telehealth_consent",),
    "intake_form": ("intake_form", "clinical_intake_form"),
    "screening_questionnaire": ("screening_questionnaire", "pre_session_screening_questionnaire"),
}
SESSION_LENGTH_MINUTES = 60
SESSION_BUFFER_MINUTES = 10
THERAPIST_WEEKLY_PATIENT_CONTACT_CAP_HOURS = 20
EMAIL_WORKFLOW_STALE_MINUTES = int(os.getenv("LUMEN_EMAIL_WORKFLOW_STALE_MINUTES", "30"))
EMAIL_FOLLOWUP_NON_BLOCKING_MISSING_FIELDS = {
    "date_of_birth",
    "dob",
    "contact_phone_or_date_of_birth",
    "contact_phone",
    "phone",
    "insurer",
    "insurance",
    "referring_entity",
    "patient_name",
}
DEFAULT_AVAILABILITY_BLOCKS = [
    {"weekday": day, "start": "08:00", "end": "21:00", "modality": "online"}
    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
]


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
    source_channel = str(raw_input.get("source_channel") or "webform")
    is_email_referral = source_channel.strip().lower() == "email"
    referral = Referral(
        tenant_id=request.tenant_id,
        patient_id=request.patient_id,
        source_channel=source_channel,
        raw_text=str(raw_input.get("raw_text") or ""),
        uploaded_file_name=raw_input.get("uploaded_file_name"),
        status="normalising",
        contact_email=_extract_email_address(str(raw_input.get("contact_email") or raw_input.get("sender") or "")),
        missing_fields=[] if is_email_referral else _deterministic_missing_fields(raw_input),
    )
    session.add(referral)
    session.flush()
    if not is_email_referral:
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


def set_workflow_execution_input(session: Session, job_id: str, raw_input: dict[str, Any]) -> None:
    run = get_workflow_run(session, job_id)
    payload = dict(run.request_payload or {})
    payload["raw_input"] = json_safe(raw_input)
    run.request_payload = payload
    run.updated_at = utc_now()


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
    if run.referral_id:
        referral = session.get(Referral, run.referral_id)
        if referral:
            _normalise_email_optional_contact_missing_fields(session, referral)
    session.flush()
    return run


def _maybe_prepare_email_referral_followup(session: Session, run: WorkflowRun) -> None:
    return


def prepare_email_referral_followup(
    session: Session,
    referral_id: str,
    *,
    availability_text: str | None = None,
    force_new_draft: bool = False,
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    if str(referral.source_channel or "").strip().lower() == "email":
        raise ValueError("Email referrals use the canonical LangGraph workflow for follow-up preparation.")
    if _email_followup_has_clinical_blocker(referral):
        return {"status": "blocked", "reason": "clinical_or_risk_review_required", "referral": referral_summary(referral)}
    blocking_missing = _matching_blocking_missing_fields(referral)
    if blocking_missing:
        return {
            "status": "blocked",
            "reason": "blocking_missing_information",
            "missing_fields": blocking_missing,
            "referral": referral_summary(referral),
        }

    patient = _ensure_patient_for_referral(session, referral)
    match = deterministic_match_for_referral(session, referral.id, allow_noncritical_missing=True)
    therapist_id = _top_match_therapist_id(referral)
    if not therapist_id:
        return {
            "status": "partial",
            "reason": "no_eligible_therapist",
            "referral": referral_summary(referral),
            "patient_id": patient.id,
            "match": match,
            "appointments": [],
            "draft": None,
        }
    proposals = propose_appointment_slots(
        session,
        referral.id,
        therapist_id=therapist_id,
        limit=1,
        availability_text=availability_text,
    )
    draft = _existing_slot_contact_draft(session, referral) if not force_new_draft else None
    if proposals and draft is None:
        draft = draft_first_contact_message(session, referral.id)
    session.flush()
    return {
        "status": "prepared" if proposals and draft else "partial",
        "referral": referral_summary(referral),
        "patient_id": patient.id,
        "match": match,
        "appointments": proposals,
        "draft": draft,
    }


def continue_email_referral_workflow(session: Session, referral_id: str) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    if str(referral.source_channel or "").strip().lower() != "email":
        raise ValueError("Only email referrals can be continued from email facts.")
    tasks = list(
        session.scalars(
            select(HumanReviewTask)
            .where(HumanReviewTask.referral_id == referral.id)
            .order_by(HumanReviewTask.created_at.desc())
        )
    )
    drafts = list(
        session.scalars(
            select(CommunicationDraft)
            .where(CommunicationDraft.referral_id == referral.id)
            .order_by(CommunicationDraft.created_at.desc())
        )
    )
    workflows = list(
        session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.referral_id == referral.id, WorkflowRun.workflow_type == "new_referral")
            .order_by(WorkflowRun.created_at.desc())
        )
    )
    _mark_stale_workflows_for_referral(session, referral, workflows)
    latest_workflow = workflows[0] if workflows else None
    open_tasks = [task for task in tasks if task.status == "open"]
    sent_draft = next((draft for draft in drafts if draft.status == "sent"), None)

    if open_tasks:
        result = {
            "status": "needs_review",
            "action": "review_existing",
            "message": "Email workflow is paused at a human review gate.",
        }
    elif latest_workflow and latest_workflow.status in {"queued", "running"}:
        result = {
            "status": latest_workflow.status,
            "action": "wait",
            "workflow": workflow_run_to_dict(latest_workflow, include_events=False),
        }
    elif sent_draft:
        result = {
            "status": "waiting_for_reply",
            "action": "sync_replies",
            "message": "Patient email has been sent; sync Gmail for replies.",
        }
    else:
        result = {
            "status": "ready_to_start",
            "action": "start_workflow",
            "tenant_id": referral.tenant_id,
            "patient_id": referral.patient_id,
            "referral_id": referral.id,
            "raw_input": {
                "source_channel": referral.source_channel or "email",
                "raw_text": referral.raw_text or "",
                "contact_email": referral.contact_email,
                "sender": referral.contact_email,
            },
        }
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="continue_email_referral_workflow",
        entity_type="referral",
        entity_id=referral.id,
        after={"result": json_safe(result)},
    )
    return {
        "status": result.get("status", "prepared"),
        "result": result,
        "referral": referral_summary(referral),
    }


def _apply_deterministic_email_referral_facts(
    session: Session,
    referral: Referral,
    *,
    raw_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {}


def _clear_email_demo_duplicate_candidates(session: Session, referral: Referral) -> bool:
    if str(referral.source_channel or "").strip().lower() != "email":
        return False
    if not referral.duplicate_candidates:
        return False
    before = referral_summary(referral)
    referral.duplicate_candidates = []
    referral.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="demo_bypass_email_duplicate_candidates",
        entity_type="referral",
        entity_id=referral.id,
        before=before,
        after=referral_summary(referral),
    )
    return True


def _deterministic_email_missing_fields(raw_text: str, referral: Referral) -> list[str]:
    text = str(raw_text or "")
    existing = {str(field or "").strip() for field in (referral.missing_fields or []) if str(field or "").strip()}
    missing: list[str] = []
    if not referral.patient_name:
        missing.append("patient_name")
    if not (referral.contact_email or _extract_email_address(text)):
        missing.append("contact_email")
    if not (referral.date_of_birth or referral.contact_phone or _extract_date_of_birth(text) or _extract_phone_number(text)):
        if {"date_of_birth", "dob"} & existing:
            missing.append("date_of_birth")
        elif {"contact_phone", "phone"} & existing:
            missing.append("contact_phone")
        else:
            missing.append("contact_phone_or_date_of_birth")
    if not (referral.insurer or _extract_insurer(text)):
        missing.append("insurer")
    return list(dict.fromkeys(missing))


def _email_followup_has_clinical_blocker(referral: Referral) -> bool:
    status = canonical_referral_status(referral.status)
    return (
        status in {"needs_clinical_review", "clinical_escalation_review"}
        or bool(referral.risk_present)
        or referral.urgency in {"elevated", "urgent", "unknown"}
        or referral.risk_category == "unknown"
    )


def _existing_slot_contact_draft(session: Session, referral: Referral) -> dict[str, Any] | None:
    drafts = list(
        session.scalars(
            select(CommunicationDraft)
            .where(
                CommunicationDraft.referral_id == referral.id,
                CommunicationDraft.status.in_(["draft_pending_review", "approved_pending_send", "sent"]),
            )
            .order_by(CommunicationDraft.created_at.desc())
            .limit(10)
        )
    )
    draft = next((item for item in drafts if item.proposed_slots), None)
    return communication_draft_to_dict(draft) if draft else None


def _review_task_exists(
    session: Session,
    referral_id: str,
    task_type: str,
    *,
    statuses: tuple[str, ...],
) -> bool:
    return bool(
        session.scalar(
            select(HumanReviewTask.id)
            .where(
                HumanReviewTask.referral_id == referral_id,
                HumanReviewTask.task_type == task_type,
                HumanReviewTask.status.in_(list(statuses)),
            )
            .limit(1)
        )
    )


def _open_inbound_reply_task(session: Session, referral: Referral) -> HumanReviewTask | None:
    return session.scalar(
        select(HumanReviewTask)
        .where(
            HumanReviewTask.referral_id == referral.id,
            HumanReviewTask.task_type == "inbound_reply_review",
            HumanReviewTask.status == "open",
        )
        .order_by(HumanReviewTask.created_at.desc())
        .limit(1)
    )


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
    _mark_workflow_stale_if_needed(session, run)
    return workflow_run_to_dict(run)


def _mark_stale_workflows_for_referral(
    session: Session,
    referral: Referral,
    workflows: list[WorkflowRun] | None = None,
) -> None:
    runs = workflows
    if runs is None:
        runs = list(
            session.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.referral_id == referral.id)
                .order_by(WorkflowRun.created_at.desc())
            )
        )
    changed = False
    for run in runs:
        if _mark_workflow_stale_if_needed(session, run):
            changed = True
    if changed and canonical_referral_status(referral.status) == "normalising":
        transition_referral_status(
            session,
            referral,
            "needs_admin_review",
            reason="Agent extraction timed out; retry is required.",
        )


def _mark_workflow_stale_if_needed(session: Session, run: WorkflowRun) -> bool:
    if run.status != "running":
        return False
    updated_at = _normalise_datetime(run.updated_at or run.created_at or utc_now())
    if utc_now() - updated_at <= timedelta(minutes=EMAIL_WORKFLOW_STALE_MINUTES):
        return False
    before = workflow_run_summary(run)
    run.status = "failed"
    run.error = f"Workflow was still running after {EMAIL_WORKFLOW_STALE_MINUTES} minutes; retry extraction."
    run.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=run.tenant_id,
        action="mark_stale",
        entity_type="workflow_run",
        entity_id=run.id,
        before=before,
        after=workflow_run_summary(run),
    )
    if run.referral_id:
        referral = session.get(Referral, run.referral_id)
        if referral is not None and canonical_referral_status(referral.status) == "normalising":
            transition_referral_status(
                session,
                referral,
                "needs_admin_review",
                reason="Agent extraction timed out; retry is required.",
            )
    if not any(event.node == "workflow_timeout" for event in run.events):
        append_workflow_event(
            session,
            job_id=run.id,
            event_type="error",
            status="failed",
            message=run.error,
            node="workflow_timeout",
        )
    session.flush()
    return True


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


def referral_journey_dashboard(session: Session, tenant_id: str | None = None) -> dict[str, Any]:
    query = select(Referral).order_by(Referral.updated_at.desc())
    if tenant_id:
        query = query.where(Referral.tenant_id == tenant_id)
    referrals = [
        referral
        for referral in session.scalars(query)
        if canonical_referral_status(referral.status) not in CLOSED_REFERRAL_STATUSES
    ]
    referral_ids = [referral.id for referral in referrals]

    tasks_by_referral: dict[str, list[HumanReviewTask]] = {referral_id: [] for referral_id in referral_ids}
    if referral_ids:
        for task in session.scalars(
            select(HumanReviewTask)
            .where(HumanReviewTask.referral_id.in_(referral_ids), HumanReviewTask.status == "open")
            .order_by(HumanReviewTask.created_at.desc())
        ):
            if task.referral_id:
                tasks_by_referral.setdefault(task.referral_id, []).append(task)

    cards = [
        _referral_journey_card(session, referral, tasks_by_referral.get(referral.id, []))
        for referral in referrals
    ]
    cards_by_stage: dict[str, list[dict[str, Any]]] = {stage["id"]: [] for stage in REFERRAL_JOURNEY_STAGES}
    for card in cards:
        cards_by_stage.setdefault(card["stage_id"], []).append(card)

    metrics = {
        "active_referrals": len(cards),
        "needs_action": len([card for card in cards if _journey_card_needs_action(card)]),
        "clinical_escalations": len(
            [
                card
                for card in cards
                if card["status"] in {"needs_clinical_review", "clinical_escalation_review"}
                or any(blocker["code"] == "clinical_escalation" for blocker in card["blockers"])
            ]
        ),
        "intake_blockers": len(
            [card for card in cards if any(blocker["code"] == "intake_incomplete" for blocker in card["blockers"])]
        ),
        "first_session_ready": len([card for card in cards if card["status"] == "first_session_ready"]),
        "blocked_referrals": len([card for card in cards if card["blockers"]]),
    }
    return {
        "metrics": metrics,
        "stages": [
            {
                "id": stage["id"],
                "label": stage["label"],
                "description": stage["description"],
                "statuses": list(stage["statuses"]),
                "count": len(cards_by_stage.get(stage["id"], [])),
                "referrals": cards_by_stage.get(stage["id"], []),
            }
            for stage in REFERRAL_JOURNEY_STAGES
        ],
    }


def referral_detail(session: Session, referral_id: str) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    _normalise_email_optional_contact_missing_fields(session, referral)

    drafts = list(
        session.scalars(select(CommunicationDraft).where(CommunicationDraft.referral_id == referral_id).order_by(CommunicationDraft.created_at.desc()))
    )
    tasks = list(
        session.scalars(select(HumanReviewTask).where(HumanReviewTask.referral_id == referral_id).order_by(HumanReviewTask.created_at.desc()))
    )
    workflows = list(
        session.scalars(select(WorkflowRun).where(WorkflowRun.referral_id == referral_id).order_by(WorkflowRun.created_at.desc()))
    )
    _mark_stale_workflows_for_referral(session, referral, workflows)
    detail = referral_summary(referral)
    detail.update(
        {
            "raw_text": referral.raw_text,
            "communication_drafts": [communication_draft_to_dict(draft) for draft in drafts],
            "review_tasks": [review_task_to_dict(task) for task in tasks],
            "workflow_runs": [workflow_run_to_dict(run, include_events=False) for run in workflows],
            "documents": _referral_all_documents(session, referral),
            "patient_replies": _referral_documents(session, referral, "patient_reply"),
            "missing_info_replies": _referral_documents(session, referral, "missing_info_reply"),
            "readiness_blockers": _first_session_readiness_blockers(session, referral),
            "workbench_state": _referral_workbench_state(
                session,
                referral,
                tasks=tasks,
                drafts=drafts,
                workflows=workflows,
            ),
        }
    )
    return detail


def referral_workbench_state(session: Session, referral_id: str) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    _normalise_email_optional_contact_missing_fields(session, referral)
    tasks = list(
        session.scalars(select(HumanReviewTask).where(HumanReviewTask.referral_id == referral_id).order_by(HumanReviewTask.created_at.desc()))
    )
    drafts = list(
        session.scalars(select(CommunicationDraft).where(CommunicationDraft.referral_id == referral_id).order_by(CommunicationDraft.created_at.desc()))
    )
    workflows = list(
        session.scalars(select(WorkflowRun).where(WorkflowRun.referral_id == referral_id).order_by(WorkflowRun.created_at.desc()))
    )
    _mark_stale_workflows_for_referral(session, referral, workflows)
    return _referral_workbench_state(session, referral, tasks=tasks, drafts=drafts, workflows=workflows)


def referral_retry_workflow_input(session: Session, referral_id: str) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    if canonical_referral_status(referral.status) not in {"normalising", "needs_admin_review"}:
        raise ValueError("Only normalising or failed-review referrals can be retried.")
    raw_text = referral.raw_text or ""
    if not raw_text.strip():
        raise ValueError("Referral has no raw email text to retry.")
    return {
        "tenant_id": referral.tenant_id,
        "patient_id": referral.patient_id,
        "referral_id": referral.id,
        "raw_input": {
            "source_channel": referral.source_channel or "email",
            "raw_text": raw_text,
            "contact_email": referral.contact_email,
            "sender": referral.contact_email,
        },
    }


def _referral_workbench_state(
    session: Session,
    referral: Referral,
    *,
    tasks: list[HumanReviewTask],
    drafts: list[CommunicationDraft],
    workflows: list[WorkflowRun],
) -> dict[str, Any]:
    summary = referral_summary(referral)
    status = summary["status"]
    stage = _journey_stage(status)
    packet_state = _intake_packet_state(session, referral, tasks=tasks, drafts=drafts)
    if _supersede_premature_intake_reminder_tasks(
        session,
        referral,
        packet_state=packet_state,
        tasks=tasks,
        drafts=drafts,
    ):
        packet_state = _intake_packet_state(session, referral, tasks=tasks, drafts=drafts)
    open_tasks = [task for task in tasks if task.status == "open"]
    change_tasks = [task for task in tasks if task.status == "changes_requested"]
    email_workflow = _email_workflow_packet(session, referral, tasks=tasks, drafts=drafts, workflows=workflows)
    next_action, next_label = _task_aware_next_action(summary, open_tasks)
    blockers = _journey_blockers(session, referral, open_tasks)

    open_gate = _highest_priority_task(open_tasks)
    changed_gate = change_tasks[0] if change_tasks else None
    primary_action = next_action
    primary_action_label = next_label
    owner = ACTION_OWNER_LABELS.get(next_action, "Admin")
    allowed_actions = list(NEXT_ACTION_ALLOWED_ACTIONS.get(next_action, ("review_referral",)))

    if open_gate is not None:
        primary_action = "review_gate"
        primary_action_label = REVIEW_TASK_NEXT_ACTIONS.get(
            open_gate.task_type,
            ("review_gate", f"Review {open_gate.task_type.replace('_', ' ')}"),
        )[1]
        owner = "Clinician" if open_gate.task_type in {"clinical_risk_review", "suitability_review"} else "Admin"
        allowed_actions = ["review_gate"]
    elif changed_gate is not None:
        primary_action = "revise_agent_output"
        primary_action_label = _changes_requested_action_label(changed_gate)
        owner = ACTION_OWNER_LABELS[primary_action]
        allowed_actions = _actions_for_changes_task(changed_gate)
        _append_blocker(
            blockers,
            "changes_requested",
            f"Changes requested: {changed_gate.rejection_reason or changed_gate.reason}",
            "warning",
        )

    if email_workflow is not None:
        primary_action = email_workflow["next_action"]
        primary_action_label = email_workflow["next_action_label"]
        owner = ACTION_OWNER_LABELS.get(primary_action, "Admin")
        allowed_actions = list(NEXT_ACTION_ALLOWED_ACTIONS.get(primary_action, ("review_gate",)))
        for blocker in email_workflow.get("blockers") or []:
            _append_blocker(blockers, blocker["code"], blocker["label"], blocker.get("severity", "warning"))

    if packet_state.get("state") != "sent":
        allowed_actions = [action for action in allowed_actions if action != "draft_intake_reminder"]

    primary_blocker = _primary_blocker(blockers)
    if changed_gate is not None:
        primary_blocker = {
            "code": "changes_requested",
            "label": f"Changes requested: {changed_gate.rejection_reason or changed_gate.reason}",
            "severity": "warning",
        }
    if primary_blocker is None and open_gate is not None:
        primary_blocker = {
            "code": f"review_{open_gate.task_type}",
            "label": open_gate.reason,
            "severity": "warning",
        }

    return {
        "stage_id": stage["id"],
        "stage_label": stage["label"],
        "stage_description": stage["description"],
        "primary_status": status,
        "primary_status_label": summary["status_label"],
        "primary_blocker": primary_blocker,
        "blockers": blockers,
        "owner": owner,
        "primary_action": primary_action,
        "primary_action_label": primary_action_label,
        "allowed_actions": allowed_actions,
        "open_review_gate": review_task_to_dict(open_gate) if open_gate else None,
        "changes_requested_gate": review_task_to_dict(changed_gate) if changed_gate else None,
        "progress": _workbench_progress_facts(session, referral, tasks=tasks, drafts=drafts),
        "agent_outputs": _agent_outputs_for_referral(session, referral, tasks=tasks, drafts=drafts),
        "activity": _referral_activity(session, referral, tasks=tasks, drafts=drafts, workflows=workflows),
        "advanced_trace": _advanced_trace_for_referral(workflows),
        "email_workflow": email_workflow,
    }


def _advanced_trace_for_referral(workflows: list[WorkflowRun]) -> dict[str, Any]:
    return {
        "workflow_runs": [workflow_run_to_dict(run, include_events=True) for run in workflows],
    }


def _email_workflow_packet(
    session: Session,
    referral: Referral,
    *,
    tasks: list[HumanReviewTask],
    drafts: list[CommunicationDraft],
    workflows: list[WorkflowRun],
) -> dict[str, Any] | None:
    if str(referral.source_channel or "").strip().lower() != "email":
        return None

    latest_workflow = next((run for run in workflows if run.workflow_type == "new_referral"), None)
    proposed_appointment = _latest_appointment_for_referral(session, referral, statuses=("proposed",))
    confirmed_appointment = _latest_appointment_for_referral(session, referral, statuses=("confirmed",))
    actionable_drafts = [draft for draft in drafts if draft.status != "superseded"]
    slot_draft = next((draft for draft in actionable_drafts if draft.proposed_slots), None)
    latest_sent_draft = next((draft for draft in actionable_drafts if draft.status == "sent"), None)
    latest_draft = slot_draft or (actionable_drafts[0] if actionable_drafts else None)
    packet_state = _intake_packet_state(session, referral, tasks=tasks, drafts=drafts)
    open_tasks = [task for task in tasks if task.status == "open"]
    open_task_types = {task.task_type for task in open_tasks}
    has_non_intake_send_approval = any(
        task.task_type == "send_approval" and not _is_intake_packet_send_task(task)
        for task in open_tasks
    )
    relevant_task_types = {
        "admin_missing_info_review",
        "match_approval",
        "slot_offer_approval",
        "send_approval",
        "appointment_confirmation_approval",
        "inbound_reply_review",
        "intake_reminder_approval",
        "intake_submission_review",
    }
    packet_tasks = [review_task_to_dict(task) for task in open_tasks if task.task_type in relevant_task_types]
    no_match = _email_no_match_summary(referral)
    blockers: list[dict[str, str]] = []
    if no_match:
        blockers.append({"code": "no_eligible_therapist", "label": no_match["label"], "severity": "warning"})

    has_response_artifacts = bool(
        proposed_appointment
        or latest_draft
        or confirmed_appointment
        or (open_task_types & relevant_task_types)
    )
    if canonical_referral_status(referral.status) == "first_session_ready":
        next_action = "ready"
        next_label = next_action_label("ready")
    elif latest_workflow and latest_workflow.status == "failed" and not has_response_artifacts:
        next_action = "retry_extraction"
        next_label = "Retry extraction"
    elif latest_workflow and latest_workflow.status in {"queued", "running"} and not has_response_artifacts:
        next_action = "wait_extraction"
        next_label = "Waiting for extraction"
    elif "inbound_reply_review" in open_task_types:
        next_action = "resolve_reply"
        next_label = "Resolve patient reply"
    elif "appointment_confirmation_approval" in open_task_types:
        next_action = "confirm_appointment"
        next_label = "Create Google Calendar event"
    elif "intake_submission_review" in open_task_types:
        next_action = "complete_intake"
        next_label = "Review intake submission"
    elif packet_state["state"] == "draft_pending_review":
        next_action = "review_prepared_email"
        next_label = "Review intake packet"
    elif confirmed_appointment and packet_state["state"] == "not_drafted" and canonical_referral_status(referral.status) in {
        "intake_incomplete",
        "intake_packet_sent",
        "intake_complete",
        "prep_brief_ready",
        "first_session_ready",
    }:
        next_action = "draft_intake_packet"
        next_label = "Draft intake packet"
    elif "intake_reminder_approval" in open_task_types and packet_state["state"] == "sent":
        next_action = "review_prepared_email"
        next_label = "Approve intake reminder"
    elif has_non_intake_send_approval and _review_prereqs_approved(tasks, require_slot_offer=False):
        next_action = "send_email"
        next_label = "Send email to patient"
    elif (
        open_task_types & {"match_approval", "slot_offer_approval"}
        or has_non_intake_send_approval
        or (latest_draft and latest_draft.status == "draft_pending_review")
    ):
        next_action = "review_prepared_email"
        next_label = "Review prepared email"
    elif referral.duplicate_candidates:
        next_action = "review_missing_info"
        next_label = "Resolve duplicate candidate"
    elif "admin_missing_info_review" in open_task_types:
        next_action = "review_gate"
        next_label = "Resolve missing information"
    elif confirmed_appointment and canonical_referral_status(referral.status) not in {"intake_packet_sent", "intake_incomplete", "intake_complete", "prep_brief_ready"}:
        next_action = "start_intake"
        next_label = "Start intake"
    elif canonical_referral_status(referral.status) in {"intake_packet_sent", "intake_incomplete"}:
        next_action = "complete_intake"
        next_label = "Complete intake"
    elif referral.missing_fields:
        next_action = "continue_email_workflow"
        next_label = "Draft missing-info email"
    elif latest_sent_draft and not confirmed_appointment:
        next_action = "sync_replies"
        next_label = "Sync replies"
    elif no_match:
        next_action = "resolve_match"
        next_label = "Resolve therapist match"
    elif not has_response_artifacts:
        next_action = "continue_email_workflow"
        next_label = "Continue from email"
    else:
        next_action = "review_prepared_email"
        next_label = "Review prepared email"

    return {
        "status": _email_workflow_status(latest_workflow, referral, latest_draft, proposed_appointment, confirmed_appointment),
        "next_action": next_action,
        "next_action_label": next_label,
        "facts": {
            "patient_name": referral.patient_name,
            "date_of_birth": referral.date_of_birth,
            "contact_email": referral.contact_email,
            "contact_phone": referral.contact_phone,
            "insurer": referral.insurer,
            "referring_entity": referral.referring_entity,
            "language_preference": referral.language_preference,
            "modality_preference": referral.modality_preference,
            "missing_fields": list(referral.missing_fields or []),
        },
        "workflow": workflow_run_to_dict(latest_workflow, include_events=False) if latest_workflow else None,
        "match": json_safe(referral.match_summary or {}),
        "no_match": no_match,
        "held_appointment": appointment_to_dict(proposed_appointment) if proposed_appointment else None,
        "confirmed_appointment": appointment_to_dict(confirmed_appointment) if confirmed_appointment else None,
        "draft": communication_draft_to_dict(latest_draft) if latest_draft else None,
        "gmail": {
            "thread_id": latest_draft.gmail_thread_id if latest_draft else None,
            "message_id": latest_draft.gmail_message_id if latest_draft else None,
            "sent_at": iso_or_none(latest_draft.sent_at) if latest_draft else None,
        },
        "review_tasks": packet_tasks,
        "intake_packet_state": packet_state,
        "blockers": blockers,
        "progress": _email_workflow_progress(latest_workflow, referral, latest_draft, proposed_appointment, confirmed_appointment),
    }


def _email_workflow_status(
    workflow: WorkflowRun | None,
    referral: Referral,
    draft: CommunicationDraft | None,
    proposed: Appointment | None,
    confirmed: Appointment | None,
) -> str:
    if confirmed:
        return "confirmed"
    if draft and draft.status == "sent":
        return "waiting_for_reply"
    if draft or proposed:
        return "first_response_prepared"
    if _email_referral_has_extracted_facts(referral):
        return "facts_extracted"
    if workflow and workflow.status in {"queued", "running"}:
        return "extracting"
    if workflow and workflow.status == "failed":
        return "retry_needed"
    if referral.match_summary:
        return "facts_extracted"
    return "received"


def _email_workflow_progress(
    workflow: WorkflowRun | None,
    referral: Referral,
    draft: CommunicationDraft | None,
    proposed: Appointment | None,
    confirmed: Appointment | None,
) -> dict[str, bool]:
    return {
        "email_received": True,
        "facts_extracted": _email_referral_has_extracted_facts(referral) or bool(workflow and workflow.status in {"completed", "needs_review"}),
        "first_response_prepared": bool(draft or proposed),
        "waiting_for_reply": bool(draft and draft.status == "sent"),
        "appointment_confirmation": bool(confirmed),
        "confirmed": bool(confirmed and (not google_workspace.is_enabled() or confirmed.google_calendar_event_id)),
    }


def _email_referral_has_extracted_facts(referral: Referral) -> bool:
    return bool(referral.patient_name or referral.risk_category or referral.language_preference or referral.match_summary)


def _review_prereqs_approved(tasks: list[HumanReviewTask], *, require_slot_offer: bool = True) -> bool:
    approved = {task.task_type for task in tasks if task.status == "approved"}
    if "match_approval" not in approved:
        return False
    return not require_slot_offer or "slot_offer_approval" in approved


def _latest_appointment_for_referral(
    session: Session,
    referral: Referral,
    *,
    statuses: tuple[str, ...],
) -> Appointment | None:
    return session.scalar(
        select(Appointment)
        .where(Appointment.referral_id == referral.id, Appointment.status.in_(list(statuses)))
        .order_by(Appointment.updated_at.desc())
        .limit(1)
    )


def _email_no_match_summary(referral: Referral) -> dict[str, Any] | None:
    match = referral.match_summary or {}
    ranked = match.get("ranked_matches") or []
    excluded = match.get("excluded_therapists") or []
    if ranked or not match:
        return None
    reasons: dict[str, int] = {}
    for item in excluded:
        for reason in item.get("exclusion_reasons") or []:
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    ordered = sorted(reasons.items(), key=lambda item: item[1], reverse=True)
    reason_text = ", ".join(reason for reason, _ in ordered[:3]) or str(match.get("rationale") or "").strip() or "no eligible therapist matched the referral constraints"
    return {
        "label": f"No eligible therapist found: {reason_text}.",
        "reasons": [{"reason": reason, "count": count} for reason, count in ordered],
        "excluded_count": len(excluded),
    }


def _journey_stage(status: str) -> dict[str, Any]:
    stage_id = REFERRAL_STAGE_BY_STATUS.get(status, "triage")
    for stage in REFERRAL_JOURNEY_STAGES:
        if stage["id"] == stage_id:
            return stage
    return REFERRAL_JOURNEY_STAGES[0]


def _highest_priority_task(tasks: list[HumanReviewTask]) -> HumanReviewTask | None:
    if not tasks:
        return None
    priority = {task_type: index for index, task_type in enumerate(REVIEW_TASK_PRIORITY)}
    return sorted(tasks, key=lambda task: (priority.get(task.task_type, len(priority)), task.created_at), reverse=False)[0]


def _primary_blocker(blockers: list[dict[str, str]]) -> dict[str, str] | None:
    if not blockers:
        return None
    severity_rank = {"danger": 0, "warning": 1, "info": 2}
    return sorted(blockers, key=lambda blocker: severity_rank.get(blocker.get("severity", "info"), 3))[0]


def _changes_requested_action_label(task: HumanReviewTask) -> str:
    if task.task_type == "match_approval":
        return "Revise therapist match"
    if task.task_type == "slot_offer_approval":
        return "Revise slot options"
    if task.task_type == "intake_reminder_approval":
        return "Revise intake reminder"
    if task.task_type == "missing_info_message_approval":
        return "Revise missing-info message"
    if task.task_type == "send_approval":
        payload_key = str(task.payload_key or "")
        if payload_key.startswith("intake_packet_draft"):
            return "Revise intake packet"
        if payload_key.startswith("intake_reminder"):
            return "Revise intake reminder"
        return "Revise patient contact"
    return f"Revise {task.task_type.replace('_', ' ')}"


def _actions_for_changes_task(task: HumanReviewTask) -> list[str]:
    if task.task_type == "send_approval":
        payload_key = str(task.payload_key or "")
        if payload_key.startswith("intake_packet_draft"):
            return ["draft_intake_packet"]
        if payload_key.startswith("intake_reminder"):
            return ["draft_intake_reminder"]
        return ["draft_first_contact"]
    return list(REQUEST_CHANGES_ACTIONS.get(task.task_type, ("review_gate",)))


def _workbench_progress_facts(
    session: Session,
    referral: Referral,
    *,
    tasks: list[HumanReviewTask],
    drafts: list[CommunicationDraft],
) -> dict[str, bool]:
    google_enabled = google_workspace.is_enabled()
    status = canonical_referral_status(referral.status)
    open_task_types = {task.task_type for task in tasks if task.status == "open"}
    approved_task_types = {task.task_type for task in tasks if task.status == "approved"}
    sent_contact = any(
        draft.status == "sent" and (not google_enabled or bool(draft.gmail_message_id))
        for draft in drafts
        if draft.proposed_slots
    )
    confirmed_appointments = list(
        session.scalars(
            select(Appointment).where(
                Appointment.referral_id == referral.id,
                Appointment.status == "confirmed",
            )
        )
    )
    appointment_confirmed = bool(confirmed_appointments) and (
        not google_enabled or any(appointment.google_calendar_event_id for appointment in confirmed_appointments)
    )
    items, consents = _intake_requirements_for_referral(session, referral)
    intake_complete = _intake_status(items, consents) == "complete"
    prep_brief_generated = bool(
        session.scalar(
            select(func.count(TherapistPrepBrief.id)).where(TherapistPrepBrief.referral_id == referral.id)
        )
    )
    reviewed = (
        not referral.missing_fields
        and "admin_missing_info_review" not in open_task_types
        and "clinical_risk_review" not in open_task_types
        and "suitability_review" not in open_task_types
        and status not in {"needs_admin_review", "waiting_for_missing_info", "needs_clinical_review", "clinical_escalation_review"}
    )
    matched = (
        bool((referral.match_summary or {}).get("ranked_matches"))
        and ("match_approval" in approved_task_types or status not in {"ready_for_matching", "match_recommended"})
    )
    slots_approved = "slot_offer_approval" in approved_task_types or status in {
        "awaiting_patient_contact",
        "contact_sent",
        "awaiting_patient_reply",
        "appointment_confirmed",
        "intake_packet_sent",
        "intake_incomplete",
        "intake_complete",
        "prep_brief_ready",
        "first_session_ready",
    }
    prep_brief_ready = prep_brief_generated and appointment_confirmed and intake_complete
    first_session_ready = not _first_session_readiness_blockers(session, referral)
    return {
        "captured": True,
        "reviewed": reviewed,
        "matched": matched,
        "slots_approved": slots_approved,
        "contacted": sent_contact,
        "appointment_confirmed": appointment_confirmed,
        "intake_complete": intake_complete,
        "prep_brief_ready": prep_brief_ready,
        "first_session_ready": first_session_ready,
    }


def _agent_outputs_for_referral(
    session: Session,
    referral: Referral,
    *,
    tasks: list[HumanReviewTask],
    drafts: list[CommunicationDraft],
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    task_by_draft = {
        str((task.source_payload or {}).get("id")): task
        for task in tasks
        if isinstance(task.source_payload, dict) and (task.source_payload or {}).get("id")
    }
    for draft in drafts[:6]:
        review_task = task_by_draft.get(draft.id) or _task_for_payload_prefix(tasks, draft.id)
        outputs.append(
            {
                "id": draft.id,
                "type": _draft_output_type(draft),
                "title": draft.subject or "Draft message",
                "status": review_task.status if review_task and review_task.status == "changes_requested" else draft.status,
                "body": draft.body,
                "review_task_id": review_task.id if review_task else None,
                "review_status": review_task.status if review_task else None,
                "review_reason": review_task.rejection_reason if review_task else None,
                "created_at": iso_or_none(draft.created_at),
                "updated_at": iso_or_none(draft.updated_at),
            }
        )

    match_summary = json_safe(referral.match_summary or {})
    ranked = match_summary.get("ranked_matches") if isinstance(match_summary, dict) else None
    if ranked:
        first = ranked[0] or {}
        outputs.append(
            {
                "id": f"match:{referral.id}",
                "type": "match_recommendation",
                "title": f"Therapist match: {first.get('name') or first.get('therapist_id') or 'recommended therapist'}",
                "status": canonical_referral_status(referral.status),
                "body": first.get("rationale") or ", ".join(first.get("reasons") or []) or "Deterministic therapist match is available.",
                "review_task_id": _latest_task_id(tasks, "match_approval"),
                "created_at": iso_or_none(referral.updated_at),
                "updated_at": iso_or_none(referral.updated_at),
            }
        )

    appointments = list(
        session.scalars(
            select(Appointment)
            .where(Appointment.referral_id == referral.id)
            .order_by(Appointment.created_at.desc())
            .limit(4)
        )
    )
    if appointments:
        outputs.append(
            {
                "id": f"slots:{referral.id}",
                "type": "slot_options",
                "title": "Appointment slot options",
                "status": appointments[0].status,
                "body": f"{len(appointments)} appointment option{'s' if len(appointments) != 1 else ''} recorded.",
                "review_task_id": _latest_task_id(tasks, "slot_offer_approval"),
                "created_at": iso_or_none(appointments[-1].created_at),
                "updated_at": iso_or_none(appointments[0].updated_at),
            }
        )

    briefs = list(
        session.scalars(
            select(TherapistPrepBrief)
            .where(TherapistPrepBrief.referral_id == referral.id)
            .order_by(TherapistPrepBrief.created_at.desc())
            .limit(2)
        )
    )
    for brief in briefs:
        outputs.append(
            {
                "id": brief.id,
                "type": "prep_brief",
                "title": brief.title,
                "status": brief.status,
                "body": brief.body,
                "created_at": iso_or_none(brief.created_at),
                "updated_at": iso_or_none(brief.updated_at),
            }
        )
    return outputs


def _task_for_payload_prefix(tasks: list[HumanReviewTask], source_id: str) -> HumanReviewTask | None:
    short = source_id[:8]
    return next((task for task in tasks if short and short in str(task.payload_key or "")), None)


def _latest_task_id(tasks: list[HumanReviewTask], task_type: str) -> str | None:
    task = next((task for task in tasks if task.task_type == task_type), None)
    return task.id if task else None


def _draft_output_type(draft: CommunicationDraft) -> str:
    subject = f"{draft.subject or ''} {draft.channel or ''}".lower()
    if "intake" in subject:
        return "intake_message"
    if "missing" in subject:
        return "missing_info_message"
    return "patient_message"


def _referral_activity(
    session: Session,
    referral: Referral,
    *,
    tasks: list[HumanReviewTask],
    drafts: list[CommunicationDraft],
    workflows: list[WorkflowRun],
) -> list[dict[str, Any]]:
    items: list[tuple[datetime, dict[str, Any]]] = []

    def add(created_at: datetime | None, item: dict[str, Any]) -> None:
        timestamp = _normalise_datetime(created_at or utc_now())
        item["created_at"] = iso_or_none(timestamp)
        items.append((timestamp, item))

    add(
        referral.created_at,
        {
            "type": "referral",
            "status": canonical_referral_status(referral.status),
            "title": "Referral captured",
            "body": referral.source_channel or "Referral source recorded.",
        },
    )

    for workflow in workflows[:8]:
        add(
            workflow.created_at,
            {
                "type": "agent",
                "status": workflow.status,
                "title": f"Agent workflow started: {workflow.workflow_type.replace('_', ' ')}",
                "body": workflow.input_summary or workflow.id,
            },
        )
        add(
            workflow.updated_at,
            {
                "type": "agent",
                "status": workflow.status,
                "title": f"Agent workflow {workflow.status}",
                "body": workflow.workflow_type.replace("_", " "),
            },
        )

    workflow_ids = [workflow.id for workflow in workflows]
    if workflow_ids:
        for event in session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id.in_(workflow_ids))
            .order_by(WorkflowEvent.created_at.desc())
            .limit(20)
        ):
            add(
                event.created_at,
                {
                    "type": "agent",
                    "status": event.status,
                    "title": _workflow_event_title(event),
                    "body": event.message,
                },
            )

    for task in tasks[:12]:
        add(
            task.created_at,
            {
                "type": "review",
                "status": task.status,
                "title": f"Review opened: {task.task_type.replace('_', ' ')}",
                "body": task.reason,
                "meta": [task.payload_key, "opened"],
            },
        )
        if task.reviewed_at:
            decision = task.status.replace("_", " ")
            add(
                task.reviewed_at,
                {
                    "type": "review",
                    "status": task.status,
                    "title": f"Review {decision}: {task.task_type.replace('_', ' ')}",
                    "body": task.rejection_reason or task.reason,
                    "meta": [task.payload_key, decision],
                },
            )

    for draft in drafts[:8]:
        add(
            draft.created_at,
            {
                "type": "agent_output",
                "status": draft.status,
                "title": f"Draft prepared: {draft.subject or 'patient message'}",
                "body": draft.channel or "Communication draft",
            },
        )
        if draft.updated_at and draft.updated_at != draft.created_at:
            add(
                draft.updated_at,
                {
                    "type": "agent_output",
                    "status": draft.status,
                    "title": f"Draft status updated: {draft.status.replace('_', ' ')}",
                    "body": draft.subject or "Patient message",
                },
            )

    for document in _referral_documents(session, referral, "patient_reply")[:6]:
        add(
            _parse_iso(document.get("created_at")),
            {
                "type": "patient",
                "status": "patient_reply",
                "title": "Patient reply recorded",
                "body": (document.get("metadata") or {}).get("reply_type") or document.get("title"),
            },
        )
    for document in _referral_documents(session, referral, "missing_info_reply")[:6]:
        add(
            _parse_iso(document.get("created_at")),
            {
                "type": "patient",
                "status": "missing_info_reply",
                "title": "Missing information reply recorded",
                "body": (document.get("metadata") or {}).get("notes") or document.get("title"),
            },
        )

    related_ids = [referral.id, *[task.id for task in tasks], *[draft.id for draft in drafts], *workflow_ids]
    for audit in session.scalars(
        select(AuditLog)
        .where(AuditLog.tenant_id == referral.tenant_id, AuditLog.entity_id.in_(related_ids))
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    ):
        add(
            audit.created_at,
            {
                "type": "audit",
                "status": "completed",
                "title": audit.action.replace("_", " ").title(),
                "body": f"{audit.entity_type.replace('_', ' ')} updated.",
            },
        )

    items.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in items[:30]]


def _workflow_event_title(event: WorkflowEvent) -> str:
    if event.type == "human_review":
        return "Human review gate created"
    if event.agent:
        return f"{event.agent} updated"
    if event.node:
        return f"Workflow step: {event.node.replace('_', ' ')}"
    return f"Workflow {event.type.replace('_', ' ')}"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _normalise_datetime(datetime.fromisoformat(value))
    except ValueError:
        return None


def _normalise_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


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
    raw_text = _email_referral_raw_text(sender=sender, subject=subject, body=clean_body)
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


def _email_referral_raw_text(*, sender: str, subject: str, body: str) -> str:
    clean_body = str(body or "").strip()
    return "\n".join(
        part
        for part in [
            f"From: {str(sender or '').strip()}" if str(sender or "").strip() else "",
            f"Subject: {str(subject or '').strip()}" if str(subject or "").strip() else "",
            clean_body,
        ]
        if part
    )


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
    try:
        google_status = google_workspace.google_workspace_status(refresh=False)
    except Exception as exc:
        google_status = {
            "enabled": google_workspace.is_enabled(),
            "authorized": False,
            "token_present": False,
            "last_provider_error": google_workspace.provider_error_message(exc),
        }
    google_check_status = _google_integration_check_status(google_status)
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
                "name": "Google Calendar availability",
                "status": google_check_status,
                "message": _google_integration_message("Google Calendar", google_status),
                "last_seen": None,
            },
            {
                "name": "Gmail send",
                "status": google_check_status,
                "message": _google_integration_message("Gmail send", google_status),
                "last_seen": None,
            },
        ]
    }


def _google_integration_check_status(status: dict[str, Any]) -> str:
    if not status.get("enabled"):
        return "manual"
    if status.get("last_provider_error"):
        return "failed"
    if status.get("authorized"):
        return "ready"
    return "not_authorized"


def _google_integration_message(name: str, status: dict[str, Any]) -> str:
    if not status.get("enabled"):
        return f"{name} is not connected yet; Lumen is using the local/manual workflow."
    if status.get("authorized"):
        return f"{name} is connected to the configured Google Workspace account."
    if status.get("last_provider_error"):
        return f"{name} failed: {status['last_provider_error']}"
    if not status.get("token_present"):
        return f"{name} is not authorized. Run scripts/google_workspace_auth.py to create the local token."
    return f"{name} is not authorized for the configured scopes."


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


def therapist_for_user(session: Session, user: User | str | None) -> dict[str, Any] | None:
    user_record = session.get(User, user) if isinstance(user, str) else user
    if user_record is None or user_record.role != "therapist" or not user_record.active:
        return None
    email = (user_record.email or "").strip().lower()
    if not email:
        return None
    therapist = session.scalar(
        select(Therapist)
        .where(
            Therapist.tenant_id == user_record.tenant_id,
            func.lower(Therapist.email) == email,
        )
        .limit(1)
    )
    return therapist_to_dict(therapist) if therapist is not None else None


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


def list_escalation_queue(session: Session, tenant_id: str | None = None) -> list[dict[str, Any]]:
    task_query = select(HumanReviewTask).where(HumanReviewTask.status == "escalated").order_by(HumanReviewTask.updated_at.desc())
    referral_query = select(Referral).where(Referral.status == "clinical_escalation_review").order_by(Referral.updated_at.desc())
    if tenant_id:
        task_query = task_query.where(HumanReviewTask.tenant_id == tenant_id)
        referral_query = referral_query.where(Referral.tenant_id == tenant_id)

    items: list[dict[str, Any]] = []
    seen_referrals: set[str] = set()
    for task in session.scalars(task_query):
        referral = session.get(Referral, task.referral_id) if task.referral_id else None
        if referral:
            seen_referrals.add(referral.id)
        items.append(
            {
                "type": "review_task",
                "status": task.status,
                "created_at": iso_or_none(task.created_at),
                "updated_at": iso_or_none(task.updated_at),
                "task": review_task_to_dict(task),
                "referral": referral_summary(referral) if referral else None,
                "reason": task.rejection_reason or task.reason,
            }
        )

    for referral in session.scalars(referral_query):
        if referral.id in seen_referrals:
            continue
        items.append(
            {
                "type": "referral",
                "status": referral.status,
                "created_at": iso_or_none(referral.created_at),
                "updated_at": iso_or_none(referral.updated_at),
                "task": None,
                "referral": referral_summary(referral),
                "reason": "Referral is in clinical escalation review.",
            }
        )

    items.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return items


def reset_clean_demo_referral(session: Session, tenant_id: str = DEMO_TENANT_ID) -> dict[str, Any]:
    return reset_gmail_first_demo_state(session, tenant_id=tenant_id)


def reset_gmail_patient_demo(session: Session, tenant_id: str = DEMO_TENANT_ID) -> dict[str, Any]:
    return reset_gmail_first_demo_state(session, tenant_id=tenant_id)


def reset_gmail_first_demo_state(session: Session, tenant_id: str = DEMO_TENANT_ID) -> dict[str, Any]:
    seed_demo_data(session)
    therapist = _ensure_clara_demo_therapist(session, tenant_id)
    intake_assets = _ensure_gmail_demo_intake_packet_assets(session, tenant_id)
    inbox_document_ids = _gmail_inbox_document_ids(session, tenant_id)
    deleted_gmail_inbox_documents = _delete_document_rows(session, inbox_document_ids)
    clara_cleanup = _clear_clara_demo_local_calendar(session, therapist)
    cleanup = _delete_demo_patient_and_referral_state(session, tenant_id=tenant_id)
    stage_seed = _reset_demo_stage_referrals(session, tenant_id=tenant_id, template_id=intake_assets["intake_template"]["id"])
    session.flush()
    write_audit(
        session,
        tenant_id=tenant_id,
        action="demo_gmail_first_reset",
        entity_type="tenant",
        entity_id=tenant_id,
        after={
            "patient_email": DEMO_OUTBOUND_PATIENT_EMAIL,
            "deleted_gmail_inbox_documents": deleted_gmail_inbox_documents,
            **cleanup,
            **clara_cleanup,
            **stage_seed,
        },
    )
    return {
        "patient_email": DEMO_OUTBOUND_PATIENT_EMAIL,
        "deleted_clean_demo_patient": cleanup["deleted_clean_demo_patient"],
        "deleted_gmail_inbox_documents": deleted_gmail_inbox_documents,
        "deleted_gmail_demo_referrals": cleanup["deleted_gmail_demo_referrals"],
        "deleted_clara_appointments": clara_cleanup["deleted_clara_appointments"],
        "superseded_appointment_tasks": clara_cleanup["superseded_appointment_tasks"],
        "removed_referral_ids": cleanup["removed_referral_ids"],
        "removed_patient_ids": cleanup["removed_patient_ids"],
        "removed_document_ids": [*inbox_document_ids, *cleanup["removed_document_ids"]],
        "google_workspace_enabled": google_workspace.is_enabled(),
        "therapist": therapist_to_dict(therapist),
        "intake_template": intake_assets["intake_template"],
        "intake_template_files": intake_assets["intake_template_files"],
        "missing_intake_template_files": intake_assets["missing_intake_template_files"],
        "seeded_stage_referrals": stage_seed["seeded_stage_referrals"],
        "seeded_stage_referral_ids": stage_seed["seeded_stage_referral_ids"],
        "deleted_stage_referrals": stage_seed["deleted_stage_referrals"],
    }


def _ensure_clara_demo_therapist(session: Session, tenant_id: str) -> Therapist:
    therapist = session.get(Therapist, DEMO_CLEAN_THERAPIST_ID)
    if therapist is None:
        therapist = Therapist(id=DEMO_CLEAN_THERAPIST_ID, tenant_id=tenant_id)
        session.add(therapist)
    therapist.tenant_id = tenant_id
    therapist.active = True
    therapist.name = "Dr. Clara Santos"
    therapist.email = DEMO_CLARA_EMAIL
    therapist.specialties = ["anxiety", "work stress", "adjustment"]
    therapist.age_groups = ["adult"]
    therapist.languages = ["Portuguese", "English"]
    therapist.modalities = ["online"]
    therapist.insurers = ["Multicare", "self-pay"]
    therapist.capacity_per_week = 6
    therapist.availability_blocks = [
        {"weekday": "Tuesday", "start": "10:00", "end": "16:00", "modality": "online"},
        {"weekday": "Thursday", "start": "09:00", "end": "13:00", "modality": "online"},
    ]
    session.flush()
    return therapist


def _reset_demo_stage_referrals(session: Session, *, tenant_id: str, template_id: str) -> dict[str, Any]:
    cleanup = _delete_demo_stage_referral_state(session, tenant_id=tenant_id)
    therapists = _ensure_demo_stage_therapists(session, tenant_id)
    template = session.get(IntakeTemplate, template_id)
    if template is None:
        template = _ensure_gmail_demo_intake_template(session, tenant_id)

    tenant_token = _demo_stage_tenant_token(tenant_id)
    specs = _demo_stage_referral_specs(therapists, tenant_token=tenant_token)
    created_ids: list[str] = []
    for index, spec in enumerate(specs, start=1):
        patient = Patient(
            id=spec["patient_id"],
            tenant_id=tenant_id,
            display_name=spec["patient_name"],
            date_of_birth=spec["date_of_birth"],
            contact_email=spec["contact_email"],
            contact_phone=spec["contact_phone"],
            language=spec["language_preference"],
        )
        referral = Referral(
            id=spec["referral_id"],
            tenant_id=tenant_id,
            patient_id=patient.id,
            source_channel=DEMO_STAGE_SOURCE,
            raw_text=spec["raw_text"],
            status=spec["status"],
            patient_name=spec["patient_name"],
            date_of_birth=spec["date_of_birth"],
            contact_email=spec["contact_email"],
            contact_phone=spec["contact_phone"],
            insurer=spec["insurer"],
            referring_entity=spec["referring_entity"],
            language_preference=spec["language_preference"],
            modality_preference=spec["modality_preference"],
            missing_fields=spec.get("missing_fields", []),
            risk_category=spec["risk_category"],
            urgency=spec["urgency"],
            risk_present=spec["risk_present"],
            match_summary=json_safe(spec.get("match_summary") or {}),
        )
        session.add_all([patient, referral])
        session.flush()
        created_ids.append(referral.id)

        if spec["status"] == "needs_admin_review":
            _seed_demo_stage_review_task(session, referral, tenant_token)
        if spec["status"] == "match_recommended":
            _seed_demo_stage_match_task(session, referral, tenant_token)
        if spec["status"] == "awaiting_patient_contact":
            _seed_demo_stage_contact_draft(session, referral, patient, tenant_token)
        if spec["status"] == "awaiting_patient_reply":
            _seed_demo_stage_sent_contact(session, referral, patient, spec["therapist_id"], index, tenant_token)
        if spec["status"] == "intake_incomplete":
            _seed_demo_stage_incomplete_intake(session, referral, patient, template, index, tenant_token)
        if spec["status"] == "first_session_ready":
            _seed_demo_stage_ready_referral(session, referral, patient, template, spec["therapist_id"], index, tenant_token)

    session.flush()
    return {
        **cleanup,
        "seeded_stage_referrals": len(created_ids),
        "seeded_stage_referral_ids": created_ids,
    }


def _delete_demo_stage_referral_state(session: Session, *, tenant_id: str) -> dict[str, Any]:
    referral_ids = list(
        session.scalars(
            select(Referral.id).where(
                Referral.tenant_id == tenant_id,
                Referral.id.like(f"{DEMO_STAGE_PREFIX}%"),
            )
        )
    )
    patient_ids = set(
        session.scalars(
            select(Patient.id).where(
                Patient.tenant_id == tenant_id,
                Patient.id.like(f"{DEMO_STAGE_PREFIX}%"),
            )
        )
    )
    if referral_ids:
        patient_ids.update(
            referral.patient_id
            for referral in session.scalars(select(Referral).where(Referral.id.in_(referral_ids)))
            if referral.patient_id
        )
    workflow_ids = list(
        session.scalars(
            select(WorkflowRun.id).where(
                WorkflowRun.tenant_id == tenant_id,
                WorkflowRun.id.like(f"{DEMO_STAGE_PREFIX}%"),
            )
        )
    )
    document_ids = []
    for document in session.scalars(select(Document).where(Document.tenant_id == tenant_id)):
        metadata = document.metadata_json or {}
        if (
            str(document.id or "").startswith(DEMO_STAGE_PREFIX)
            or document.patient_id in patient_ids
            or str(metadata.get("source") or "") == DEMO_STAGE_SEED_SOURCE
            or str(metadata.get("referral_id") or "") in referral_ids
        ):
            document_ids.append(document.id)

    if workflow_ids:
        session.execute(delete(WorkflowEvent).where(WorkflowEvent.workflow_run_id.in_(workflow_ids)))
    if referral_ids:
        session.execute(delete(DraftFeedback).where(DraftFeedback.referral_id.in_(referral_ids)))
        session.execute(delete(HumanReviewTask).where(HumanReviewTask.referral_id.in_(referral_ids)))
        session.execute(delete(CommunicationDraft).where(CommunicationDraft.referral_id.in_(referral_ids)))
        session.execute(delete(Appointment).where(Appointment.referral_id.in_(referral_ids)))
        session.execute(delete(IntakeChecklistItem).where(IntakeChecklistItem.referral_id.in_(referral_ids)))
        session.execute(delete(ScoreRecord).where(ScoreRecord.referral_id.in_(referral_ids)))
        session.execute(delete(QuestionnaireResponse).where(QuestionnaireResponse.referral_id.in_(referral_ids)))
        session.execute(delete(TherapistPrepBrief).where(TherapistPrepBrief.referral_id.in_(referral_ids)))
        session.execute(delete(SessionNote).where(SessionNote.referral_id.in_(referral_ids)))
        session.execute(delete(ReportDraft).where(ReportDraft.referral_id.in_(referral_ids)))
    if patient_ids:
        ids = list(patient_ids)
        session.execute(delete(DocumentChunk).where(DocumentChunk.patient_id.in_(ids)))
        session.execute(delete(DraftFeedback).where(DraftFeedback.patient_id.in_(ids)))
        session.execute(delete(HumanReviewTask).where(HumanReviewTask.patient_id.in_(ids)))
        session.execute(delete(CommunicationDraft).where(CommunicationDraft.patient_id.in_(ids)))
        session.execute(delete(Appointment).where(Appointment.patient_id.in_(ids)))
        session.execute(delete(IntakeChecklistItem).where(IntakeChecklistItem.patient_id.in_(ids)))
        session.execute(delete(ScoreRecord).where(ScoreRecord.patient_id.in_(ids)))
        session.execute(delete(QuestionnaireResponse).where(QuestionnaireResponse.patient_id.in_(ids)))
        session.execute(delete(TherapistPrepBrief).where(TherapistPrepBrief.patient_id.in_(ids)))
        session.execute(delete(SessionNote).where(SessionNote.patient_id.in_(ids)))
        session.execute(delete(ReportDraft).where(ReportDraft.patient_id.in_(ids)))
        session.execute(delete(ConsentRecord).where(ConsentRecord.patient_id.in_(ids)))
        session.execute(delete(DocumentationProgressOverview).where(DocumentationProgressOverview.patient_id.in_(ids)))
    session.execute(
        delete(HumanReviewTask).where(
            HumanReviewTask.tenant_id == tenant_id,
            HumanReviewTask.id.like(f"{DEMO_STAGE_PREFIX}%"),
        )
    )
    removed_documents = _delete_document_rows(session, document_ids)
    if workflow_ids:
        session.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(workflow_ids)))
    if referral_ids:
        session.execute(delete(Referral).where(Referral.id.in_(referral_ids)))
    if patient_ids:
        session.execute(delete(Patient).where(Patient.id.in_(list(patient_ids))))
    session.flush()
    return {
        "deleted_stage_referrals": len(referral_ids),
        "deleted_stage_patients": len(patient_ids),
        "deleted_stage_documents": removed_documents,
    }


def _ensure_demo_stage_therapists(session: Session, tenant_id: str) -> dict[str, Therapist]:
    specs = [
        {
            "id": "demo-therapist-001",
            "name": "Dr. Sofia Almeida",
            "email": "sofia.almeida@lumen-clinic.local",
            "specialties": ["anxiety", "adjustment", "work stress"],
            "age_groups": ["adult", "older_adult"],
            "languages": ["Portuguese", "English"],
            "modalities": ["online", "hybrid"],
            "insurers": ["Multicare", "AdvanceCare", "self-pay"],
            "capacity_per_week": 6,
            "availability_blocks": [
                {"weekday": "Tuesday", "start": "10:00", "end": "13:00", "modality": "online"},
                {"weekday": "Thursday", "start": "14:00", "end": "18:00", "modality": "hybrid"},
            ],
        },
        {
            "id": "demo-therapist-002",
            "name": "Miguel Costa",
            "email": "miguel.costa@lumen-clinic.local",
            "specialties": ["adolescent mental health", "family transitions", "school stress"],
            "age_groups": ["adolescent", "adult"],
            "languages": ["Portuguese", "Spanish"],
            "modalities": ["in_person", "hybrid"],
            "insurers": ["Medis", "self-pay"],
            "capacity_per_week": 4,
            "availability_blocks": [
                {"weekday": "Monday", "start": "15:00", "end": "19:00", "modality": "in_person"},
                {"weekday": "Wednesday", "start": "09:00", "end": "12:00", "modality": "hybrid"},
            ],
        },
    ]
    therapists: dict[str, Therapist] = {}
    for spec in specs:
        therapist = session.get(Therapist, spec["id"])
        if therapist is None:
            therapist = Therapist(id=spec["id"], tenant_id=tenant_id)
            session.add(therapist)
        therapist.tenant_id = tenant_id
        therapist.active = True
        therapist.name = spec["name"]
        therapist.email = spec["email"]
        therapist.specialties = spec["specialties"]
        therapist.age_groups = spec["age_groups"]
        therapist.languages = spec["languages"]
        therapist.modalities = spec["modalities"]
        therapist.insurers = spec["insurers"]
        therapist.capacity_per_week = spec["capacity_per_week"]
        therapist.availability_blocks = spec["availability_blocks"]
        therapists[spec["id"]] = therapist
    session.flush()
    return therapists


def _demo_stage_tenant_token(tenant_id: str) -> str:
    if tenant_id == DEMO_TENANT_ID:
        return "demo"
    return hashlib.sha1(str(tenant_id).encode("utf-8")).hexdigest()[:6]


def _demo_stage_referral_specs(therapists: dict[str, Therapist], *, tenant_token: str) -> list[dict[str, Any]]:
    sofia = therapists["demo-therapist-001"]
    miguel = therapists["demo-therapist-002"]
    return [
        _demo_stage_referral_spec(
            "needs-review",
            "Marta Silva",
            "needs_admin_review",
            "Referrer sent a partial workplace anxiety referral missing insurer confirmation.",
            tenant_token=tenant_token,
            missing_fields=["insurer"],
            therapist=sofia,
            insurer=None,
        ),
        _demo_stage_referral_spec(
            "ready-match",
            "Rui Pereira",
            "ready_for_matching",
            "Adult referral with complete details ready for deterministic matching.",
            tenant_token=tenant_token,
            therapist=sofia,
        ),
        _demo_stage_referral_spec(
            "match-rec",
            "Helena Duarte",
            "match_recommended",
            "Referral has a recommended therapist and awaits admin match approval.",
            tenant_token=tenant_token,
            therapist=sofia,
            match=True,
        ),
        _demo_stage_referral_spec(
            "await-contact",
            "Tiago Rocha",
            "awaiting_patient_contact",
            "Match approved and first-contact email is drafted for admin approval.",
            tenant_token=tenant_token,
            therapist=miguel,
            match=True,
        ),
        _demo_stage_referral_spec(
            "await-reply",
            "Ana Ferreira",
            "awaiting_patient_reply",
            "Patient has been sent appointment options and the clinic is waiting for a reply.",
            tenant_token=tenant_token,
            therapist=miguel,
            match=True,
        ),
        _demo_stage_referral_spec(
            "intake-open",
            "Bruno Nunes",
            "intake_incomplete",
            "Appointment is confirmed, but intake paperwork is still incomplete.",
            tenant_token=tenant_token,
            therapist=sofia,
            match=True,
        ),
        _demo_stage_referral_spec(
            "ready-one",
            "Carla Mendes",
            "first_session_ready",
            "Appointment, intake, questionnaires, and prep brief are complete.",
            tenant_token=tenant_token,
            therapist=sofia,
            match=True,
        ),
        _demo_stage_referral_spec(
            "ready-two",
            "Joao Ribeiro",
            "first_session_ready",
            "Second ready referral with completed patient files and confirmed appointment.",
            tenant_token=tenant_token,
            therapist=miguel,
            match=True,
            insurer="self-pay",
            modality="hybrid",
        ),
    ]


def _demo_stage_referral_spec(
    key: str,
    patient_name: str,
    status: str,
    raw_text: str,
    *,
    tenant_token: str,
    therapist: Therapist,
    missing_fields: list[str] | None = None,
    insurer: str | None = "Multicare",
    modality: str = "online",
    match: bool = False,
) -> dict[str, Any]:
    slug = key.replace("_", "-")
    first_name = patient_name.split()[0].lower()
    match_summary = {}
    if match:
        match_summary = {
            "ranked_matches": [
                {
                    "therapist_id": therapist.id,
                    "name": therapist.name,
                    "score": 91,
                    "rationale": "Matches language, modality, insurer, and presenting concern.",
                }
            ],
            "excluded_therapists": [],
        }
    return {
        "referral_id": f"{DEMO_STAGE_PREFIX}{tenant_token}-{slug}",
        "patient_id": f"{DEMO_STAGE_PREFIX}{tenant_token}-p-{slug[:10]}",
        "patient_name": patient_name,
        "date_of_birth": "1990-04-12",
        "contact_email": f"{first_name}.{tenant_token}.{slug}@demo-stage.local",
        "contact_phone": "+351 910 000 100",
        "insurer": insurer,
        "referring_entity": "Demo staging referrer",
        "language_preference": "Portuguese",
        "modality_preference": modality,
        "missing_fields": missing_fields or [],
        "risk_category": "low",
        "urgency": "routine",
        "risk_present": False,
        "raw_text": raw_text,
        "status": status,
        "therapist_id": therapist.id,
        "match_summary": match_summary,
    }


def _seed_demo_stage_review_task(session: Session, referral: Referral, tenant_token: str) -> None:
    session.add(
        HumanReviewTask(
            id=f"{DEMO_STAGE_PREFIX}{tenant_token}-task-review",
            tenant_id=referral.tenant_id,
            referral_id=referral.id,
            patient_id=referral.patient_id,
            task_type="admin_missing_info_review",
            status="open",
            reason="Demo staging referral is missing insurer details.",
            payload_key="demo_stage_missing_info",
            source_payload={"source": DEMO_STAGE_SEED_SOURCE, "referral_id": referral.id},
        )
    )


def _seed_demo_stage_match_task(session: Session, referral: Referral, tenant_token: str) -> None:
    session.add(
        HumanReviewTask(
            id=f"{DEMO_STAGE_PREFIX}{tenant_token}-task-match",
            tenant_id=referral.tenant_id,
            referral_id=referral.id,
            patient_id=referral.patient_id,
            task_type="match_approval",
            status="open",
            reason="Demo staging match recommendation is ready for admin approval.",
            payload_key="demo_stage_match",
            source_payload={"source": DEMO_STAGE_SEED_SOURCE, "referral_id": referral.id},
        )
    )


def _seed_demo_stage_contact_draft(
    session: Session,
    referral: Referral,
    patient: Patient,
    tenant_token: str,
) -> None:
    draft = CommunicationDraft(
        id=f"{DEMO_STAGE_PREFIX}{tenant_token}-draft-contact",
        tenant_id=referral.tenant_id,
        referral_id=referral.id,
        patient_id=patient.id,
        channel="email",
        subject="Appointment options for your referral",
        body="Hello, we have a therapist match and can offer appointment options once this message is approved.",
        status="draft_pending_review",
        proposed_slots=[],
        requires_human_send=True,
        recipient_email=patient.contact_email,
    )
    session.add(draft)
    referral.communication_draft_id = draft.id
    session.add(
        HumanReviewTask(
            id=f"{DEMO_STAGE_PREFIX}{tenant_token}-task-contact",
            tenant_id=referral.tenant_id,
            referral_id=referral.id,
            patient_id=patient.id,
            task_type="send_approval",
            status="open",
            reason="Demo staging patient-contact draft requires approval.",
            payload_key=f"first_contact_draft:{draft.id[:8]}",
            source_payload=communication_draft_to_dict(draft),
            draft_text=draft.body,
        )
    )


def _seed_demo_stage_sent_contact(
    session: Session,
    referral: Referral,
    patient: Patient,
    therapist_id: str,
    index: int,
    tenant_token: str,
) -> None:
    starts_at = _demo_stage_future_time(index + 14, 15)
    appointment = Appointment(
        id=f"{DEMO_STAGE_PREFIX}{tenant_token}-appt-prop",
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        therapist_id=therapist_id,
        referral_id=referral.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=SESSION_LENGTH_MINUTES),
        status="proposed",
        source=DEMO_STAGE_SEED_SOURCE,
    )
    draft = CommunicationDraft(
        id=f"{DEMO_STAGE_PREFIX}{tenant_token}-draft-sent",
        tenant_id=referral.tenant_id,
        referral_id=referral.id,
        patient_id=patient.id,
        channel="email",
        subject="Please confirm one appointment option",
        body="Hello, please reply with the appointment option that works best for you.",
        status="sent",
        proposed_slots=[appointment.id],
        requires_human_send=True,
        recipient_email=patient.contact_email,
        sent_at=utc_now() - timedelta(days=1),
    )
    session.add_all([appointment, draft])
    referral.communication_draft_id = draft.id


def _seed_demo_stage_incomplete_intake(
    session: Session,
    referral: Referral,
    patient: Patient,
    template: IntakeTemplate,
    index: int,
    tenant_token: str,
) -> None:
    starts_at = _demo_stage_future_time(index + 20, 10)
    session.add(
        Appointment(
            id=f"{DEMO_STAGE_PREFIX}{tenant_token}-appt-intake",
            tenant_id=referral.tenant_id,
            patient_id=patient.id,
            therapist_id=_top_match_therapist_id(referral),
            referral_id=referral.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=SESSION_LENGTH_MINUTES),
            status="confirmed",
            source=DEMO_STAGE_SEED_SOURCE,
            google_calendar_id="demo_seed_local",
            google_calendar_event_id=f"{DEMO_STAGE_PREFIX}{tenant_token}-event-intake",
            google_calendar_synced_at=utc_now(),
        )
    )
    for spec in _intake_template_required_item_specs(template):
        key = _intake_template_item_key(spec)
        if not key:
            continue
        session.add(
            IntakeChecklistItem(
                id=f"{DEMO_STAGE_PREFIX}{tenant_token}-item-6-{_demo_stage_item_alias(key)}",
                tenant_id=referral.tenant_id,
                patient_id=patient.id,
                referral_id=referral.id,
                template_id=template.id,
                item_key=key,
                label=str(spec.get("label") or key.replace("_", " ").title()),
                item_type=str(spec.get("type") or "form"),
                status="missing",
                due_at=utc_now() + timedelta(days=int(spec.get("due_days") or 5)),
            )
        )
        if str(spec.get("type") or "form") == "consent":
            session.add(
                ConsentRecord(
                    id=f"{DEMO_STAGE_PREFIX}{tenant_token}-cons-6-{_demo_stage_item_alias(key)}",
                    tenant_id=referral.tenant_id,
                    patient_id=patient.id,
                    scope=str(spec.get("consent_scope") or key),
                    status="missing",
                )
            )


def _seed_demo_stage_ready_referral(
    session: Session,
    referral: Referral,
    patient: Patient,
    template: IntakeTemplate,
    therapist_id: str,
    index: int,
    tenant_token: str,
) -> None:
    document_by_key: dict[str, Document] = {}
    for spec in _intake_template_required_item_specs(template):
        key = _intake_template_item_key(spec)
        if not key:
            continue
        alias = _demo_stage_item_alias(key)
        document = _create_demo_stage_patient_document(session, referral, patient, spec, index, alias, tenant_token)
        document_by_key[key] = document
        session.add(
            IntakeChecklistItem(
                id=f"{DEMO_STAGE_PREFIX}{tenant_token}-item-{index}-{alias}",
                tenant_id=referral.tenant_id,
                patient_id=patient.id,
                referral_id=referral.id,
                template_id=template.id,
                item_key=key,
                label=str(spec.get("label") or key.replace("_", " ").title()),
                item_type=str(spec.get("type") or "form"),
                status="completed",
                due_at=utc_now() - timedelta(days=2),
                completed_at=utc_now() - timedelta(days=1),
                source_document_id=document.id,
                notes="Returned by seeded demo patient.",
            )
        )
        if str(spec.get("type") or "form") == "consent":
            session.add(
                ConsentRecord(
                    id=f"{DEMO_STAGE_PREFIX}{tenant_token}-cons-{index}-{alias}",
                    tenant_id=referral.tenant_id,
                    patient_id=patient.id,
                    scope=str(spec.get("consent_scope") or key),
                    status="completed",
                    source_document_id=document.id,
                )
            )
    questionnaire_document = document_by_key.get("screening_questionnaire")
    response = QuestionnaireResponse(
        id=f"{DEMO_STAGE_PREFIX}{tenant_token}-q-{index}",
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        referral_id=referral.id,
        template_id=template.id,
        questionnaire_name="generic_screening",
        answers={"mood": 1, "anxiety": 2, "sleep": 1},
        score_summary={"total_score": 4, "answered_items": 3, "numeric_items": 3},
        status="completed",
    )
    session.add(response)
    session.flush()
    if questionnaire_document is not None:
        questionnaire_document.metadata_json = json_safe(
            {
                **(questionnaire_document.metadata_json or {}),
                "questionnaire_response_id": response.id,
            }
        )
    session.add(
        ScoreRecord(
            id=f"{DEMO_STAGE_PREFIX}{tenant_token}-score-{index}",
            tenant_id=referral.tenant_id,
            patient_id=patient.id,
            referral_id=referral.id,
            source_response_id=response.id,
            instrument_name=response.questionnaire_name,
            score_summary=response.score_summary,
            status="recorded",
        )
    )
    starts_at = _demo_stage_future_time(index + 24, 11)
    appointment = Appointment(
        id=f"{DEMO_STAGE_PREFIX}{tenant_token}-appt-{index}",
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        therapist_id=therapist_id,
        referral_id=referral.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=SESSION_LENGTH_MINUTES),
        status="confirmed",
        source=DEMO_STAGE_SEED_SOURCE,
        google_calendar_id="demo_seed_local",
        google_calendar_event_id=f"{DEMO_STAGE_PREFIX}{tenant_token}-event-{index}",
        google_calendar_synced_at=utc_now(),
    )
    brief = TherapistPrepBrief(
        id=f"{DEMO_STAGE_PREFIX}{tenant_token}-brief-{index}",
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        referral_id=referral.id,
        therapist_id=therapist_id,
        title=f"Prep brief for {patient.display_name}",
        body=(
            f"{patient.display_name} is ready for a first session. Intake files, consent records, "
            "screening scores, and appointment confirmation are complete."
        ),
        source_summary={
            "source": DEMO_STAGE_SEED_SOURCE,
            "completed_intake_count": len(document_by_key),
            "questionnaire_count": 1,
            "appointment_count": 1,
        },
        status="ready",
    )
    session.add_all([appointment, brief])


def _create_demo_stage_patient_document(
    session: Session,
    referral: Referral,
    patient: Patient,
    spec: dict[str, Any],
    index: int,
    alias: str,
    tenant_token: str,
) -> Document:
    label = str(spec.get("label") or alias.replace("_", " ").title())
    file_name = f"{tenant_token}_{patient.display_name.replace(' ', '_')}_{alias}.txt"
    storage_uri, size_bytes, checksum = _write_demo_stage_patient_file(file_name, patient.display_name or "Patient", label)
    document = Document(
        id=f"{DEMO_STAGE_PREFIX}{tenant_token}-doc-{index}-{alias}",
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        document_type="intake_submission",
        title=file_name,
        storage_uri=storage_uri,
        metadata_json={
            "source": DEMO_STAGE_SEED_SOURCE,
            "referral_id": referral.id,
            "patient_id": patient.id,
            "file_name": file_name,
            "content_type": "text/plain",
            "mime_type": "text/plain",
            "size_bytes": size_bytes,
            "sha256": checksum,
            "item_key": _intake_template_item_key(spec),
            "item_label": label,
            "item_type": str(spec.get("type") or "form"),
        },
    )
    session.add(document)
    session.flush()
    return document


def _write_demo_stage_patient_file(file_name: str, patient_name: str, label: str) -> tuple[str, int, str]:
    storage_dir = REPO_ROOT / "storage" / "uploads" / "intake" / "demo-stage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", file_name).strip("-") or "patient-file.txt"
    path = storage_dir / safe_name
    content = (
        f"Demo returned patient file\n"
        f"Patient: {patient_name}\n"
        f"File: {label}\n"
        f"Source: {DEMO_STAGE_SEED_SOURCE}\n"
    ).encode("utf-8")
    path.write_bytes(content)
    return (
        str(path.resolve().relative_to(REPO_ROOT.resolve())),
        len(content),
        hashlib.sha256(content).hexdigest(),
    )


def _demo_stage_item_alias(key: str) -> str:
    aliases = {
        "privacy_notice": "privacy",
        "telehealth_consent": "telehealth",
        "intake_form": "form",
        "screening_questionnaire": "screen",
    }
    return aliases.get(key, re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:12] or "file")


def _demo_stage_future_time(days_from_now: int, hour: int) -> datetime:
    base = utc_now() + timedelta(days=days_from_now)
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


def _gmail_inbox_document_ids(session: Session, tenant_id: str) -> list[str]:
    return list(
        session.scalars(
            select(Document.id).where(
                Document.tenant_id == tenant_id,
                Document.storage_uri.like(f"{INBOUND_GMAIL_STORAGE_PREFIX}%"),
            )
        )
    )


def _delete_document_rows(session: Session, document_ids: list[str]) -> int:
    ids = list(dict.fromkeys(document_ids))
    if not ids:
        return 0
    session.execute(
        update(ConsentRecord)
        .where(ConsentRecord.source_document_id.in_(ids))
        .values(source_document_id=None)
    )
    session.execute(
        update(IntakeChecklistItem)
        .where(IntakeChecklistItem.source_document_id.in_(ids))
        .values(source_document_id=None)
    )
    session.execute(
        update(SessionNote)
        .where(SessionNote.source_document_id.in_(ids))
        .values(source_document_id=None)
    )
    session.execute(
        update(ClinicalLibraryRecord)
        .where(ClinicalLibraryRecord.source_document_id.in_(ids))
        .values(source_document_id=None)
    )
    session.execute(
        update(ReferralImportBatch)
        .where(ReferralImportBatch.source_document_id.in_(ids))
        .values(source_document_id=None)
    )
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(ids)))
    session.execute(delete(DocumentChunk).where(DocumentChunk.source_id.in_(ids)))
    session.execute(delete(Document).where(Document.id.in_(ids)))
    return len(ids)


def _delete_documentation_session_rows(session: Session, session_ids: list[str]) -> int:
    ids = list(dict.fromkeys(session_ids))
    if not ids:
        return 0
    text_ids = list(
        session.scalars(
            select(DocumentationSessionText.id).where(DocumentationSessionText.documentation_session_id.in_(ids))
        )
    )
    session.execute(delete(DocumentationSessionNote).where(DocumentationSessionNote.documentation_session_id.in_(ids)))
    if text_ids:
        session.execute(delete(DocumentationSessionNote).where(DocumentationSessionNote.source_text_id.in_(text_ids)))
    session.execute(delete(DocumentationSessionText).where(DocumentationSessionText.documentation_session_id.in_(ids)))
    session.execute(delete(DocumentationSession).where(DocumentationSession.id.in_(ids)))
    return len(ids)


def _task_references_appointment(task: HumanReviewTask, appointment_ids: set[str]) -> bool:
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    candidates = {str(payload.get("appointment_id") or "").strip()}
    candidates.update(_candidate_appointment_ids_for_task(task))
    return bool(appointment_ids & {candidate for candidate in candidates if candidate})


def _clear_clara_demo_local_calendar(session: Session, therapist: Therapist) -> dict[str, int]:
    appointments = list(
        session.scalars(
            select(Appointment).where(
                Appointment.therapist_id == therapist.id,
            )
        )
    )
    appointment_ids = {appointment.id for appointment in appointments}
    if not appointment_ids:
        return {"deleted_clara_appointments": 0, "superseded_appointment_tasks": 0}

    superseded_tasks = 0
    for task in session.scalars(
        select(HumanReviewTask).where(
            HumanReviewTask.tenant_id == therapist.tenant_id,
            HumanReviewTask.status == "open",
            HumanReviewTask.task_type.in_(["appointment_confirmation_approval", "appointment_reschedule_approval"]),
        )
    ):
        if not _task_references_appointment(task, appointment_ids):
            continue
        before = review_task_to_dict(task)
        task.status = "superseded"
        task.rejection_reason = "Dr. Clara Santos's local calendar was reset for workflow testing."
        task.reviewed_at = utc_now()
        task.updated_at = utc_now()
        superseded_tasks += 1
        write_audit(
            session,
            tenant_id=task.tenant_id,
            actor_user_id=task.reviewer_id,
            action="review_superseded",
            entity_type="human_review_task",
            entity_id=task.id,
            before=before,
            after=review_task_to_dict(task),
        )

    for draft in session.scalars(
        select(CommunicationDraft).where(
            CommunicationDraft.tenant_id == therapist.tenant_id,
            CommunicationDraft.proposed_slots.is_not(None),
        )
    ):
        proposed_slots = [slot for slot in draft.proposed_slots or [] if slot not in appointment_ids]
        if proposed_slots != list(draft.proposed_slots or []):
            draft.proposed_slots = proposed_slots
            draft.updated_at = utc_now()

    session.execute(
        update(DocumentationSession)
        .where(DocumentationSession.appointment_id.in_(list(appointment_ids)))
        .values(appointment_id=None)
    )
    session.execute(
        update(SessionNote)
        .where(SessionNote.appointment_id.in_(list(appointment_ids)))
        .values(appointment_id=None)
    )
    session.execute(delete(Appointment).where(Appointment.id.in_(list(appointment_ids))))
    return {
        "deleted_clara_appointments": len(appointment_ids),
        "superseded_appointment_tasks": superseded_tasks,
    }


def _delete_demo_patient_and_referral_state(session: Session, *, tenant_id: str) -> dict[str, Any]:
    clean_patient = session.get(Patient, DEMO_CLEAN_PATIENT_ID)
    if clean_patient is not None and clean_patient.tenant_id != tenant_id:
        clean_patient = None
    gmail_patients = list(
        session.scalars(
            select(Patient).where(
                Patient.tenant_id == tenant_id,
                func.lower(Patient.contact_email) == DEMO_OUTBOUND_PATIENT_EMAIL,
            )
        )
    )
    patient_ids = {
        *(patient.id for patient in gmail_patients),
        *([DEMO_CLEAN_PATIENT_ID] if clean_patient is not None else []),
    }
    clean_referrals = list(
        session.scalars(
            select(Referral).where(
                Referral.tenant_id == tenant_id,
                (Referral.id == DEMO_CLEAN_REFERRAL_ID)
                | (Referral.patient_id == DEMO_CLEAN_PATIENT_ID)
                | (func.lower(Referral.contact_email) == DEMO_CLEAN_PATIENT_EMAIL),
            )
        )
    )
    gmail_referrals = list(
        session.scalars(
            select(Referral).where(
                Referral.tenant_id == tenant_id,
                Referral.source_channel == "email",
                func.lower(Referral.contact_email) == DEMO_OUTBOUND_PATIENT_EMAIL,
            )
        )
    )
    referral_ids = {
        *(referral.id for referral in clean_referrals),
        *(referral.id for referral in gmail_referrals),
    }
    patient_ids.update(referral.patient_id for referral in [*clean_referrals, *gmail_referrals] if referral.patient_id)

    workflow_ids: list[str] = []
    if referral_ids or patient_ids:
        workflow_query = select(WorkflowRun.id)
        filters = []
        if referral_ids:
            filters.append(WorkflowRun.referral_id.in_(list(referral_ids)))
        if patient_ids:
            filters.append(WorkflowRun.patient_id.in_(list(patient_ids)))
        workflow_ids = [
            item
            for item in session.scalars(workflow_query.where(filters[0] if len(filters) == 1 else filters[0] | filters[1]))
        ] if filters else []

    document_ids = [
        document.id
        for document in session.scalars(select(Document).where(Document.tenant_id == tenant_id))
        if (
            document.patient_id in patient_ids
            or str((document.metadata_json or {}).get("referral_id") or "") in referral_ids
        )
    ]
    documentation_session_ids: list[str] = []
    if referral_ids or patient_ids:
        docs_query = select(DocumentationSession.id).where(DocumentationSession.tenant_id == tenant_id)
        filters = []
        if referral_ids:
            filters.append(DocumentationSession.referral_id.in_(list(referral_ids)))
        if patient_ids:
            filters.append(DocumentationSession.patient_id.in_(list(patient_ids)))
        documentation_session_ids = [
            item
            for item in session.scalars(docs_query.where(filters[0] if len(filters) == 1 else filters[0] | filters[1]))
        ] if filters else []
    report_draft_ids: list[str] = []
    if referral_ids or patient_ids:
        report_query = select(ReportDraft.id).where(ReportDraft.tenant_id == tenant_id)
        filters = []
        if referral_ids:
            filters.append(ReportDraft.referral_id.in_(list(referral_ids)))
        if patient_ids:
            filters.append(ReportDraft.patient_id.in_(list(patient_ids)))
        report_draft_ids = [
            item
            for item in session.scalars(report_query.where(filters[0] if len(filters) == 1 else filters[0] | filters[1]))
        ] if filters else []

    _delete_documentation_session_rows(session, documentation_session_ids)
    if workflow_ids:
        session.execute(delete(WorkflowEvent).where(WorkflowEvent.workflow_run_id.in_(workflow_ids)))
    if report_draft_ids:
        session.execute(delete(DraftFeedback).where(DraftFeedback.report_draft_id.in_(report_draft_ids)))

    if referral_ids:
        ids = list(referral_ids)
        session.execute(delete(DraftFeedback).where(DraftFeedback.referral_id.in_(ids)))
        session.execute(delete(HumanReviewTask).where(HumanReviewTask.referral_id.in_(ids)))
        session.execute(delete(CommunicationDraft).where(CommunicationDraft.referral_id.in_(ids)))
        session.execute(delete(Appointment).where(Appointment.referral_id.in_(ids)))
        session.execute(delete(IntakeChecklistItem).where(IntakeChecklistItem.referral_id.in_(ids)))
        session.execute(delete(ScoreRecord).where(ScoreRecord.referral_id.in_(ids)))
        session.execute(delete(QuestionnaireResponse).where(QuestionnaireResponse.referral_id.in_(ids)))
        session.execute(delete(TherapistPrepBrief).where(TherapistPrepBrief.referral_id.in_(ids)))
        session.execute(delete(SessionNote).where(SessionNote.referral_id.in_(ids)))
        session.execute(delete(ReportDraft).where(ReportDraft.referral_id.in_(ids)))
    if patient_ids:
        ids = list(patient_ids)
        session.execute(delete(DocumentChunk).where(DocumentChunk.patient_id.in_(ids)))
        session.execute(delete(DraftFeedback).where(DraftFeedback.patient_id.in_(ids)))
        session.execute(delete(HumanReviewTask).where(HumanReviewTask.patient_id.in_(ids)))
        session.execute(delete(CommunicationDraft).where(CommunicationDraft.patient_id.in_(ids)))
        session.execute(delete(Appointment).where(Appointment.patient_id.in_(ids)))
        session.execute(delete(IntakeChecklistItem).where(IntakeChecklistItem.patient_id.in_(ids)))
        session.execute(delete(ScoreRecord).where(ScoreRecord.patient_id.in_(ids)))
        session.execute(delete(QuestionnaireResponse).where(QuestionnaireResponse.patient_id.in_(ids)))
        session.execute(delete(TherapistPrepBrief).where(TherapistPrepBrief.patient_id.in_(ids)))
        session.execute(delete(SessionNote).where(SessionNote.patient_id.in_(ids)))
        session.execute(delete(ReportDraft).where(ReportDraft.patient_id.in_(ids)))
        session.execute(delete(ConsentRecord).where(ConsentRecord.patient_id.in_(ids)))
    removed_documents = _delete_document_rows(session, document_ids)
    if workflow_ids:
        session.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(workflow_ids)))
    if referral_ids:
        session.execute(delete(Referral).where(Referral.id.in_(list(referral_ids))))
    if patient_ids:
        session.execute(delete(Patient).where(Patient.id.in_(list(patient_ids))))

    return {
        "deleted_clean_demo_patient": 1 if clean_patient is not None else 0,
        "deleted_gmail_demo_referrals": len({referral.id for referral in gmail_referrals}),
        "removed_referral_ids": list(referral_ids),
        "removed_patient_ids": list(patient_ids),
        "removed_document_ids": document_ids,
        "deleted_demo_documents": removed_documents,
    }


def _ensure_gmail_demo_intake_packet_assets(session: Session, tenant_id: str) -> dict[str, Any]:
    ensure_tenant(session, tenant_id)
    template = _ensure_gmail_demo_intake_template(session, tenant_id)
    asset_missing: list[dict[str, Any]] = []
    for spec in DEMO_GMAIL_INTAKE_REQUIRED_ITEMS:
        file_name = str(spec.get("demo_asset_file") or "").strip()
        asset_path = DEMO_GMAIL_INTAKE_PACKET_ASSET_DIR / file_name
        if not file_name or not asset_path.exists() or not asset_path.is_file():
            asset_missing.append(_missing_demo_intake_asset_entry(template, spec, asset_path))
            continue
        _ensure_demo_intake_template_file(session, template, spec, asset_path)
    attachment_state = _intake_template_attachment_state(session, template)
    missing_by_key = {
        str(item.get("item_key") or ""): item
        for item in attachment_state["missing_template_files"]
    }
    for item in asset_missing:
        missing_by_key[str(item.get("item_key") or "")] = item
    return {
        "intake_template": intake_template_to_dict(template, attachment_state=attachment_state),
        "intake_template_files": attachment_state["template_files"],
        "missing_intake_template_files": list(missing_by_key.values()),
    }


def _ensure_gmail_demo_intake_template(session: Session, tenant_id: str) -> IntakeTemplate:
    template = None
    if tenant_id == DEMO_TENANT_ID:
        template = session.get(IntakeTemplate, "demo-intake-standard")
    if template is None or template.tenant_id != tenant_id:
        template = session.scalar(
            select(IntakeTemplate).where(
                IntakeTemplate.tenant_id == tenant_id,
                IntakeTemplate.name == DEMO_GMAIL_INTAKE_TEMPLATE_NAME,
            )
        )
    if template is None:
        template = IntakeTemplate(
            tenant_id=tenant_id,
            name=DEMO_GMAIL_INTAKE_TEMPLATE_NAME,
            patient_type="standard",
        )
        session.add(template)
        session.flush()
    template.patient_type = "standard"
    template.source_channel = "email"
    template.required_items = [
        {key: value for key, value in spec.items() if key != "demo_asset_file"}
        for spec in DEMO_GMAIL_INTAKE_REQUIRED_ITEMS
    ]
    template.questionnaire_schema = {
        "name": "generic_screening",
        "questions": [
            {"key": "mood", "label": "Mood difficulty", "type": "number", "min": 0, "max": 3},
            {"key": "anxiety", "label": "Anxiety difficulty", "type": "number", "min": 0, "max": 3},
            {"key": "sleep", "label": "Sleep difficulty", "type": "number", "min": 0, "max": 3},
        ],
    }
    template.active = True
    template.updated_at = utc_now()
    session.flush()
    return template


def _ensure_demo_intake_template_file(
    session: Session,
    template: IntakeTemplate,
    spec: dict[str, Any],
    asset_path: Path,
) -> dict[str, Any]:
    content = asset_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    try:
        storage_uri = str(asset_path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        storage_uri = str(asset_path.resolve())
    key = str(spec.get("key") or "").strip()
    active_document = _active_intake_template_files_by_key(session, template).get(key)
    if active_document is not None:
        metadata = dict(active_document.metadata_json or {})
        if metadata.get("sha256") == digest and str(active_document.storage_uri or "") == storage_uri:
            return document_to_dict(active_document)
    metadata = {
        "file_name": asset_path.name,
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size_bytes": len(content),
        "sha256": digest,
        "storage_uri": storage_uri,
        "source": "gmail_demo_intake_asset",
    }
    return create_intake_template_file(
        session,
        template_id=template.id,
        item_key=key,
        title=asset_path.name,
        storage_uri=storage_uri,
        metadata=metadata,
    )


def _missing_demo_intake_asset_entry(template: IntakeTemplate, spec: dict[str, Any], asset_path: Path) -> dict[str, Any]:
    key = str(spec.get("key") or "").strip()
    return {
        "template_id": template.id,
        "template_name": template.name,
        "item_key": key,
        "item_label": str(spec.get("label") or key.replace("_", " ").title()),
        "item_type": str(spec.get("type") or "form"),
        "file_name": asset_path.name,
        "required": True,
        "reason": f"Demo blank DOCX file is missing from {DEMO_GMAIL_INTAKE_PACKET_ASSET_DIR}.",
    }


CLARA_DEMO_DOCUMENTATION_TRANSCRIPTS: tuple[tuple[str, str], ...] = (
    (
        "Initial documentation session",
        (
            "Therapist: We reviewed what brought you in and what would make therapy useful right now. "
            "Patient: I have been carrying a lot of work stress and I notice it most at night. "
            "Patient described difficulty falling asleep, replaying unfinished tasks, and feeling tense before meetings. "
            "Therapist introduced a brief grounding practice and asked the patient to notice physical cues of stress. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: patient will track sleep timing and work-stress triggers before the next session."
        ),
    ),
    (
        "Sleep and work stress follow-up",
        (
            "Patient reported two nights of improved sleep after using the grounding practice before bed. "
            "Patient also reported one difficult night after a late work email. "
            "Therapist helped the patient separate urgent tasks from tasks that could wait until morning. "
            "Patient responded that writing the next day's first task made the evening feel more manageable. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: continue grounding and add a brief end-of-work shutdown note."
        ),
    ),
    (
        "Boundaries and evening routine",
        (
            "Patient described checking work messages repeatedly after dinner and feeling pulled back into work. "
            "Therapist explored boundaries around phone notifications and supported the patient in choosing one realistic change. "
            "Patient agreed to silence work notifications after 20:00 on three weekdays. "
            "Therapist practiced a short breathing exercise with the patient. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: test the notification boundary and record what gets easier or harder."
        ),
    ),
    (
        "Partner tension and repair",
        (
            "Patient shared recent tension with their partner about being distracted and unavailable in the evenings. "
            "Therapist reflected the link between work rumination, withdrawal, and conflict at home. "
            "Patient identified wanting to communicate earlier instead of waiting until frustration builds. "
            "Therapist supported role-play of a short repair conversation. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: patient will try one brief check-in conversation before the next session."
        ),
    ),
    (
        "Coping with activation",
        (
            "Patient reported feeling activated before a presentation and noticing tightness in the chest and shoulders. "
            "Therapist guided the patient through naming sensations, orienting to the room, and slowing the pace of breathing. "
            "Patient said the exercise reduced the intensity enough to return to planning. "
            "Therapist emphasized practicing the skill before stress peaks. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: patient will practice orienting once daily and before work presentations."
        ),
    ),
    (
        "Reviewing progress",
        (
            "Patient reported fewer late-night work checks and described feeling more present during two evenings at home. "
            "Patient also reported frustration after one boundary was interrupted by an urgent request. "
            "Therapist normalized the setback and reviewed what was inside and outside the patient's control. "
            "Patient identified that the shutdown note was most helpful when written before leaving the desk. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: keep the shutdown note and refine the notification boundary."
        ),
    ),
    (
        "Values and workload",
        (
            "Patient explored how responsibility and fear of disappointing others affect workload decisions. "
            "Therapist used values clarification to distinguish being reliable from being constantly available. "
            "Patient described wanting to be reliable without sacrificing sleep and relationships. "
            "Therapist helped draft language for declining one non-urgent request. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: patient will use the drafted language if a similar request appears."
        ),
    ),
    (
        "Preparing for a difficult meeting",
        (
            "Patient described an upcoming meeting with a manager and worry about sounding defensive. "
            "Therapist supported rehearsal of a concise agenda, including what support the patient needs and what is realistic. "
            "Patient practiced slowing down before answering and naming one concrete request. "
            "Therapist reinforced observing body cues during the meeting. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: patient will bring the agenda and use one grounding pause before the meeting starts."
        ),
    ),
    (
        "After the manager meeting",
        (
            "Patient reported the manager meeting went better than expected and that the agenda helped keep the conversation focused. "
            "Patient noticed some tension but did not feel overwhelmed. "
            "Therapist reviewed the patient's use of grounding and communication skills. "
            "Patient identified feeling proud of asking for clearer priorities. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: monitor whether the priority agreement changes evening rumination."
        ),
    ),
    (
        "Maintaining gains",
        (
            "Patient reported continued improvement in sleep on workdays when the shutdown routine is completed. "
            "Patient described a remaining pattern of checking messages on Sunday evenings. "
            "Therapist explored what Sunday checking is trying to prevent and whether another planning ritual could meet that need. "
            "Patient agreed to a 15-minute Sunday planning window instead of repeated checking. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: test the planning window and compare sleep quality."
        ),
    ),
    (
        "Stress spike and adjustment",
        (
            "Patient reported a stressful week with several deadlines and one return to late-night work. "
            "Therapist helped the patient review the week without treating it as failure. "
            "Patient identified that skipping meals and breaks made evening stress worse. "
            "Therapist supported selecting one midday regulation practice. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: patient will schedule one protected lunch break on high-demand days."
        ),
    ),
    (
        "Consolidation session",
        (
            "Patient summarized that sleep, evening availability, and work boundaries have improved since the first session. "
            "Patient noted that stress still rises during deadline weeks but feels more workable. "
            "Therapist reviewed skills that have been useful: grounding, shutdown notes, notification boundaries, agenda preparation, and repair conversations. "
            "Patient identified wanting to keep practicing before stress becomes intense. "
            "Risk and safety were not directly assessed during this session. "
            "Plan: continue the current routine and revisit goals at the next session."
        ),
    ),
)


def seed_clara_demo_documentation_transcripts(session: Session, tenant_id: str = DEMO_TENANT_ID) -> dict[str, Any]:
    seed_demo_data(session)
    therapist = _ensure_clara_demo_therapist(session, tenant_id)
    patient = session.scalar(
        select(Patient)
        .where(
            Patient.tenant_id == tenant_id,
            func.lower(Patient.contact_email) == DEMO_OUTBOUND_PATIENT_EMAIL,
        )
        .order_by(Patient.updated_at.desc())
        .limit(1)
    )
    if patient is None:
        raise ValueError("Create the Gmail demo patient before seeding Clara documentation transcripts.")
    referral = session.scalar(
        select(Referral)
        .where(Referral.tenant_id == tenant_id, Referral.patient_id == patient.id)
        .order_by(Referral.updated_at.desc())
        .limit(1)
    )
    assignment = session.scalar(
        select(Appointment)
        .where(
            Appointment.tenant_id == tenant_id,
            Appointment.patient_id == patient.id,
            Appointment.therapist_id == therapist.id,
            Appointment.status != "cancelled",
        )
        .order_by(Appointment.starts_at.asc())
        .limit(1)
    )
    if assignment is None:
        assignment_start = utc_now() - timedelta(days=91)
        assignment = Appointment(
            id=f"demo-clara-gmail-doc-appt-{patient.id[:8]}",
            tenant_id=tenant_id,
            patient_id=patient.id,
            therapist_id=therapist.id,
            referral_id=referral.id if referral else None,
            starts_at=assignment_start,
            ends_at=assignment_start + timedelta(minutes=SESSION_LENGTH_MINUTES),
            status="confirmed",
            source="demo_clara_documentation_assignment",
        )
        session.add(assignment)
        session.flush()
    session_ids = [
        item
        for item in session.scalars(
            select(DocumentationSession.id).where(
                DocumentationSession.id.like(f"{DEMO_CLARA_DOCUMENTATION_SESSION_PREFIX}-%"),
                DocumentationSession.therapist_id == therapist.id,
            )
        )
    ]
    _delete_documentation_session_rows(session, session_ids)
    base_time = utc_now() - timedelta(days=84)
    created_sessions = []
    patient_label = patient.display_name or patient.contact_email or patient.id
    for index, (title, transcript) in enumerate(CLARA_DEMO_DOCUMENTATION_TRANSCRIPTS, start=1):
        created_at = base_time + timedelta(days=(index - 1) * 7)
        doc_session = DocumentationSession(
            id=f"{DEMO_CLARA_DOCUMENTATION_SESSION_PREFIX}-{index:03d}",
            tenant_id=tenant_id,
            patient_id=patient.id,
            therapist_id=therapist.id,
            referral_id=referral.id if referral else None,
            appointment_id=assignment.id,
            title=title,
            patient_label_snapshot=patient_label,
            therapist_label_snapshot=therapist.name,
            status="active",
            created_at=created_at,
            updated_at=created_at,
        )
        source_text = DocumentationSessionText(
            id=f"{DEMO_CLARA_DOCUMENTATION_TEXT_PREFIX}-{index:03d}",
            tenant_id=tenant_id,
            documentation_session_id=doc_session.id,
            text=transcript,
            input_type="manual_text",
            source_metadata={
                "source": "synthetic_admin_seed",
                "seed": "clara_demo_documentation_transcripts",
                "raw_source_stored": False,
            },
            raw_source_stored=False,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add_all([doc_session, source_text])
        created_sessions.append(doc_session)
    session.flush()
    return {
        "patient_id": patient.id,
        "therapist_id": therapist.id,
        "appointment_id": assignment.id,
        "referral_id": referral.id if referral else None,
        "session_count": len(created_sessions),
        "text_count": len(created_sessions),
        "sessions": [
            {
                "id": item.id,
                "title": item.title,
                "patient_id": item.patient_id,
                "therapist_id": item.therapist_id,
                "created_at": iso_or_none(item.created_at),
            }
            for item in created_sessions
        ],
    }


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
        if source_payload is not None and isinstance(source_payload, dict):
            current_payload = existing.source_payload if isinstance(existing.source_payload, dict) else {}
            if source_payload.get("id") and not current_payload.get("id"):
                existing.source_payload = json_safe({**current_payload, **source_payload})
                existing.draft_text = draft_text if draft_text is not None else existing.draft_text
                existing.updated_at = utc_now()
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


def _close_open_review_tasks(
    session: Session,
    referral: Referral,
    *,
    task_types: tuple[str, ...],
    status: str,
    reason: str,
) -> None:
    tasks = list(
        session.scalars(
            select(HumanReviewTask).where(
                HumanReviewTask.referral_id == referral.id,
                HumanReviewTask.task_type.in_(list(task_types)),
                HumanReviewTask.status == "open",
            )
        )
    )
    for task in tasks:
        before = review_task_to_dict(task)
        task.status = status
        task.rejection_reason = reason
        task.reviewed_at = utc_now()
        task.updated_at = utc_now()
        write_audit(
            session,
            tenant_id=task.tenant_id,
            actor_user_id=task.reviewer_id,
            action=f"review_{status}",
            entity_type="human_review_task",
            entity_id=task.id,
            before=before,
            after=review_task_to_dict(task),
        )
    if tasks:
        session.flush()


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
    if str(referral.source_channel or "").strip().lower() == "email":
        raise ValueError("Email referrals use the canonical LangGraph workflow for patient email drafts.")
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
        recipient_email=_outbound_patient_email(referral, patient),
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
    if str(referral.source_channel or "").strip().lower() == "email":
        raise ValueError("Email referrals use the canonical LangGraph workflow for patient email drafts.")
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
    source_metadata: dict[str, Any] | None = None,
    storage_uri: str | None = None,
    prepare_followup: bool = True,
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
    _normalise_email_optional_contact_missing_fields(session, referral)
    referral.updated_at = utc_now()
    patient = _ensure_patient_for_referral(session, referral)
    metadata = {
        "referral_id": referral.id,
        "source": source,
        "updates": json_safe(clean_updates),
        "notes": notes.strip(),
        "remaining_missing_fields": list(referral.missing_fields or []),
    }
    if source_metadata:
        metadata["source_metadata"] = json_safe(source_metadata)
    document = Document(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        document_type="missing_info_reply",
        title=f"Missing information reply from {source}",
        storage_uri=storage_uri,
        metadata_json=metadata,
    )
    session.add(document)
    session.flush()
    transition_referral_status(
        session,
        referral,
        _next_admin_gate_status(referral),
        reason="Missing-information reply recorded.",
    )
    _close_open_review_tasks(
        session,
        referral,
        task_types=("missing_info_message_approval",),
        status="superseded",
        reason="Missing-information reply was recorded; previous message approval is no longer actionable.",
    )
    if not referral.missing_fields:
        _close_open_review_tasks(
            session,
            referral,
            task_types=("admin_missing_info_review",),
            status="completed",
            reason="Missing information has been resolved.",
        )
        if prepare_followup and str(referral.source_channel or "").strip().lower() == "email":
            write_audit(
                session,
                tenant_id=referral.tenant_id,
                action="email_missing_info_ready_for_workflow",
                entity_type="referral",
                entity_id=referral.id,
                after={"source": "missing_info_reply"},
            )
    else:
        _ensure_admin_missing_info_task(session, referral)
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


def record_patient_reply(
    session: Session,
    referral_id: str,
    *,
    source: str = "patient",
    notes: str = "",
    reply_type: str = "unclassified",
    source_metadata: dict[str, Any] | None = None,
    storage_uri: str | None = None,
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    clean_notes = notes.strip()
    if not clean_notes:
        raise ValueError("Patient reply requires notes.")
    before = referral_summary(referral)
    patient = _ensure_patient_for_referral(session, referral)
    metadata = {
        "referral_id": referral.id,
        "source": source,
        "reply_type": reply_type,
        "notes": clean_notes,
    }
    if source_metadata:
        metadata["source_metadata"] = json_safe(source_metadata)
    document = Document(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        document_type="patient_reply",
        title=f"Patient reply from {source}",
        storage_uri=storage_uri,
        metadata_json=metadata,
    )
    session.add(document)
    session.flush()
    transition_referral_status(
        session,
        referral,
        "needs_admin_review",
        reason="Patient reply recorded; admin review required.",
    )
    task = create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=referral.id,
        patient_id=patient.id,
        task_type="inbound_reply_review",
        reason="Inbound patient reply requires admin review.",
        payload_key=f"inbound_reply:{document.id[:8]}",
        source_payload={
            "document_id": document.id,
            "reply_type": reply_type,
            "source": source,
        },
    )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="record_patient_reply",
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
    return {
        "reply": document_to_dict(document),
        "task": review_task_to_dict(task),
        "referral": referral_summary(referral),
    }


def ingest_gmail_message(
    session: Session,
    *,
    tenant_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("Gmail message is missing an id.")
    if _gmail_message_processed(session, message_id):
        return {"status": "skipped", "message_id": message_id, "reason": "already_processed"}

    thread_id = str(message.get("thread_id") or "").strip() or None
    subject = str(message.get("subject") or "").strip()
    body = str(message.get("body") or "").strip()
    snippet = str(message.get("snippet") or "").strip()
    sender_raw = str(message.get("from") or "").strip()
    sender_email = _extract_email_address(sender_raw)
    note = body or snippet or "No message body captured."

    metadata = {
        "gmail_message_id": message_id,
        "gmail_thread_id": thread_id,
        "from": sender_raw,
        "sender_email": sender_email,
        "subject": subject,
        "snippet": snippet,
        "date": str(message.get("date") or "").strip(),
    }
    attachments = [dict(item) for item in (message.get("attachments") or []) if isinstance(item, dict)]
    if attachments:
        metadata["attachments"] = json_safe(attachments)
    storage_uri = _gmail_storage_uri(message_id)
    referral, match_reason = _match_gmail_reply(session, thread_id, subject, note, sender_email)

    if referral is not None:
        outcome = _handle_gmail_patient_reply(
            session,
            referral,
            note=note,
            thread_id=thread_id,
            source_metadata={**metadata, "match_reason": match_reason},
            storage_uri=storage_uri,
        )
        return {
            "status": "processed",
            "message_id": message_id,
            "referral_id": referral.id,
            "match_reason": match_reason,
            **outcome,
        }

    document = Document(
        tenant_id=tenant_id,
        document_type="inbound_email_unmatched",
        title=subject or "Inbound email reply",
        storage_uri=storage_uri,
        metadata_json={**metadata, "body": note, "match_reason": match_reason},
    )
    session.add(document)
    session.flush()
    task = create_review_task(
        session,
        tenant_id=tenant_id,
        task_type="inbound_reply_review",
        reason="Unmatched inbound email reply requires routing.",
        payload_key=f"inbound_unmatched:{document.id[:8]}",
        source_payload={
            "document_id": document.id,
            "message_id": message_id,
            "match_reason": match_reason,
        },
    )
    write_audit(
        session,
        tenant_id=tenant_id,
        action="create",
        entity_type="document",
        entity_id=document.id,
        after=document_to_dict(document),
    )
    return {
        "status": "processed",
        "message_id": message_id,
        "action": "unmatched",
        "task_id": task.id,
        "match_reason": match_reason,
    }


def _handle_gmail_patient_reply(
    session: Session,
    referral: Referral,
    *,
    note: str,
    thread_id: str | None,
    source_metadata: dict[str, Any],
    storage_uri: str | None,
) -> dict[str, Any]:
    attachments = source_metadata.get("attachments") or []
    if _is_intake_submission_reply(session, referral, thread_id, attachments):
        return _handle_intake_submission_reply(
            session,
            referral,
            note=note,
            thread_id=thread_id,
            source_metadata=source_metadata,
            storage_uri=storage_uri,
            attachments=[dict(item) for item in attachments if isinstance(item, dict)],
        )

    reply_note = _strip_quoted_reply(note) or note
    choices = _slot_choice_context(session, referral, thread_id)
    reply_type, appointment_id = _classify_patient_reply(reply_note, choices)
    missing_updates = _extract_missing_info_updates_from_text(reply_note, referral.missing_fields or [])
    sender_email = str(source_metadata.get("sender_email") or "").strip()
    if (
        sender_email
        and _is_valid_email(sender_email)
        and "contact_email" not in missing_updates
        and ("contact_email" in (referral.missing_fields or []) or not referral.contact_email)
    ):
        missing_updates["contact_email"] = sender_email
    missing_reply: dict[str, Any] | None = None
    if missing_updates or (_is_missing_info_referral(referral) and reply_type not in {"accepted_slot", "alternative_requested", "declined", "ambiguous_slot"}):
        missing_reply = record_missing_info_reply(
            session,
            referral.id,
            source="patient",
            updates=missing_updates,
            notes=reply_note,
            source_metadata=source_metadata,
            storage_uri=storage_uri,
            prepare_followup=reply_type not in {"accepted_slot", "alternative_requested", "declined", "ambiguous_slot"},
        )
        if not missing_updates and referral.missing_fields:
            task = _ensure_reply_resolution_task(
                session,
                referral,
                note=reply_note,
                source_metadata=source_metadata,
                storage_uri=storage_uri,
                reason="Patient reply was received but could not be mapped to the remaining missing fields.",
                document_id=(missing_reply.get("reply") or {}).get("id") if missing_reply else None,
                candidate_choices=choices,
            )
            return {
                "action": "reply_resolution_required",
                "reply_type": "missing_info_reply",
                "appointment_id": None,
                "task_id": task.id,
                "missing_updates": {},
            }

    if reply_type == "accepted_slot" and appointment_id:
        acceptance = record_patient_slot_acceptance(
            session,
            referral.id,
            appointment_id=appointment_id,
            notes=reply_note,
            source_metadata=source_metadata,
            storage_uri=storage_uri,
            auto_approve=True,
        )
        action = "appointment_auto_confirmed" if acceptance.get("auto_approved") else "appointment_confirmation_requested"
        task = acceptance.get("task") or {}
        return {
            "action": action,
            "reply_type": reply_type,
            "appointment_id": appointment_id,
            "task_id": task.get("id"),
            "auto_approved": bool(acceptance.get("auto_approved")),
            "missing_updates": missing_updates,
        }

    if reply_type == "ambiguous_slot":
        task = _ensure_reply_resolution_task(
            session,
            referral,
            note=reply_note,
            source_metadata=source_metadata,
            storage_uri=storage_uri,
            reason="Patient reply matched multiple proposed slots. Select the appointment to confirm.",
            document_id=(missing_reply.get("reply") or {}).get("id") if missing_reply else None,
            candidate_choices=choices,
        )
        return {
            "action": "reply_resolution_required",
            "reply_type": reply_type,
            "appointment_id": None,
            "task_id": task.id,
            "missing_updates": missing_updates,
        }

    if missing_reply and reply_type == "unclear":
        return {
            "action": "missing_info_reply",
            "reply_type": "missing_info_reply",
            "appointment_id": None,
            "task_id": None,
            "missing_updates": missing_updates,
        }

    reply = record_patient_reply(
        session,
        referral.id,
        source="patient",
        notes=reply_note,
        reply_type=reply_type,
        source_metadata=source_metadata,
        storage_uri=storage_uri,
    )
    task = reply.get("task") or {}
    replacement: dict[str, Any] | None = None
    if reply_type in {"alternative_requested", "declined"}:
        _supersede_proposed_appointments_for_referral(session, referral, reason=f"Patient reply classified as {reply_type}.")
        if reply_type == "alternative_requested" and _patient_availability_constraints(reply_note):
            write_audit(
                session,
                tenant_id=referral.tenant_id,
                action="email_rebooking_requested",
                entity_type="referral",
                entity_id=referral.id,
                after={"reply_type": reply_type, "next_action": "rerun_langgraph_workflow"},
            )
    return {
        "action": "rebooking_requested" if reply_type in {"alternative_requested", "declined"} else "patient_reply",
        "reply_type": reply_type,
        "appointment_id": appointment_id,
        "task_id": task.get("id"),
        "missing_updates": missing_updates,
        "replacement": replacement,
    }


def _is_intake_submission_reply(
    session: Session,
    referral: Referral,
    thread_id: str | None,
    attachments: Any,
) -> bool:
    if canonical_referral_status(referral.status) not in INTAKE_REPLY_STATUSES:
        return False
    if attachments:
        return True
    if not thread_id:
        return True
    draft = session.scalar(
        select(CommunicationDraft)
        .where(
            CommunicationDraft.referral_id == referral.id,
            CommunicationDraft.gmail_thread_id == thread_id,
        )
        .order_by(CommunicationDraft.created_at.desc())
    )
    if draft is None:
        return True
    subject = _normal(draft.subject or "")
    body = _normal(draft.body or "")
    return "intake" in subject or "reply to this same email thread" in body


def _handle_intake_submission_reply(
    session: Session,
    referral: Referral,
    *,
    note: str,
    thread_id: str | None,
    source_metadata: dict[str, Any],
    storage_uri: str | None,
    attachments: list[dict[str, Any]],
) -> dict[str, Any]:
    patient = _ensure_patient_for_referral(session, referral)
    parent = Document(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        document_type="intake_submission_reply",
        title="Patient intake reply",
        storage_uri=storage_uri,
        metadata_json={
            "referral_id": referral.id,
            "patient_id": patient.id,
            "reply_text": note,
            "gmail_thread_id": thread_id,
            "source_metadata": json_safe(source_metadata),
            "attachments": json_safe(attachments),
        },
    )
    session.add(parent)
    session.flush()
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="document",
        entity_id=parent.id,
        after=document_to_dict(parent),
    )

    documents: list[Document] = []
    attachment_errors: list[dict[str, Any]] = []
    for attachment in attachments:
        if attachment.get("download_status") == "stored" and attachment.get("storage_uri"):
            document = Document(
                tenant_id=referral.tenant_id,
                patient_id=patient.id,
                document_type="intake_submission",
                title=str(attachment.get("file_name") or "Intake attachment"),
                storage_uri=str(attachment.get("storage_uri")),
                metadata_json={
                    **json_safe(attachment),
                    "referral_id": referral.id,
                    "patient_id": patient.id,
                    "reply_document_id": parent.id,
                    "gmail_thread_id": thread_id,
                },
            )
            session.add(document)
            session.flush()
            documents.append(document)
            extracted_text = str(attachment.get("extracted_text") or "")
            if extracted_text.strip():
                _index_text_chunks(
                    session,
                    tenant_id=referral.tenant_id,
                    patient_id=patient.id,
                    document_id=document.id,
                    source_type=document.document_type,
                    source_id=document.id,
                    text=extracted_text,
                    metadata={"title": document.title, "referral_id": referral.id},
                )
            write_audit(
                session,
                tenant_id=referral.tenant_id,
                action="create",
                entity_type="document",
                entity_id=document.id,
                after=document_to_dict(document),
            )
        else:
            attachment_errors.append(
                {
                    "file_name": attachment.get("file_name") or "attachment",
                    "mime_type": attachment.get("mime_type") or attachment.get("content_type"),
                    "error": attachment.get("error") or "Attachment could not be stored.",
                }
            )

    parent.metadata_json = {
        **(parent.metadata_json or {}),
        "document_ids": [document.id for document in documents],
        "attachment_errors": json_safe(attachment_errors),
    }
    parent.updated_at = utc_now()

    tasks: list[HumanReviewTask] = []
    for document in documents:
        tasks.append(
            create_review_task(
                session,
                tenant_id=referral.tenant_id,
                workflow_run_id=referral.workflow_run_id,
                referral_id=referral.id,
                patient_id=patient.id,
                task_type="intake_submission_review",
                reason="Patient returned an intake attachment; map it to the matching checklist item, consent, or questionnaire.",
                payload_key=f"intake_submission:{document.id[:8]}",
                source_payload=_intake_submission_review_payload(
                    session,
                    referral,
                    reply_document=parent,
                    documents=[document],
                    attachment_errors=[],
                    note=note,
                    source_metadata=source_metadata,
                ),
            )
        )
    if not tasks or attachment_errors:
        tasks.append(
            create_review_task(
                session,
                tenant_id=referral.tenant_id,
                workflow_run_id=referral.workflow_run_id,
                referral_id=referral.id,
                patient_id=patient.id,
                task_type="intake_submission_review",
                reason=(
                    "Patient replied to the intake request but no usable attachment was received."
                    if not documents
                    else "One or more intake attachments could not be stored and need admin review."
                ),
                payload_key=f"intake_submission_error:{parent.id[:8]}",
                source_payload=_intake_submission_review_payload(
                    session,
                    referral,
                    reply_document=parent,
                    documents=[],
                    attachment_errors=attachment_errors or [{"error": "No attachments were included."}],
                    note=note,
                    source_metadata=source_metadata,
                ),
                draft_text=note,
            )
        )

    auto_mapping = _auto_map_demo_intake_submissions(
        session,
        referral,
        documents=documents,
        review_tasks=tasks,
        source_metadata=source_metadata,
    )
    if auto_mapping["attempted"]:
        parent.metadata_json = json_safe(
            {
                **(parent.metadata_json or {}),
                "auto_mapping": auto_mapping,
            }
        )
        parent.updated_at = utc_now()
        _refresh_intake_review_task_context(session, referral, tasks)
        if auto_mapping["completed_all_required"]:
            _close_open_review_tasks(
                session,
                referral,
                task_types=("intake_submission_review",),
                status="completed",
                reason="Demo intake attachments were auto-mapped to all required intake items.",
            )

    if not auto_mapping["completed_all_required"] and canonical_referral_status(referral.status) != "first_session_ready":
        transition_referral_status(
            session,
            referral,
            "intake_incomplete",
            reason="Patient intake submission received for admin review.",
        )
    session.flush()

    return {
        "action": "intake_submission_review",
        "reply_type": "intake_submission",
        "task_id": tasks[0].id if tasks else None,
        "task_ids": [task.id for task in tasks],
        "document_ids": [document.id for document in documents],
        "reply_document_id": parent.id,
        "attachment_errors": attachment_errors,
        "auto_mapping": auto_mapping,
    }


def _intake_submission_review_payload(
    session: Session,
    referral: Referral,
    *,
    reply_document: Document,
    documents: list[Document],
    attachment_errors: list[dict[str, Any]],
    note: str,
    source_metadata: dict[str, Any],
) -> dict[str, Any]:
    context = _intake_review_context(session, referral)
    received_filenames = _intake_attachment_filenames(source_metadata)
    download_links = [
        {
            "document_id": document.id,
            "file_name": document.title,
            "download_url": f"/api/documents/{document.id}/download",
        }
        for document in documents
    ]
    return {
        "reply_document_id": reply_document.id,
        "reply_text": note,
        "source_metadata": json_safe(source_metadata),
        "received_attachment_filenames": received_filenames,
        "stored_attachment_filenames": [document.title for document in documents],
        "download_links": download_links,
        "documents": [document_to_dict(document) for document in documents],
        "document_ids": [document.id for document in documents],
        "document_id": documents[0].id if documents else None,
        "attachment_errors": json_safe(attachment_errors),
        "missing_intake_items": context["missing_items"],
        "missing_consents": context["missing_consents"],
    }


def _intake_review_context(session: Session, referral: Referral) -> dict[str, Any]:
    items = list(
        session.scalars(
            select(IntakeChecklistItem)
            .where(IntakeChecklistItem.referral_id == referral.id)
            .order_by(IntakeChecklistItem.created_at)
        )
    )
    consents: list[ConsentRecord] = []
    if referral.patient_id:
        consents = list(
            session.scalars(
                select(ConsentRecord)
                .where(ConsentRecord.tenant_id == referral.tenant_id, ConsentRecord.patient_id == referral.patient_id)
                .order_by(ConsentRecord.scope)
            )
        )
    return {
        "missing_items": [intake_item_to_dict(item) for item in items if not _intake_done(item.status)],
        "missing_consents": [consent_record_to_dict(consent) for consent in consents if not _intake_done(consent.status)],
    }


def _intake_attachment_filenames(source_metadata: dict[str, Any]) -> list[str]:
    filenames: list[str] = []
    for attachment in source_metadata.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        file_name = str(attachment.get("file_name") or attachment.get("filename") or "").strip()
        if file_name:
            filenames.append(file_name)
    return list(dict.fromkeys(filenames))


def _auto_map_demo_intake_submissions(
    session: Session,
    referral: Referral,
    *,
    documents: list[Document],
    review_tasks: list[HumanReviewTask],
    source_metadata: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "attempted": False,
        "completed": [],
        "unmapped": [],
        "completed_all_required": False,
    }
    if not documents or not _is_demo_gmail_intake_submission(referral, source_metadata):
        return result

    result["attempted"] = True
    tasks_by_document_id = _intake_review_tasks_by_document_id(review_tasks)
    for document in documents:
        target_key = _demo_intake_target_key(document.title)
        if not target_key:
            result["unmapped"].append(
                {
                    "document_id": document.id,
                    "file_name": document.title,
                    "reason": "Filename did not match a demo intake item.",
                }
            )
            continue

        item = _demo_intake_item_for_key(session, referral, target_key)
        if item is None:
            result["unmapped"].append(
                {
                    "document_id": document.id,
                    "file_name": document.title,
                    "target_key": target_key,
                    "reason": "No matching checklist item exists for this referral.",
                }
            )
            continue

        completed = _apply_demo_intake_document_mapping(session, referral, document, item, target_key)
        result["completed"].extend(completed)
        task = tasks_by_document_id.get(document.id)
        if task is not None and task.status == "open":
            _complete_intake_review_task_from_auto_mapping(session, task, document, target_key, completed)

    if result["completed"]:
        _refresh_referral_intake_status(session, referral.id)
    context = _intake_review_context(session, referral)
    result["completed_all_required"] = not context["missing_items"] and not context["missing_consents"]
    return json_safe(result)


def _is_demo_gmail_intake_submission(referral: Referral, source_metadata: dict[str, Any]) -> bool:
    if str(referral.source_channel or "").strip().lower() != "email":
        return False
    sender_email = str(source_metadata.get("sender_email") or "").strip().lower()
    if not sender_email:
        sender_email = (_extract_email_address(str(source_metadata.get("from") or "")) or "").lower()
    if sender_email != DEMO_OUTBOUND_PATIENT_EMAIL:
        return False
    contact_email = str(referral.contact_email or "").strip().lower()
    return not contact_email or contact_email == DEMO_OUTBOUND_PATIENT_EMAIL


def _intake_review_tasks_by_document_id(tasks: list[HumanReviewTask]) -> dict[str, HumanReviewTask]:
    by_document_id: dict[str, HumanReviewTask] = {}
    for task in tasks:
        payload = task.source_payload if isinstance(task.source_payload, dict) else {}
        document_ids = [payload.get("document_id"), *(payload.get("document_ids") or [])]
        for document_id in document_ids:
            clean = str(document_id or "").strip()
            if clean:
                by_document_id[clean] = task
    return by_document_id


def _demo_intake_target_key(file_name: str) -> str | None:
    token = _filename_match_token(file_name)
    for key, hints in DEMO_GMAIL_INTAKE_FILENAME_HINTS.items():
        if any(hint in token for hint in hints):
            return key
    return None


def _filename_match_token(value: str) -> str:
    stem = Path(str(value or "")).stem
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")


def _demo_intake_item_for_key(session: Session, referral: Referral, target_key: str) -> IntakeChecklistItem | None:
    hints = DEMO_GMAIL_INTAKE_FILENAME_HINTS.get(target_key, (target_key,))
    items = list(
        session.scalars(
            select(IntakeChecklistItem)
            .where(IntakeChecklistItem.tenant_id == referral.tenant_id, IntakeChecklistItem.referral_id == referral.id)
            .order_by(IntakeChecklistItem.created_at)
        )
    )
    for item in items:
        item_key = _filename_match_token(item.item_key)
        item_label = _filename_match_token(item.label)
        if item_key == target_key or any(hint in item_key or hint in item_label for hint in hints):
            return item
    return None


def _apply_demo_intake_document_mapping(
    session: Session,
    referral: Referral,
    document: Document,
    item: IntakeChecklistItem,
    target_key: str,
) -> list[dict[str, str]]:
    before_document = document_to_dict(document)
    completed: list[dict[str, str]] = []
    if not _intake_done(item.status):
        completed_item = _complete_intake_item_from_document(session, referral, document, item.id)
        completed.append({"type": "intake_item", "id": completed_item.id, "label": completed_item.label})
        if completed_item.item_type == "questionnaire":
            _maybe_save_questionnaire_from_document(session, referral, document, completed_item, completed_item.item_key)
    elif item.item_type == "consent":
        _complete_matching_consent_for_item(session, item, document.id)

    if item.item_type == "consent":
        for consent in _matching_consents_for_intake_item(session, item):
            if consent.source_document_id == document.id and _intake_done(consent.status):
                completed.append({"type": "consent", "id": consent.id, "scope": consent.scope})

    metadata = document.metadata_json or {}
    document.metadata_json = json_safe(
        {
            **metadata,
            "linked_intake_item_id": item.id,
            "demo_auto_mapping_key": target_key,
            "questionnaire_name": item.item_key if item.item_type == "questionnaire" else None,
            "review_outcome": "auto_mapped",
        }
    )
    document.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="map_intake_submission",
        entity_type="document",
        entity_id=document.id,
        before=before_document,
        after=document_to_dict(document),
    )
    return completed


def _matching_consents_for_intake_item(session: Session, item: IntakeChecklistItem) -> list[ConsentRecord]:
    if not item.patient_id:
        return []
    item_key = _normal(item.item_key)
    item_label = _normal(item.label)
    return [
        consent
        for consent in session.scalars(
            select(ConsentRecord).where(
                ConsentRecord.tenant_id == item.tenant_id,
                ConsentRecord.patient_id == item.patient_id,
            )
        )
        if (scope := _normal(consent.scope)) and (scope in item_key or scope in item_label or item_key in scope)
    ]


def _complete_intake_review_task_from_auto_mapping(
    session: Session,
    task: HumanReviewTask,
    document: Document,
    target_key: str,
    completed: list[dict[str, str]],
) -> None:
    before = review_task_to_dict(task)
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    task.status = "completed"
    task.reviewed_at = utc_now()
    task.source_payload = json_safe(
        {
            **payload,
            "selected_document_id": document.id,
            "demo_auto_mapping_key": target_key,
            "review_outcome": "auto_mapped",
            "completed": completed,
        }
    )
    task.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=task.tenant_id,
        actor_user_id=task.reviewer_id,
        action="review_completed",
        entity_type="human_review_task",
        entity_id=task.id,
        before=before,
        after=review_task_to_dict(task),
    )


def _refresh_intake_review_task_context(
    session: Session,
    referral: Referral,
    tasks: list[HumanReviewTask],
) -> None:
    context = _intake_review_context(session, referral)
    for task in tasks:
        if task.status != "open":
            continue
        payload = task.source_payload if isinstance(task.source_payload, dict) else {}
        task.source_payload = json_safe(
            {
                **payload,
                "missing_intake_items": context["missing_items"],
                "missing_consents": context["missing_consents"],
            }
        )
        task.updated_at = utc_now()


def _ensure_reply_resolution_task(
    session: Session,
    referral: Referral,
    *,
    note: str,
    source_metadata: dict[str, Any],
    storage_uri: str | None,
    reason: str,
    document_id: str | None = None,
    candidate_choices: list[dict[str, Any]] | None = None,
) -> HumanReviewTask:
    message_id = str(source_metadata.get("gmail_message_id") or storage_uri or "")[:80]
    if not document_id:
        patient = _ensure_patient_for_referral(session, referral)
        document = Document(
            tenant_id=referral.tenant_id,
            patient_id=patient.id,
            document_type="patient_reply",
            title="Patient reply requiring admin resolution",
            storage_uri=storage_uri,
            metadata_json={
                "referral_id": referral.id,
                "source": "patient",
                "reply_type": "ambiguous_slot",
                "notes": note,
                "source_metadata": json_safe(source_metadata),
            },
        )
        session.add(document)
        session.flush()
        document_id = document.id
        write_audit(
            session,
            tenant_id=referral.tenant_id,
            action="create",
            entity_type="document",
            entity_id=document.id,
            after=document_to_dict(document),
        )
    candidates = _slot_choice_payloads(session, candidate_choices or [])
    return create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=referral.id,
        patient_id=referral.patient_id,
        task_type="inbound_reply_review",
        reason=reason,
        payload_key=f"reply_resolution:{message_id or referral.id[:8]}",
        source_payload={
            "document_id": document_id,
            "remaining_missing_fields": list(referral.missing_fields or []),
            "reply_text": note,
            "source_metadata": json_safe(source_metadata),
            "storage_uri": storage_uri,
            "candidate_appointments": candidates,
            "candidate_appointment_ids": [item["appointment_id"] for item in candidates],
        },
        draft_text=note,
    )


def _slot_choice_payloads(session: Session, choices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for choice in choices:
        appointment = choice.get("appointment")
        if not isinstance(appointment, Appointment):
            continue
        therapist = session.get(Therapist, appointment.therapist_id or "") if appointment.therapist_id else None
        payloads.append(
            {
                "index": choice.get("index"),
                "appointment_id": appointment.id,
                "code": choice.get("code"),
                "therapist_id": appointment.therapist_id,
                "therapist_name": therapist.name if therapist else None,
                "starts_at": iso_or_none(appointment.starts_at),
                "ends_at": iso_or_none(appointment.ends_at),
                "status": appointment.status,
            }
        )
    return payloads


def record_patient_slot_acceptance(
    session: Session,
    referral_id: str,
    *,
    appointment_id: str,
    notes: str,
    source_metadata: dict[str, Any] | None = None,
    storage_uri: str | None = None,
    auto_approve: bool = True,
    reviewer_id: str | None = DEMO_USER_ID,
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        raise KeyError(f"Unknown appointment: {appointment_id}")
    if appointment.referral_id != referral.id:
        raise ValueError("Appointment does not belong to this referral.")

    patient = _ensure_patient_for_referral(session, referral)
    clean_notes = notes.strip() or "Patient accepted a proposed slot."
    metadata = {
        "referral_id": referral.id,
        "source": "patient",
        "reply_type": "accepted_slot",
        "appointment_id": appointment.id,
        "notes": clean_notes,
    }
    if source_metadata:
        metadata["source_metadata"] = json_safe(source_metadata)

    before = referral_summary(referral)
    document = Document(
        tenant_id=referral.tenant_id,
        patient_id=patient.id,
        document_type="patient_reply",
        title="Patient reply from patient",
        storage_uri=storage_uri,
        metadata_json=metadata,
    )
    session.add(document)
    session.flush()
    transition_referral_status(
        session,
        referral,
        "awaiting_patient_reply",
        reason="Patient accepted a proposed slot; confirmation required.",
    )
    task = create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=referral.id,
        patient_id=patient.id,
        task_type="appointment_confirmation_approval",
        reason="Patient accepted a proposed slot; approve to confirm the appointment record.",
        payload_key=f"appointment_confirmation:{appointment.id[:8]}",
        source_payload={
            "reply_type": "accepted_slot",
            "appointment_id": appointment.id,
            "patient_reply_document_id": document.id,
            "notes": clean_notes,
        },
    )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="record_patient_reply",
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

    auto_approved = False
    if auto_approve:
        try:
            task = apply_review_action(
                session,
                task_id=task.id,
                action="approve",
                reviewer_id=reviewer_id,
            )
            auto_approved = task.status == "approved"
        except Exception as exc:
            _record_task_provider_failure(session, task, str(exc))

    return {
        "reply": document_to_dict(document),
        "task": review_task_to_dict(task),
        "auto_approved": auto_approved,
    }


def _slot_choice_context(
    session: Session,
    referral: Referral,
    thread_id: str | None,
) -> list[dict[str, Any]]:
    appointment_ids: list[str] = []
    if thread_id:
        draft = session.scalar(
            select(CommunicationDraft)
            .where(CommunicationDraft.gmail_thread_id == thread_id)
            .order_by(CommunicationDraft.created_at.desc())
            .limit(1)
        )
        appointment_ids = list((draft.proposed_slots or [])) if draft else []

    if not appointment_ids:
        recent_drafts = list(
            session.scalars(
                select(CommunicationDraft)
                .where(CommunicationDraft.referral_id == referral.id)
                .order_by(CommunicationDraft.created_at.desc())
                .limit(5)
            )
        )
        for draft in recent_drafts:
            if draft.proposed_slots:
                appointment_ids = list(draft.proposed_slots)
                break

    appointments: list[Appointment] = []
    if appointment_ids:
        for appointment_id in appointment_ids:
            appointment = session.get(Appointment, appointment_id)
            if appointment is not None:
                appointments.append(appointment)
    if not appointments:
        appointments = list(
            session.scalars(
                select(Appointment)
                .where(Appointment.referral_id == referral.id, Appointment.status == "proposed")
                .order_by(Appointment.starts_at)
            )
        )

    choices = []
    for index, appointment in enumerate(appointments, start=1):
        choices.append(
            {
                "index": index,
                "appointment": appointment,
                "code": _appointment_choice_code(appointment.id),
            }
        )
    return choices


def _appointment_choice_code(appointment_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", appointment_id)[:6].upper()


def _classify_patient_reply(
    note: str,
    choices: list[dict[str, Any]],
) -> tuple[str, str | None]:
    appointment_id = _match_slot_choice(note, choices)
    if appointment_id:
        return "accepted_slot", appointment_id

    text = _normalize_reply_text(note)
    if _looks_like_slot_acceptance(text) and len(choices) > 1:
        matching_choices = _matching_slot_choices(text, choices)
        if len(matching_choices) != 1:
            return "ambiguous_slot", None
    if _contains_any(
        text,
        [
            "alternative",
            "alternatives",
            "another time",
            "other time",
            "different time",
            "other option",
            "other options",
            "none of these",
            "neither",
            "not available",
            "cant do",
            "cannot do",
            "can't do",
        ],
    ):
        return "alternative_requested", None
    if _contains_any(text, ["decline", "not interested", "no thanks", "cancel", "stop", "do not contact"]):
        return "declined", None
    if len(choices) == 1 and _looks_like_slot_acceptance(text):
        appointment = choices[0].get("appointment")
        return ("accepted_slot", appointment.id) if appointment else ("accepted_slot", None)
    if "?" in text or _contains_any(text, ["question", "wondering", "can you", "could you", "what about"]):
        return "asked_question", None
    return "unclear", None


def _match_slot_choice(note: str, choices: list[dict[str, Any]]) -> str | None:
    if not note or not choices:
        return None
    text = _normalize_reply_text(note)
    for choice in choices:
        code = str(choice.get("code") or "").lower()
        if code and code in text:
            appointment = choice.get("appointment")
            return appointment.id if appointment else None

    word_map = {"first": 1, "second": 2, "third": 3}
    for choice in choices:
        index = int(choice.get("index") or 0)
        if not index:
            continue
        if re.search(rf"\b(option|opt|slot|choice|opcao)\s*{index}\b", text):
            appointment = choice.get("appointment")
            return appointment.id if appointment else None
        if re.search(rf"\b{index}(st|nd|rd|th)\b", text):
            appointment = choice.get("appointment")
            return appointment.id if appointment else None
        for word, mapped in word_map.items():
            if mapped == index and re.search(rf"\b{word}\b", text):
                appointment = choice.get("appointment")
                return appointment.id if appointment else None

    for choice in choices:
        appointment = choice.get("appointment")
        if appointment and appointment.starts_at and _reply_mentions_slot(text, appointment):
            return appointment.id
    matching_choices = _matching_slot_choices(text, choices)
    if len(matching_choices) == 1:
        appointment = matching_choices[0].get("appointment")
        return appointment.id if appointment else None
    return None


def _looks_like_slot_acceptance(text: str) -> bool:
    return _contains_any(
        text,
        [
            "yes",
            "confirm",
            "i can attend",
            "can attend",
            "can i proceed",
            "that works",
            "this works",
            "works for me",
            "i can do",
            "i will attend",
            "please book",
            "book it",
            "i accept",
            "accepted",
            "have this appointment",
            "proceed with this session",
        ],
    )


def _matching_slot_choices(text: str, choices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for choice in choices:
        appointment = choice.get("appointment")
        if not isinstance(appointment, Appointment) or not appointment.starts_at:
            continue
        if _reply_mentions_slot_time(text, appointment):
            matches.append(choice)
    return matches


def _reply_mentions_slot(text: str, appointment: Appointment) -> bool:
    date_token = appointment.starts_at.date().isoformat()
    time_token = appointment.starts_at.strftime("%H:%M")
    if date_token in text and time_token in text:
        return True
    if time_token.startswith("0") and date_token in text and time_token[1:] in text:
        return True
    return False


def _reply_mentions_slot_time(text: str, appointment: Appointment) -> bool:
    time_token = appointment.starts_at.strftime("%H:%M")
    loose_token = time_token[1:] if time_token.startswith("0") else time_token
    return bool(re.search(rf"\b{re.escape(time_token)}\b", text) or re.search(rf"\b{re.escape(loose_token)}\b", text))


def _normalize_reply_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _contains_any(text: str, phrases: list[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _extract_missing_info_updates_from_text(text: str, missing_fields: list[str] | None = None) -> dict[str, str]:
    value = _strip_quoted_reply(str(text or ""))
    updates: dict[str, str] = {}

    patient_name = _extract_patient_name(value)
    if patient_name:
        updates["patient_name"] = patient_name

    email = _extract_email_address(value)
    if email:
        updates["contact_email"] = email

    dob = _extract_date_of_birth(value)
    if dob:
        updates["date_of_birth"] = dob

    phone = _extract_phone_number(value)
    if phone:
        updates["contact_phone"] = phone

    insurer = _extract_insurer(value)
    if insurer:
        updates["insurer"] = insurer

    referring_entity = _extract_referring_entity(value)
    if referring_entity:
        updates["referring_entity"] = referring_entity

    wanted = set(missing_fields or [])
    if wanted:
        aliases = {
            "contact_phone_or_date_of_birth": {"contact_phone", "date_of_birth"},
            "dob": {"date_of_birth"},
            "email": {"contact_email"},
            "phone": {"contact_phone"},
            "insurance": {"insurer"},
            "name": {"patient_name"},
            "referrer": {"referring_entity"},
            "referring_entity": {"referring_entity"},
        }
        allowed = set(wanted)
        for field in wanted:
            allowed.update(aliases.get(field, set()))
        updates = {
            key: item
            for key, item in updates.items()
            if key in allowed or not wanted
        }
    return updates


def _strip_quoted_reply(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        clean = line.strip()
        if clean.startswith(">"):
            continue
        if re.match(r"^on .+ wrote:\s*$", clean, re.I):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_patient_name(text: str) -> str | None:
    patterns = [
        r"\bmy name is\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' .-]{1,80})",
        r"\bpatient name\s*(?:is|:|-)?\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' .-]{1,80})",
        r"\bname\s*(?:is|:|-)\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' .-]{1,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _clean_extracted_text_value(match.group(1), max_words=6)
    return None


def _extract_date_of_birth(text: str) -> str | None:
    label_pattern = re.compile(
        r"\b(?:dob|date of birth|birth date|data de nascimento|nascimento)\b\s*(?:is|:|-)?\s*"
        r"(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        re.I,
    )
    match = label_pattern.search(text)
    if not match:
        return None
    raw = match.group(1)
    if re.match(r"\d{4}-\d{1,2}-\d{1,2}$", raw):
        year, month, day = raw.split("-")
        return _normalise_date_of_birth_parts(year, month, day)
    parts = re.split(r"[/-]", raw)
    if len(parts) != 3:
        return raw
    day, month, year = parts
    if len(year) == 2:
        year = f"19{year}" if int(year) > 30 else f"20{year}"
    return _normalise_date_of_birth_parts(year, month, day)


def _normalise_date_of_birth_parts(year: str, month: str, day: str) -> str | None:
    try:
        parsed = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
    except ValueError:
        return None
    now = utc_now()
    if parsed.date() > now.date() or parsed.year < now.year - 120:
        return None
    return f"{parsed.year:04d}-{parsed.month:02d}-{parsed.day:02d}"


def _extract_phone_number(text: str) -> str | None:
    match = re.search(r"\b(?:phone|telephone|telemovel|telemóvel|contact number)\b\s*(?:is|:|-)?\s*([+\d][\d\s().-]{6,})", text, re.I)
    if not match:
        return None
    phone = re.sub(r"\s+", " ", match.group(1)).strip(" .")
    digits = re.sub(r"\D", "", phone)
    return phone if len(digits) >= 7 else None


def _extract_insurer(text: str) -> str | None:
    known = ["Multicare", "AdvanceCare", "Médis", "Medis", "Allianz", "Fidelidade", "Tranquilidade"]
    lowered = text.lower()
    for name in known:
        if name.lower() in lowered:
            return "Médis" if name == "Medis" else name
    match = re.search(r"\b(?:insurer|insurance|seguradora|seguro)\b\s*(?:is|:|-)?\s*([A-Za-zÀ-ÿ0-9 &.-]{2,60})", text, re.I)
    if not match:
        return None
    return match.group(1).strip(" .")


def _extract_referring_entity(text: str) -> str | None:
    patterns = [
        r"\breferring entity\b\s*(?:(?:is|was)\s+|[:\-]\s*)?([A-Za-zÀ-ÿ0-9 &\"'()./-]{2,100})",
        r"\breferrer\b\s*(?:(?:is|was)\s+|[:\-]\s*)?([A-Za-zÀ-ÿ0-9 &\"'()./-]{2,100})",
        r"\breferred by\s+([A-Za-zÀ-ÿ0-9 &\"'()./-]{2,100})",
        r"\bgp\b\s*(?:(?:is|was)\s+|[:\-]\s*)?([A-Za-zÀ-ÿ0-9 &\"'()./-]{2,100})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        value = _clean_extracted_text_value(match.group(1), max_words=8)
        if not value:
            continue
        if _normal(value) in {"none", "no", "n/a", "na", "self", "self referral", "self-referral"}:
            return "Self-referral"
        return value
    return None


def _clean_extracted_text_value(value: str, *, max_words: int = 12) -> str | None:
    clean = re.split(r"[\r\n]", str(value or ""), maxsplit=1)[0]
    clean = re.split(r"\s{2,}|[.;,]", clean, maxsplit=1)[0]
    clean = clean.strip(" \"'.,:-")
    words = clean.split()
    if max_words and len(words) > max_words:
        clean = " ".join(words[:max_words])
    return clean or None


def _clean_demo_extracted_referral_output(
    session: Session,
    referral: Referral,
    referral_output: dict[str, Any],
    *,
    raw_text: str,
    source_channel: str,
    workflow_run_id: str | None,
) -> tuple[dict[str, Any], list[str]]:
    if source_channel != "email":
        return dict(referral_output or {}), []

    cleaned = dict(referral_output or {})
    missing_fields: list[str] = []
    discarded: dict[str, Any] = {}
    corrected: dict[str, Any] = {}
    source_text = _strip_quoted_reply(raw_text) or raw_text

    explicit_name = _extract_patient_name(source_text)
    candidate_name = str(cleaned.get("patient_name") or "").strip()
    if explicit_name and _normal(candidate_name) != _normal(explicit_name):
        cleaned["patient_name"] = explicit_name
        corrected["patient_name"] = {"from": candidate_name or None, "to": explicit_name}

    if cleaned.get("date_of_birth") and not _source_supports_date_of_birth(source_text, str(cleaned.get("date_of_birth"))):
        discarded["date_of_birth"] = cleaned.pop("date_of_birth")
        missing_fields.append("date_of_birth")
    if cleaned.get("contact_phone") and not _source_supports_phone(source_text, str(cleaned.get("contact_phone"))):
        discarded["contact_phone"] = cleaned.pop("contact_phone")
        missing_fields.append("contact_phone")
    if cleaned.get("insurer") and not _source_supports_text_field(source_text, str(cleaned.get("insurer")), _extract_insurer(source_text)):
        discarded["insurer"] = cleaned.pop("insurer")
        missing_fields.append("insurer")
    if cleaned.get("referring_entity") and not _source_supports_text_field(
        source_text,
        str(cleaned.get("referring_entity")),
        _extract_referring_entity(source_text),
    ):
        discarded["referring_entity"] = cleaned.pop("referring_entity")
        missing_fields.append("referring_entity")

    if discarded or corrected:
        write_audit(
            session,
            tenant_id=referral.tenant_id,
            action="clean_demo_unsupported_extracted_facts",
            entity_type="referral",
            entity_id=referral.id,
            after={
                "workflow_run_id": workflow_run_id,
                "discarded": json_safe(discarded),
                "corrected": json_safe(corrected),
            },
        )
    return cleaned, list(dict.fromkeys(missing_fields))


def _source_supports_date_of_birth(source_text: str, candidate: str) -> bool:
    parsed = _extract_date_of_birth(source_text)
    return bool(parsed and parsed == candidate)


def _source_supports_phone(source_text: str, candidate: str) -> bool:
    parsed = _extract_phone_number(source_text)
    if not parsed:
        return False
    return re.sub(r"\D", "", parsed) == re.sub(r"\D", "", candidate)


def _source_supports_text_field(source_text: str, candidate: str, parsed: str | None) -> bool:
    clean_candidate = _normal(candidate)
    if not clean_candidate:
        return False
    if parsed and _normal(parsed) == clean_candidate:
        return True
    return clean_candidate in _normal(source_text)


def _supersede_proposed_appointments_for_referral(session: Session, referral: Referral, *, reason: str) -> None:
    appointments = list(
        session.scalars(
            select(Appointment).where(
                Appointment.referral_id == referral.id,
                Appointment.status == "proposed",
            )
        )
    )
    for appointment in appointments:
        before = appointment_to_dict(appointment)
        appointment.status = "cancelled"
        appointment.updated_at = utc_now()
        write_audit(
            session,
            tenant_id=appointment.tenant_id,
            action="supersede_proposed_appointment",
            entity_type="appointment",
            entity_id=appointment.id,
            before=before,
            after={**appointment_to_dict(appointment), "reason": reason},
        )
    if appointments:
        session.flush()


def list_inbound_gmail_messages(
    session: Session,
    *,
    tenant_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = select(Document).where(Document.storage_uri.like(f"{INBOUND_GMAIL_STORAGE_PREFIX}%"))
    if tenant_id:
        query = query.where(Document.tenant_id == tenant_id)
    query = query.order_by(Document.created_at.desc()).limit(max(1, min(limit, 200)))
    messages = []
    for document in session.scalars(query):
        metadata = document.metadata_json or {}
        referral_id = _gmail_metadata_value(metadata, "referral_id")
        status = "unmatched" if document.document_type == "inbound_email_unmatched" else "linked"
        if document.document_type in {"missing_info_reply", "patient_reply"}:
            status = "reply"
        if metadata.get("converted_to_referral"):
            status = "converted"
        messages.append(
            {
                "document_id": document.id,
                "tenant_id": document.tenant_id,
                "document_type": document.document_type,
                "status": status,
                "message_id": _gmail_metadata_value(metadata, "gmail_message_id")
                or _gmail_message_id_from_storage(document.storage_uri),
                "thread_id": _gmail_metadata_value(metadata, "gmail_thread_id"),
                "from": _gmail_metadata_value(metadata, "from"),
                "sender_email": _gmail_metadata_value(metadata, "sender_email"),
                "subject": _gmail_metadata_value(metadata, "subject") or document.title,
                "date": _gmail_metadata_value(metadata, "date"),
                "snippet": _gmail_metadata_value(metadata, "snippet"),
                "body": _gmail_inbox_body(metadata),
                "reply_type": _gmail_metadata_value(metadata, "reply_type"),
                "match_reason": _gmail_metadata_value(metadata, "match_reason"),
                "referral_id": referral_id,
                "created_at": iso_or_none(document.created_at),
                "updated_at": iso_or_none(document.updated_at),
            }
        )
    return messages


def gmail_referral_workflow_input(
    session: Session,
    *,
    document_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    document = session.get(Document, document_id)
    if document is None:
        raise KeyError(f"Unknown inbound email document: {document_id}")
    if document.tenant_id != tenant_id:
        raise ValueError("Inbound email document does not match the tenant.")
    if document.document_type != "inbound_email_unmatched":
        raise ValueError("Inbound email document is not eligible for conversion.")

    metadata = dict(document.metadata_json or {})
    referral_id = _gmail_metadata_value(metadata, "referral_id")
    if referral_id:
        referral = session.get(Referral, referral_id)
        return {
            "status": "already_converted",
            "referral_id": referral_id,
            "job_id": _gmail_metadata_value(metadata, "workflow_job_id"),
            "referral": referral_summary(referral) if referral else None,
            "document": document_to_dict(document),
        }

    sender = _gmail_metadata_value(metadata, "sender_email") or _gmail_metadata_value(metadata, "from") or ""
    subject = _gmail_metadata_value(metadata, "subject") or document.title or "Email referral"
    body = _gmail_inbox_body(metadata)
    if not body.strip():
        raise ValueError("Inbound email is missing a usable body.")

    raw_text = _email_referral_raw_text(sender=sender, subject=subject, body=body)
    return {
        "status": "ready",
        "document": document_to_dict(document),
        "raw_input": {
            "source_channel": "email",
            "raw_text": raw_text,
            "sender": sender,
            "subject": subject,
            "contact_email": _extract_email_address(sender),
            "gmail_message_id": _gmail_metadata_value(metadata, "gmail_message_id")
            or _gmail_message_id_from_storage(document.storage_uri),
            "gmail_thread_id": _gmail_metadata_value(metadata, "gmail_thread_id"),
            "inbound_document_id": document.id,
        },
    }


def mark_inbound_gmail_referral_workflow_started(
    session: Session,
    *,
    document_id: str,
    tenant_id: str,
    referral_id: str | None,
    job_id: str,
    actor_user_id: str | None = DEMO_USER_ID,
) -> dict[str, Any]:
    document = session.get(Document, document_id)
    if document is None:
        raise KeyError(f"Unknown inbound email document: {document_id}")
    if document.tenant_id != tenant_id:
        raise ValueError("Inbound email document does not match the tenant.")
    metadata = dict(document.metadata_json or {})
    metadata.update(
        {
            "referral_id": referral_id,
            "workflow_job_id": job_id,
            "converted_to_referral": True,
            "converted_at": iso_or_none(utc_now()),
        }
    )
    document.metadata_json = json_safe(metadata)
    document.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=document.tenant_id,
        actor_user_id=actor_user_id if session.get(User, actor_user_id or "") else None,
        action="start_inbound_email_workflow",
        entity_type="document",
        entity_id=document.id,
        after=document_to_dict(document),
    )
    return document_to_dict(document)


def convert_inbound_gmail_to_referral(
    session: Session,
    *,
    document_id: str,
    tenant_id: str,
    actor_user_id: str | None = DEMO_USER_ID,
) -> dict[str, Any]:
    document = session.get(Document, document_id)
    if document is None:
        raise KeyError(f"Unknown inbound email document: {document_id}")
    if document.tenant_id != tenant_id:
        raise ValueError("Inbound email document does not match the tenant.")
    if document.document_type != "inbound_email_unmatched":
        raise ValueError("Inbound email document is not eligible for conversion.")

    metadata = dict(document.metadata_json or {})
    referral_id = _gmail_metadata_value(metadata, "referral_id")
    if referral_id:
        referral = session.get(Referral, referral_id)
        return {"referral": referral_summary(referral) if referral else None, "document": document_to_dict(document)}

    sender = _gmail_metadata_value(metadata, "sender_email") or _gmail_metadata_value(metadata, "from") or ""
    subject = _gmail_metadata_value(metadata, "subject") or document.title or "Email referral"
    body = _gmail_inbox_body(metadata)
    if not body.strip():
        raise ValueError("Inbound email is missing a usable body.")

    result = create_email_referral(
        session,
        tenant_id=tenant_id,
        sender=sender,
        subject=subject,
        body=body,
        actor_user_id=actor_user_id,
    )
    metadata.update(
        {
            "referral_id": result["referral"]["id"],
            "converted_to_referral": True,
            "converted_at": iso_or_none(utc_now()),
        }
    )
    document.metadata_json = json_safe(metadata)
    document.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=document.tenant_id,
        actor_user_id=actor_user_id if session.get(User, actor_user_id or "") else None,
        action="convert_inbound_email",
        entity_type="document",
        entity_id=document.id,
        after=document_to_dict(document),
    )
    return {"referral": result["referral"], "document": document_to_dict(document)}


def _gmail_message_id_from_storage(storage_uri: str | None) -> str | None:
    if not storage_uri:
        return None
    if storage_uri.startswith(INBOUND_GMAIL_STORAGE_PREFIX):
        return storage_uri[len(INBOUND_GMAIL_STORAGE_PREFIX) :]
    return None


def _gmail_metadata_value(metadata: dict[str, Any], key: str) -> Any:
    if not metadata:
        return None
    value = metadata.get(key)
    if value not in (None, ""):
        return value
    source_metadata = metadata.get("source_metadata") if isinstance(metadata.get("source_metadata"), dict) else {}
    return source_metadata.get(key)


def _gmail_inbox_body(metadata: dict[str, Any]) -> str:
    if not metadata:
        return ""
    source_metadata = metadata.get("source_metadata") if isinstance(metadata.get("source_metadata"), dict) else {}
    return (
        metadata.get("notes")
        or metadata.get("body")
        or source_metadata.get("notes")
        or source_metadata.get("body")
        or metadata.get("snippet")
        or source_metadata.get("snippet")
        or ""
    )


def _gmail_storage_uri(message_id: str) -> str:
    return f"{INBOUND_GMAIL_STORAGE_PREFIX}{message_id}"


def _gmail_message_processed(session: Session, message_id: str) -> bool:
    storage_uri = _gmail_storage_uri(message_id)
    return bool(session.scalar(select(Document.id).where(Document.storage_uri == storage_uri)))


def _match_gmail_reply(
    session: Session,
    thread_id: str | None,
    subject: str,
    body: str,
    sender_email: str | None,
) -> tuple[Referral | None, str]:
    if thread_id:
        draft = session.scalar(
            select(CommunicationDraft)
            .where(CommunicationDraft.gmail_thread_id == thread_id)
            .order_by(CommunicationDraft.created_at.desc())
            .limit(1)
        )
        if draft and draft.referral_id:
            referral = session.get(Referral, draft.referral_id)
            if referral is not None:
                return referral, "thread_id"

    referral_id = _extract_referral_id(f"{subject}\n{body}")
    if referral_id:
        referral = session.get(Referral, referral_id)
        if referral is not None:
            return referral, "referral_id"

    if sender_email and _is_valid_email(sender_email) and _gmail_subject_looks_like_reply(subject):
        active_statuses = {
            "waiting_for_missing_info",
            "awaiting_patient_contact",
            "contact_sent",
            "awaiting_patient_reply",
        }
        draft = session.scalar(
            select(CommunicationDraft)
            .join(Referral, Referral.id == CommunicationDraft.referral_id)
            .where(func.lower(CommunicationDraft.recipient_email) == sender_email.lower())
            .where(CommunicationDraft.status == "sent")
            .where(Referral.status.in_(active_statuses))
            .order_by(CommunicationDraft.updated_at.desc(), CommunicationDraft.created_at.desc())
            .limit(1)
        )
        if draft and draft.referral_id:
            referral = session.get(Referral, draft.referral_id)
            if referral is not None:
                return referral, "sender_email_reply"

    return None, "unmatched"


def _gmail_subject_looks_like_reply(subject: str) -> bool:
    return bool(re.match(r"^\s*(?:re|fw|fwd)\s*:", str(subject or ""), re.I))


def _extract_referral_id(text: str) -> str | None:
    if not text:
        return None
    match = re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", text, re.I)
    return match.group(0) if match else None


def _extract_email_address(value: str) -> str | None:
    if not value:
        return None
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value)
    return match.group(0).lower() if match else None


def _is_missing_info_referral(referral: Referral) -> bool:
    status = canonical_referral_status(referral.status)
    return status in {"waiting_for_missing_info", "needs_admin_review"} or bool(referral.missing_fields)


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
    if str(referral.source_channel or "").strip().lower() == "email":
        raise ValueError("Email referrals use the canonical LangGraph workflow for patient email drafts.")
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
    slot_lines = []
    for index, appointment in enumerate(appointments[:3], start=1):
        code = _appointment_choice_code(appointment.id)
        slot_lines.append(
            f"- Option {index} (code {code}): {iso_or_none(appointment.starts_at)} to {iso_or_none(appointment.ends_at)}"
        )
    missing_lines = [f"- {field.replace('_', ' ')}" for field in (referral.missing_fields or [])]
    missing_section = ["", "Before we can confirm, please provide:", *missing_lines] if missing_lines else []
    note_lines = ["", "Clinic note:", note.strip()] if note.strip() else []
    body = "\n".join(
        [
            f"Hello {referral.patient_name or patient.display_name or 'there'},",
            "",
            "We have tentatively held the following appointment time based on current availability:",
            "",
            *slot_lines,
            *missing_section,
            *note_lines,
            "",
            "Please reply with the option number or code to confirm that you can attend this date and time.",
            "If this time does not work, reply with a few alternative times that do.",
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
        recipient_email=_outbound_patient_email(referral, patient),
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
    template = session.get(IntakeTemplate, template_id) if template_id else None
    if template is None:
        item_template_id = next((item.get("template_id") for item in workspace["items"] if item.get("template_id")), None)
        template = session.get(IntakeTemplate, item_template_id) if item_template_id else None
    if template is None and workspace.get("template"):
        template = session.get(IntakeTemplate, workspace["template"]["id"])
    attachment_state = _intake_template_attachment_state(session, template)
    item_lines = [f"- {item['label']}" for item in workspace["items"] if not _intake_done(item["status"])]
    consent_lines = [f"- {consent['scope'].replace('_', ' ')} consent" for consent in workspace["consents"] if not _intake_done(consent["status"])]
    attachment_lines = [
        f"- {item['item_label']}: {item['file_name']}"
        for item in attachment_state["outbound_attachment_manifest"]
    ]
    note_lines = ["", "Clinic note:", note.strip()] if note.strip() else []
    body = "\n".join(
        [
            f"Hello {referral.patient_name or patient.display_name or 'there'},",
            "",
            "Before your first session, please complete the attached intake files and reply to this same email thread with the completed files attached.",
            "You can attach the intake files directly as TXT, PDF, DOCX, CSV, XLSX, or JSON files.",
            "",
            "Attached blank files:",
            *(attachment_lines or ["- Clinic staff will provide any required blank files separately."]),
            "",
            "Required forms and documents:",
            *(item_lines or ["- No document checklist items are outstanding."]),
            "",
            "Required consents:",
            *(consent_lines or ["- No consent records are outstanding."]),
            *note_lines,
            "",
            "Once we receive the attachments, clinic staff will review them and confirm that your referral is ready for the first session.",
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
        recipient_email=_outbound_patient_email(referral, patient),
    )
    session.add(draft)
    session.flush()
    draft_payload = {
        **communication_draft_to_dict(draft),
        "intake_template_id": template.id if template else None,
        "outbound_attachment_manifest": attachment_state["outbound_attachment_manifest"],
        "missing_template_files": attachment_state["missing_template_files"],
        "sent_attachment_records": [],
    }
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
        source_payload=json_safe(draft_payload),
        draft_text=draft.body,
    )
    return draft_payload


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
    appointment_id: str | None = None,
    document_id: str | None = None,
    intake_item_id: str | None = None,
    consent_id: str | None = None,
    questionnaire_name: str | None = None,
) -> HumanReviewTask:
    task = session.get(HumanReviewTask, task_id)
    if task is None:
        raise KeyError(f"Unknown review task: {task_id}")

    before = review_task_to_dict(task)
    task.reviewer_id = reviewer_id if session.get(User, reviewer_id or "") else None
    task.reviewed_at = utc_now()
    task.updated_at = utc_now()

    referral = session.get(Referral, task.referral_id) if task.referral_id else None
    if action == "approve" and task.task_type == "inbound_reply_review":
        candidate_ids = _candidate_appointment_ids_for_task(task)
        if candidate_ids and not appointment_id:
            raise ValueError("Select the appointment slot that the patient accepted before approving this reply.")
        if appointment_id and candidate_ids and appointment_id not in candidate_ids:
            raise ValueError("Selected appointment is not one of the candidate slots for this reply.")

    if action == "approve" and referral and task.task_type == "send_approval":
        blocker = _slot_contact_send_blocker(session, task, referral)
        if blocker:
            _record_task_provider_failure(session, task, blocker)
            return task

    if action == "approve" and referral and google_workspace.is_enabled():
        if task.task_type in GMAIL_APPROVAL_TASK_TYPES:
            if not _prepare_google_send_approval(session, task, referral, final_text or task.draft_text):
                return task
        elif task.task_type == "appointment_confirmation_approval":
            if not _prepare_google_appointment_confirmation(session, task, referral):
                return task
        elif task.task_type == "appointment_reschedule_approval":
            if not _prepare_google_appointment_reschedule(session, task, referral):
                return task

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
        referral = referral or session.get(Referral, task.referral_id)
        if referral:
            if action == "approve" and task.task_type in GMAIL_APPROVAL_TASK_TYPES:
                _approve_patient_message_task(session, task, referral)
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
            elif action == "approve" and task.task_type == "inbound_reply_review" and appointment_id:
                _approve_inbound_reply_slot_resolution(session, task, referral, appointment_id=appointment_id)
            elif action == "approve" and task.task_type == "appointment_reschedule_approval":
                _approve_appointment_reschedule(session, task, referral)
            elif action == "approve" and task.task_type == "intake_exception_approval":
                _approve_intake_exception(session, task)
            elif action == "approve" and task.task_type == "intake_submission_review":
                _approve_intake_submission_review(
                    session,
                    task,
                    document_id=document_id,
                    intake_item_id=intake_item_id,
                    consent_id=consent_id,
                    questionnaire_name=questionnaire_name,
                )
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
    session.flush()
    return task


def _update_reviewed_draft(session: Session, task: HumanReviewTask, action: str) -> None:
    draft = _draft_for_review_task(session, task)
    if draft is None:
        return
    before = communication_draft_to_dict(draft)
    if action == "approve" and draft.status == "sent" and draft.gmail_message_id:
        if task.final_text:
            draft.body = task.final_text
        draft.updated_at = utc_now()
        write_audit(
            session,
            tenant_id=draft.tenant_id,
            actor_user_id=task.reviewer_id,
            action="draft_review_approve",
            entity_type="communication_draft",
            entity_id=draft.id,
            before=before,
            after=communication_draft_to_dict(draft),
        )
        return
    draft.status = "approved_pending_send" if action == "approve" else task.status
    if action == "approve" and task.final_text:
        draft.body = task.final_text
    draft.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=draft.tenant_id,
        actor_user_id=task.reviewer_id,
        action=f"draft_review_{action}",
        entity_type="communication_draft",
        entity_id=draft.id,
        before=before,
        after=communication_draft_to_dict(draft),
    )


def _approve_patient_message_task(session: Session, task: HumanReviewTask, referral: Referral) -> None:
    send_reason = (
        "approved and sent through Gmail."
        if google_workspace.is_enabled()
        else "approved for simulated/manual send."
    )
    if task.task_type == "missing_info_message_approval":
        transition_referral_status(
            session,
            referral,
            "waiting_for_missing_info",
            actor_user_id=task.reviewer_id,
            reason=f"Missing-information message {send_reason}",
        )
        return
    if task.task_type == "intake_reminder_approval":
        write_audit(
            session,
            tenant_id=task.tenant_id,
            actor_user_id=task.reviewer_id,
            action="intake_reminder_sent",
            entity_type="referral",
            entity_id=referral.id,
            after={"task_id": task.id, "reason": f"Intake reminder {send_reason}"},
        )
        return
    if task.payload_key.startswith("intake_packet_draft"):
        transition_referral_status(
            session,
            referral,
            "intake_packet_sent",
            actor_user_id=task.reviewer_id,
            reason=f"Intake packet {send_reason}",
        )
        return
    transition_referral_status(
        session,
        referral,
        "contact_sent",
        actor_user_id=task.reviewer_id,
        reason=f"Patient-facing contact draft {send_reason}",
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


def _approve_inbound_reply_slot_resolution(
    session: Session,
    task: HumanReviewTask,
    referral: Referral,
    *,
    appointment_id: str,
) -> None:
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    reply_text = str(payload.get("reply_text") or task.draft_text or "Patient accepted a proposed slot.").strip()
    source_metadata = payload.get("source_metadata") if isinstance(payload.get("source_metadata"), dict) else {}
    source_metadata = {
        **source_metadata,
        "resolved_from_task_id": task.id,
        "resolved_appointment_id": appointment_id,
    }
    acceptance = record_patient_slot_acceptance(
        session,
        referral.id,
        appointment_id=appointment_id,
        notes=reply_text,
        source_metadata=source_metadata,
        auto_approve=True,
        reviewer_id=task.reviewer_id,
    )
    task.source_payload = json_safe(
        {
            **payload,
            "resolved_appointment_id": appointment_id,
            "appointment_confirmation_task_id": (acceptance.get("task") or {}).get("id"),
            "auto_approved": bool(acceptance.get("auto_approved")),
        }
    )
    task.updated_at = utc_now()


def _candidate_appointment_ids_for_task(task: HumanReviewTask) -> list[str]:
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    ids = payload.get("candidate_appointment_ids")
    if not ids:
        ids = [
            item.get("appointment_id")
            for item in payload.get("candidate_appointments") or []
            if isinstance(item, dict)
        ]
    return [str(item) for item in ids or [] if str(item or "").strip()]


def _approve_appointment_reschedule(session: Session, task: HumanReviewTask, referral: Referral) -> None:
    appointment = _appointment_for_confirmation_task(session, task)
    if appointment is None:
        raise ValueError("Review task is not linked to an appointment.")
    starts_at, ends_at = _reschedule_window_from_task(task)
    if starts_at is None or ends_at is None:
        raise ValueError("Reschedule task is missing a valid proposed time.")
    if int((ends_at - starts_at).total_seconds() // 60) != SESSION_LENGTH_MINUTES:
        raise ValueError("Appointments must use the 60-minute Lumen session length.")
    if _appointment_conflicts(
        session,
        appointment.therapist_id or "",
        starts_at,
        ends_at,
        exclude_appointment_id=appointment.id,
    ):
        raise ValueError("Appointment reschedule conflicts with an existing proposed or confirmed slot.")
    if not _therapist_has_weekly_capacity(
        session,
        appointment.therapist_id,
        starts_at,
        ends_at,
        exclude_appointment_id=appointment.id,
    ):
        raise ValueError("Therapist weekly patient-contact cap would be exceeded.")
    before = appointment_to_dict(appointment)
    appointment.starts_at = starts_at
    appointment.ends_at = ends_at
    appointment.last_provider_error = None
    appointment.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=appointment.tenant_id,
        actor_user_id=task.reviewer_id,
        action="reschedule",
        entity_type="appointment",
        entity_id=appointment.id,
        before=before,
        after=appointment_to_dict(appointment),
    )
    transition_referral_status(
        session,
        referral,
        "appointment_confirmed",
        actor_user_id=task.reviewer_id,
        reason="Appointment reschedule approved.",
    )


def _prepare_google_send_approval(
    session: Session,
    task: HumanReviewTask,
    referral: Referral,
    final_text: str | None,
) -> bool:
    draft = _draft_for_review_task(session, task)
    if draft is None:
        _record_task_provider_failure(
            session,
            task,
            "Review task is not linked to a communication draft.",
        )
        return False

    patient = session.get(Patient, referral.patient_id or "") if referral.patient_id else None
    recipient_email = (_outbound_patient_email(referral, patient) or "").strip()
    if not _is_valid_email(recipient_email):
        error = "Recipient email is missing or invalid; Gmail send was not attempted."
        draft.last_provider_error = error
        _record_task_provider_failure(session, task, error)
        return False

    if final_text:
        draft.body = final_text
        task.draft_text = final_text
    draft.recipient_email = recipient_email
    draft.provider = "gmail"
    draft.updated_at = utc_now()

    if draft.gmail_message_id:
        draft.status = "sent"
        draft.sent_at = draft.sent_at or utc_now()
        draft.last_provider_error = None
        _clear_task_provider_error(task)
        return True

    send_attachments: list[dict[str, Any]] | None = None
    intake_manifest: list[dict[str, Any]] = []
    if _is_intake_packet_send_task(task):
        attachment_state = _intake_packet_attachment_state_for_task(session, task, referral)
        intake_manifest = attachment_state["outbound_attachment_manifest"]
        task.source_payload = json_safe(
            {
                **(task.source_payload if isinstance(task.source_payload, dict) else {}),
                "intake_template_id": attachment_state.get("template_id"),
                "outbound_attachment_manifest": intake_manifest,
                "missing_template_files": attachment_state["missing_template_files"],
                "sent_attachment_records": [],
            }
        )
        if attachment_state["missing_template_files"]:
            labels = ", ".join(item["item_label"] for item in attachment_state["missing_template_files"])
            error = f"Intake packet is missing required blank template files: {labels}. Upload the missing files and retry send approval."
            draft.last_provider_error = error
            draft.updated_at = utc_now()
            _record_task_provider_failure(session, task, error, entity_type="communication_draft", entity_id=draft.id)
            return False
        send_attachments, attachment_errors = _load_manifest_attachments(intake_manifest)
        if attachment_errors:
            labels = ", ".join(item["item_label"] for item in attachment_errors)
            error = f"Intake packet attachment files are missing from local storage: {labels}. Re-upload the missing files and retry send approval."
            draft.last_provider_error = error
            draft.updated_at = utc_now()
            task.source_payload = json_safe(
                {
                    **(task.source_payload if isinstance(task.source_payload, dict) else {}),
                    "missing_template_files": attachment_errors,
                }
            )
            _record_task_provider_failure(session, task, error, entity_type="communication_draft", entity_id=draft.id)
            return False

    before = communication_draft_to_dict(draft)
    try:
        result = google_workspace.send_approved_draft(
            recipient_email=recipient_email,
            subject=draft.subject,
            body=draft.body,
            attachments=send_attachments,
        )
        message_id = str(result.get("message_id") or "").strip()
        if not message_id:
            raise google_workspace.GoogleWorkspaceError("Gmail did not return a message ID.")
    except Exception as exc:
        error = google_workspace.provider_error_message(exc)
        draft.status = "draft_pending_review"
        draft.sent_at = None
        draft.gmail_message_id = None
        draft.gmail_thread_id = None
        draft.last_provider_error = error
        draft.updated_at = utc_now()
        _record_task_provider_failure(session, task, error, entity_type="communication_draft", entity_id=draft.id)
        return False

    draft.status = "sent"
    draft.sent_at = utc_now()
    draft.provider = "gmail"
    draft.gmail_message_id = message_id
    draft.gmail_thread_id = str(result.get("thread_id") or "").strip() or None
    draft.last_provider_error = None
    if _is_intake_packet_send_task(task):
        sent_at = utc_now()
        sent_records = [
            {
                **item,
                "gmail_message_id": message_id,
                "gmail_thread_id": draft.gmail_thread_id,
                "sent_at": iso_or_none(sent_at),
            }
            for item in intake_manifest
        ]
        task.source_payload = json_safe(
            {
                **(task.source_payload if isinstance(task.source_payload, dict) else {}),
                "outbound_attachment_manifest": intake_manifest,
                "missing_template_files": [],
                "sent_attachment_records": sent_records,
            }
        )
    _clear_task_provider_error(task)
    write_audit(
        session,
        tenant_id=draft.tenant_id,
        actor_user_id=task.reviewer_id,
        action="provider_send",
        entity_type="communication_draft",
        entity_id=draft.id,
        before=before,
        after=communication_draft_to_dict(draft),
    )
    return True


def _is_intake_packet_send_task(task: HumanReviewTask) -> bool:
    return task.task_type == "send_approval" and str(task.payload_key or "").startswith("intake_packet_draft")


def _normalized_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _intake_packet_draft_id_from_task(task: HumanReviewTask) -> str:
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    return str(payload.get("id") or "").strip()


def _is_intake_packet_draft(draft: CommunicationDraft, *, packet_task_draft_ids: set[str] | None = None) -> bool:
    if packet_task_draft_ids and draft.id in packet_task_draft_ids:
        return True
    subject = str(draft.subject or "").strip().lower()
    body = str(draft.body or "").strip().lower()
    return (
        "intake packet" in subject
        or "attached blank files" in body
        or "reply to this same email thread with the completed files attached" in body
    )


def _intake_packet_state(
    session: Session,
    referral: Referral,
    *,
    tasks: list[HumanReviewTask] | None = None,
    drafts: list[CommunicationDraft] | None = None,
) -> dict[str, Any]:
    if tasks is None:
        tasks = list(
            session.scalars(
                select(HumanReviewTask)
                .where(HumanReviewTask.referral_id == referral.id)
                .order_by(HumanReviewTask.created_at.desc())
            )
        )
    if drafts is None:
        drafts = list(
            session.scalars(
                select(CommunicationDraft)
                .where(CommunicationDraft.referral_id == referral.id)
                .order_by(CommunicationDraft.created_at.desc())
            )
        )

    draft_by_id = {draft.id: draft for draft in drafts}
    packet_tasks = [task for task in tasks if _is_intake_packet_send_task(task)]
    packet_task_draft_ids = {
        draft_id
        for draft_id in (_intake_packet_draft_id_from_task(task) for task in packet_tasks)
        if draft_id
    }
    packet_drafts = [
        draft
        for draft in drafts
        if _is_intake_packet_draft(draft, packet_task_draft_ids=packet_task_draft_ids)
    ]

    def draft_for_task(task: HumanReviewTask) -> CommunicationDraft | None:
        draft_id = _intake_packet_draft_id_from_task(task)
        return draft_by_id.get(draft_id) if draft_id else None

    latest_task = max(packet_tasks, key=lambda task: task.created_at, default=None)
    latest_draft = max(packet_drafts, key=lambda draft: draft.created_at, default=None)
    latest_draft_from_task = draft_for_task(latest_task) if latest_task else None

    def task_has_sent_evidence(task: HumanReviewTask) -> bool:
        payload = task.source_payload if isinstance(task.source_payload, dict) else {}
        if task.status == "approved":
            return True
        return bool(payload.get("sent_attachment_records"))

    def draft_has_sent_evidence(draft: CommunicationDraft) -> bool:
        return bool(
            draft.status in {"sent", "approved_pending_send"}
            or draft.gmail_message_id
            or draft.sent_at
        )

    sent_times = [
        sent_at
        for sent_at in (
            *(
                _normalized_utc_datetime(task.reviewed_at or task.updated_at or task.created_at)
                for task in packet_tasks
                if task_has_sent_evidence(task)
            ),
            *(
                _normalized_utc_datetime(draft.sent_at or draft.updated_at or draft.created_at)
                for draft in packet_drafts
                if draft_has_sent_evidence(draft)
            ),
        )
        if sent_at is not None
    ]
    packet_sent_at = max(sent_times, default=None)
    latest_is_pending = bool(
        (latest_task and latest_task.status in {"open", "changes_requested"})
        or (latest_draft_from_task and latest_draft_from_task.status in {"draft_pending_review", "changes_requested"})
        or (
            latest_draft
            and latest_draft.id not in packet_task_draft_ids
            and latest_draft.status in {"draft_pending_review", "changes_requested"}
        )
    )
    if latest_is_pending:
        state = "draft_pending_review"
    elif any(task_has_sent_evidence(task) for task in packet_tasks) or any(
        draft_has_sent_evidence(draft) for draft in packet_drafts
    ):
        state = "sent"
    else:
        state = "not_drafted"

    return {
        "state": state,
        "task_id": latest_task.id if latest_task else None,
        "task_status": latest_task.status if latest_task else None,
        "draft_id": (latest_draft_from_task or latest_draft).id if (latest_draft_from_task or latest_draft) else None,
        "draft_status": (latest_draft_from_task or latest_draft).status if (latest_draft_from_task or latest_draft) else None,
        "sent_at": iso_or_none(packet_sent_at),
    }


def _supersede_premature_intake_reminder_tasks(
    session: Session,
    referral: Referral,
    *,
    packet_state: dict[str, Any] | None = None,
    tasks: list[HumanReviewTask] | None = None,
    drafts: list[CommunicationDraft] | None = None,
) -> int:
    packet_state = packet_state or _intake_packet_state(session, referral, tasks=tasks, drafts=drafts)
    if tasks is None:
        tasks = list(
            session.scalars(
                select(HumanReviewTask)
                .where(
                    HumanReviewTask.referral_id == referral.id,
                    HumanReviewTask.task_type == "intake_reminder_approval",
                    HumanReviewTask.status == "open",
                )
                .order_by(HumanReviewTask.created_at.desc())
            )
        )
    if drafts is None:
        drafts = list(
            session.scalars(
                select(CommunicationDraft)
                .where(CommunicationDraft.referral_id == referral.id)
                .order_by(CommunicationDraft.created_at.desc())
            )
        )
    draft_by_id = {draft.id: draft for draft in drafts}
    open_reminder_tasks = [
        task
        for task in tasks
        if task.task_type == "intake_reminder_approval" and task.status == "open"
    ]
    if packet_state.get("state") == "sent":
        packet_sent_at = _normalized_utc_datetime(packet_state.get("sent_at"))
        if packet_sent_at is None:
            return 0
        open_reminder_tasks = [
            task
            for task in open_reminder_tasks
            if (
                task_created_at := _normalized_utc_datetime(task.created_at)
            ) is not None
            and task_created_at < packet_sent_at
        ]
    if not open_reminder_tasks:
        return 0

    reason = "Intake reminders cannot be sent until the intake packet has been sent."
    changed = 0
    for task in open_reminder_tasks:
        before = review_task_to_dict(task)
        task.status = "superseded"
        task.rejection_reason = reason
        task.reviewed_at = utc_now()
        task.updated_at = utc_now()
        write_audit(
            session,
            tenant_id=task.tenant_id,
            actor_user_id=task.reviewer_id,
            action="review_superseded",
            entity_type="human_review_task",
            entity_id=task.id,
            before=before,
            after=review_task_to_dict(task),
        )
        changed += 1

        payload = task.source_payload if isinstance(task.source_payload, dict) else {}
        draft_id = str(payload.get("id") or "").strip()
        draft = draft_by_id.get(draft_id) if draft_id else None
        if draft and draft.status == "draft_pending_review":
            draft_before = communication_draft_to_dict(draft)
            draft.status = "superseded"
            draft.last_provider_error = reason
            draft.updated_at = utc_now()
            write_audit(
                session,
                tenant_id=draft.tenant_id,
                actor_user_id=task.reviewer_id,
                action="draft_review_superseded",
                entity_type="communication_draft",
                entity_id=draft.id,
                before=draft_before,
                after=communication_draft_to_dict(draft),
            )
    session.flush()
    return changed


def _intake_packet_attachment_state_for_task(
    session: Session,
    task: HumanReviewTask,
    referral: Referral,
) -> dict[str, Any]:
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    template_id = str(payload.get("intake_template_id") or "").strip()
    template = session.get(IntakeTemplate, template_id) if template_id else None
    if template is None:
        item = session.scalar(
            select(IntakeChecklistItem)
            .where(IntakeChecklistItem.referral_id == referral.id, IntakeChecklistItem.template_id.is_not(None))
            .order_by(IntakeChecklistItem.created_at)
        )
        template = session.get(IntakeTemplate, item.template_id) if item and item.template_id else None
    if template is None:
        template = _select_intake_template(session, referral, None)
    return _intake_template_attachment_state(session, template)


def _load_manifest_attachments(
    manifest: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    attachments: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in manifest:
        path = _local_document_path(str(item.get("storage_uri") or ""))
        if path is None or not path.exists() or not path.is_file():
            errors.append(
                {
                    **item,
                    "required": True,
                    "reason": "The active template file is missing from local storage.",
                }
            )
            continue
        attachments.append(
            {
                "document_id": item.get("document_id"),
                "item_key": item.get("item_key"),
                "item_label": item.get("item_label"),
                "file_name": item.get("file_name") or path.name,
                "content_type": item.get("mime_type") or "application/octet-stream",
                "content": path.read_bytes(),
            }
        )
    return attachments, errors


def _prepare_google_appointment_confirmation(session: Session, task: HumanReviewTask, referral: Referral) -> bool:
    appointment = _appointment_for_confirmation_task(session, task)
    if appointment is None:
        _record_task_provider_failure(session, task, "Review task is not linked to an appointment.")
        return False
    if not appointment.starts_at or not appointment.ends_at:
        error = "Appointment has no proposed time; Google Calendar event was not created."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    if int((appointment.ends_at - appointment.starts_at).total_seconds() // 60) != SESSION_LENGTH_MINUTES:
        error = "Appointments must use the 60-minute Lumen session length."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    if _appointment_conflicts(
        session,
        appointment.therapist_id or "",
        appointment.starts_at,
        appointment.ends_at,
        exclude_appointment_id=appointment.id,
    ):
        error = "Appointment conflicts with an existing proposed or confirmed slot."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    if appointment.google_calendar_event_id:
        appointment.last_provider_error = None
        _clear_task_provider_error(task)
        return True
    if not _therapist_has_weekly_capacity(session, appointment.therapist_id, appointment.starts_at, appointment.ends_at, exclude_appointment_id=appointment.id):
        error = "Therapist weekly patient-contact cap would be exceeded."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    try:
        google_busy = _google_busy_window(appointment.starts_at, _with_session_buffer(appointment.ends_at))
    except Exception as exc:
        error = google_workspace.provider_error_message(exc)
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    if _overlaps_busy(appointment.starts_at, _with_session_buffer(appointment.ends_at), google_busy):
        error = "Appointment conflicts with Google Calendar busy time."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False

    patient = session.get(Patient, appointment.patient_id or "") if appointment.patient_id else None
    therapist = session.get(Therapist, appointment.therapist_id or "") if appointment.therapist_id else None
    patient_email = _outbound_patient_email(referral, patient)
    therapist_email = therapist.email.strip() if therapist and therapist.email else None
    if patient_email and not _is_valid_email(patient_email):
        patient_email = None
    if therapist_email and not _is_valid_email(therapist_email):
        therapist_email = None

    before = appointment_to_dict(appointment)
    try:
        result = google_workspace.create_appointment_event(
            appointment_id=appointment.id,
            tenant_id=appointment.tenant_id,
            referral_id=appointment.referral_id,
            starts_at=appointment.starts_at,
            ends_at=appointment.ends_at,
            patient_name=referral.patient_name or (patient.display_name if patient else None),
            therapist_name=therapist.name if therapist else None,
            therapist_id=therapist.id if therapist else None,
            patient_email=patient_email,
            therapist_email=therapist_email,
            calendar_id=appointment.google_calendar_id,
        )
        event_id = str(result.get("event_id") or "").strip()
        if not event_id:
            raise google_workspace.GoogleWorkspaceError("Google Calendar did not return an event ID.")
    except Exception as exc:
        error = google_workspace.provider_error_message(exc)
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False

    appointment.google_calendar_id = str(result.get("calendar_id") or google_workspace.settings().calendar_id)
    appointment.google_calendar_event_id = event_id
    appointment.google_calendar_event_link = str(result.get("event_link") or "").strip() or None
    appointment.google_calendar_synced_at = utc_now()
    appointment.last_provider_error = None
    _clear_task_provider_error(task)
    write_audit(
        session,
        tenant_id=appointment.tenant_id,
        actor_user_id=task.reviewer_id,
        action="provider_calendar_event_create",
        entity_type="appointment",
        entity_id=appointment.id,
        before=before,
        after=appointment_to_dict(appointment),
    )
    return True


def _prepare_google_appointment_reschedule(session: Session, task: HumanReviewTask, referral: Referral) -> bool:
    appointment = _appointment_for_confirmation_task(session, task)
    if appointment is None:
        _record_task_provider_failure(session, task, "Review task is not linked to an appointment.")
        return False
    starts_at, ends_at = _reschedule_window_from_task(task)
    if starts_at is None or ends_at is None:
        error = "Reschedule task is missing a valid proposed time."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    if int((ends_at - starts_at).total_seconds() // 60) != SESSION_LENGTH_MINUTES:
        error = "Appointments must use the 60-minute Lumen session length."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    if not appointment.google_calendar_event_id:
        error = "Appointment is missing its Google Calendar event ID; reschedule cannot sync."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    if _appointment_conflicts(
        session,
        appointment.therapist_id or "",
        starts_at,
        ends_at,
        exclude_appointment_id=appointment.id,
    ):
        error = "Appointment reschedule conflicts with an existing proposed or confirmed slot."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    if not _therapist_has_weekly_capacity(session, appointment.therapist_id, starts_at, ends_at, exclude_appointment_id=appointment.id):
        error = "Therapist weekly patient-contact cap would be exceeded."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    try:
        google_busy = _google_busy_window(starts_at, _with_session_buffer(ends_at))
    except Exception as exc:
        error = google_workspace.provider_error_message(exc)
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False
    if _overlaps_busy(starts_at, _with_session_buffer(ends_at), google_busy):
        error = "Reschedule conflicts with Google Calendar busy time."
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False

    patient = session.get(Patient, appointment.patient_id or "") if appointment.patient_id else None
    therapist = session.get(Therapist, appointment.therapist_id or "") if appointment.therapist_id else None
    patient_email = _outbound_patient_email(referral, patient)
    therapist_email = therapist.email.strip() if therapist and therapist.email else None
    if patient_email and not _is_valid_email(patient_email):
        patient_email = None
    if therapist_email and not _is_valid_email(therapist_email):
        therapist_email = None

    before = appointment_to_dict(appointment)
    try:
        result = google_workspace.update_appointment_event(
            event_id=appointment.google_calendar_event_id,
            appointment_id=appointment.id,
            tenant_id=appointment.tenant_id,
            referral_id=appointment.referral_id,
            starts_at=starts_at,
            ends_at=ends_at,
            patient_name=referral.patient_name or (patient.display_name if patient else None),
            therapist_name=therapist.name if therapist else None,
            therapist_id=therapist.id if therapist else None,
            patient_email=patient_email,
            therapist_email=therapist_email,
            calendar_id=appointment.google_calendar_id,
        )
    except Exception as exc:
        error = google_workspace.provider_error_message(exc)
        appointment.last_provider_error = error
        _record_task_provider_failure(session, task, error, entity_type="appointment", entity_id=appointment.id)
        return False

    appointment.google_calendar_id = str(result.get("calendar_id") or google_workspace.settings().calendar_id)
    appointment.google_calendar_event_id = str(result.get("event_id") or appointment.google_calendar_event_id)
    appointment.google_calendar_event_link = str(result.get("event_link") or appointment.google_calendar_event_link or "").strip() or None
    appointment.google_calendar_synced_at = utc_now()
    appointment.last_provider_error = None
    _clear_task_provider_error(task)
    write_audit(
        session,
        tenant_id=appointment.tenant_id,
        actor_user_id=task.reviewer_id,
        action="provider_calendar_event_update",
        entity_type="appointment",
        entity_id=appointment.id,
        before=before,
        after=appointment_to_dict(appointment),
    )
    return True


def _draft_for_review_task(session: Session, task: HumanReviewTask) -> CommunicationDraft | None:
    draft_id = (task.source_payload or {}).get("id") if isinstance(task.source_payload, dict) else None
    if draft_id:
        draft = session.get(CommunicationDraft, draft_id)
        if draft is not None:
            return draft
    if not task.workflow_run_id and not task.referral_id:
        return None
    query = select(CommunicationDraft).order_by(CommunicationDraft.created_at.desc()).limit(1)
    if task.workflow_run_id:
        query = query.where(CommunicationDraft.workflow_run_id == task.workflow_run_id)
    if task.referral_id:
        query = query.where(CommunicationDraft.referral_id == task.referral_id)
    draft = session.scalar(query)
    if draft is not None:
        payload = task.source_payload if isinstance(task.source_payload, dict) else {}
        task.source_payload = json_safe({**payload, **communication_draft_to_dict(draft)})
        task.updated_at = utc_now()
    return draft


def _slot_contact_send_blocker(session: Session, task: HumanReviewTask, referral: Referral) -> str | None:
    draft = _draft_for_review_task(session, task)
    if draft is None or not draft.proposed_slots:
        return None
    is_canonical_email_draft = str(referral.source_channel or "").strip().lower() == "email" and bool(draft.workflow_run_id)
    match_approved = bool(
        session.scalar(
            select(HumanReviewTask.id).where(
                HumanReviewTask.referral_id == referral.id,
                HumanReviewTask.task_type == "match_approval",
                HumanReviewTask.status == "approved",
            )
        )
    )
    slot_approved = bool(
        session.scalar(
            select(HumanReviewTask.id).where(
                HumanReviewTask.referral_id == referral.id,
                HumanReviewTask.task_type == "slot_offer_approval",
                HumanReviewTask.status == "approved",
            )
        )
    )
    if not match_approved:
        return "Patient contact cannot be sent until the therapist match is approved."
    if is_canonical_email_draft:
        return None
    if not slot_approved:
        return "Patient contact cannot be sent until the held appointment slot is approved."
    return None


def _appointment_for_confirmation_task(session: Session, task: HumanReviewTask) -> Appointment | None:
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    appointment_id = str(payload.get("appointment_id") or "").strip()
    return session.get(Appointment, appointment_id) if appointment_id else None


def _record_task_provider_failure(
    session: Session,
    task: HumanReviewTask,
    error: str,
    *,
    entity_type: str = "human_review_task",
    entity_id: str | None = None,
) -> None:
    task.status = "open"
    task.reviewed_at = None
    task.rejection_reason = None
    task.source_payload = {
        **(task.source_payload if isinstance(task.source_payload, dict) else {}),
        "provider_error": error,
    }
    task.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=task.tenant_id,
        actor_user_id=task.reviewer_id,
        action="provider_failure",
        entity_type=entity_type,
        entity_id=entity_id or task.id,
        before=None,
        after={"task_id": task.id, "provider_error": error},
    )


def _clear_task_provider_error(task: HumanReviewTask) -> None:
    if not isinstance(task.source_payload, dict) or "provider_error" not in task.source_payload:
        return
    payload = dict(task.source_payload)
    payload.pop("provider_error", None)
    task.source_payload = payload


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


def _approve_intake_submission_review(
    session: Session,
    task: HumanReviewTask,
    *,
    document_id: str | None,
    intake_item_id: str | None,
    consent_id: str | None,
    questionnaire_name: str | None,
) -> None:
    referral = session.get(Referral, task.referral_id) if task.referral_id else None
    if referral is None:
        raise KeyError("Intake submission review task is not linked to a referral.")
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    candidate_document_id = (
        document_id
        or payload.get("document_id")
        or next(iter(payload.get("document_ids") or []), None)
    )
    if not candidate_document_id:
        task.source_payload = json_safe(
            {
                **payload,
                "review_outcome": "reviewed_without_usable_attachment",
                "reviewed_at": iso_or_none(utc_now()),
            }
        )
        return
    if not intake_item_id and not consent_id:
        raise ValueError("Select an intake item, consent record, or questionnaire before approving this submission.")

    document = session.get(Document, str(candidate_document_id))
    if document is None:
        raise KeyError("Selected intake document was not found.")
    metadata = document.metadata_json or {}
    if metadata.get("referral_id") != referral.id:
        raise ValueError("Selected intake document does not belong to this referral.")

    completed: list[dict[str, str]] = []
    if intake_item_id:
        item = _complete_intake_item_from_document(session, referral, document, intake_item_id)
        completed.append({"type": "intake_item", "id": item.id, "label": item.label})
        if item.item_type == "questionnaire":
            _maybe_save_questionnaire_from_document(session, referral, document, item, questionnaire_name)
    if consent_id:
        consent = _complete_consent_from_document(session, referral, document, consent_id)
        completed.append({"type": "consent", "id": consent.id, "scope": consent.scope})

    before_document = document_to_dict(document)
    document.metadata_json = json_safe(
        {
            **metadata,
            "linked_intake_item_id": intake_item_id,
            "linked_consent_id": consent_id,
            "questionnaire_name": questionnaire_name,
            "review_task_id": task.id,
            "review_outcome": "approved",
        }
    )
    document.updated_at = utc_now()
    task.source_payload = json_safe(
        {
            **payload,
            "selected_document_id": document.id,
            "selected_intake_item_id": intake_item_id,
            "selected_consent_id": consent_id,
            "questionnaire_name": questionnaire_name,
            "completed": completed,
        }
    )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        actor_user_id=task.reviewer_id,
        action="map_intake_submission",
        entity_type="document",
        entity_id=document.id,
        before=before_document,
        after=document_to_dict(document),
    )
    _refresh_referral_intake_status(session, referral.id)


def _complete_intake_item_from_document(
    session: Session,
    referral: Referral,
    document: Document,
    item_id: str,
) -> IntakeChecklistItem:
    item = session.get(IntakeChecklistItem, item_id)
    if item is None:
        raise KeyError("Selected intake checklist item was not found.")
    if item.tenant_id != referral.tenant_id or item.referral_id != referral.id:
        raise ValueError("Selected intake item does not belong to this referral.")
    before = intake_item_to_dict(item)
    item.source_document_id = document.id
    item.status = "completed"
    item.completed_at = utc_now()
    item.notes = f"Completed from intake submission: {document.title}"
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
    return item


def _complete_consent_from_document(
    session: Session,
    referral: Referral,
    document: Document,
    consent_id: str,
) -> ConsentRecord:
    consent = session.get(ConsentRecord, consent_id)
    if consent is None:
        raise KeyError("Selected consent record was not found.")
    if consent.tenant_id != referral.tenant_id or consent.patient_id != referral.patient_id:
        raise ValueError("Selected consent record does not belong to this referral.")
    before = consent_record_to_dict(consent)
    consent.status = "completed"
    consent.source_document_id = document.id
    consent.updated_at = utc_now()
    scope = _normal(consent.scope)
    for item in session.scalars(
        select(IntakeChecklistItem).where(
            IntakeChecklistItem.tenant_id == referral.tenant_id,
            IntakeChecklistItem.referral_id == referral.id,
            IntakeChecklistItem.patient_id == consent.patient_id,
            IntakeChecklistItem.item_type == "consent",
        )
    ):
        if _intake_done(item.status):
            continue
        item_key = _normal(item.item_key)
        item_label = _normal(item.label)
        if scope and (scope in item_key or scope in item_label or item_key in scope):
            item.source_document_id = document.id
            item.status = "completed"
            item.completed_at = utc_now()
            item.notes = f"Completed from consent submission: {document.title}"
            item.updated_at = utc_now()
    write_audit(
        session,
        tenant_id=consent.tenant_id,
        action="complete_by_upload",
        entity_type="consent_record",
        entity_id=consent.id,
        before=before,
        after=consent_record_to_dict(consent),
    )
    return consent


def _maybe_save_questionnaire_from_document(
    session: Session,
    referral: Referral,
    document: Document,
    item: IntakeChecklistItem,
    questionnaire_name: str | None,
) -> None:
    metadata = document.metadata_json or {}
    extracted_text = str(metadata.get("extracted_text") or "").strip()
    if not extracted_text:
        return
    try:
        payload = json.loads(extracted_text)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else None
    if answers is None and all(isinstance(key, str) for key in payload.keys()):
        answers = payload
    if not isinstance(answers, dict) or not answers:
        return
    name = str(questionnaire_name or payload.get("questionnaire_name") or payload.get("name") or item.item_key).strip()
    save_questionnaire_response(session, referral.id, name or "intake_questionnaire", answers)


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


def appointment_options_for_workflow(
    session: Session,
    tenant_id: str,
    raw_input: dict[str, Any],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Read-only appointment candidates for LangGraph context.

    This intentionally does not create appointments, drafts, review tasks, or
    referral status transitions. It only exposes bounded scheduling facts that
    agents can reference in their match rationale and communication draft.
    """

    raw_text = str((raw_input or {}).get("raw_text") or "")
    availability = _patient_availability_constraints(raw_text)
    search_start = utc_now()
    search_end = search_start + timedelta(days=29)
    google_busy = _google_busy_intervals_for_slot_proposal(search_start, search_end) if google_workspace.is_enabled() else []
    options: list[dict[str, Any]] = []
    therapists = list(
        session.scalars(
            select(Therapist)
            .where(Therapist.tenant_id == tenant_id, Therapist.active.is_(True))
            .order_by(Therapist.name)
        )
    )
    for therapist in therapists:
        for starts_at, ends_at, block in _generate_slots(therapist.availability_blocks, limit * 8):
            if len(options) >= limit:
                return options
            if availability and not _slot_matches_patient_availability(starts_at, ends_at, availability):
                continue
            if _appointment_conflicts(session, therapist.id, starts_at, ends_at):
                continue
            if _overlaps_busy(starts_at, _with_session_buffer(ends_at), google_busy):
                continue
            if not _therapist_has_weekly_capacity(session, therapist.id, starts_at, ends_at):
                continue
            option_number = len(options) + 1
            options.append(
                {
                    "slot_id": _candidate_slot_id(therapist.id, starts_at),
                    "option_code": f"OPT{option_number}",
                    "option_number": option_number,
                    "therapist_id": therapist.id,
                    "therapist_name": therapist.name,
                    "therapist_email": therapist.email,
                    "starts_at": iso_or_none(starts_at),
                    "ends_at": iso_or_none(ends_at),
                    "weekday": starts_at.strftime("%A"),
                    "modality": block.get("modality") or "online",
                    "source": f"availability:{block.get('weekday', 'manual')}",
                }
            )
    return options


def _candidate_slot_id(therapist_id: str, starts_at: datetime) -> str:
    return f"{therapist_id}:{starts_at.strftime('%Y%m%dT%H%M%SZ')}"


def deterministic_match_for_referral(
    session: Session,
    referral_id: str,
    *,
    allow_noncritical_missing: bool = False,
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    if str(referral.source_channel or "").strip().lower() == "email":
        raise ValueError("Email referrals use the canonical LangGraph workflow for matching.")
    blocking_missing = _matching_blocking_missing_fields(referral) if allow_noncritical_missing else list(referral.missing_fields or [])
    if blocking_missing:
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
    if included and not _review_task_exists(session, referral.id, "match_approval", statuses=("open", "approved")):
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
    availability_text: str | None = None,
) -> list[dict[str, Any]]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    if str(referral.source_channel or "").strip().lower() == "email":
        raise ValueError("Email referrals use the canonical LangGraph workflow for appointment proposals.")
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

    search_start = utc_now()
    search_end = search_start + timedelta(days=29)
    google_busy = _google_busy_intervals_for_slot_proposal(search_start, search_end) if google_workspace.is_enabled() else []
    availability_source = "\n".join(part for part in [referral.raw_text or "", availability_text or ""] if part.strip())
    availability = _patient_availability_constraints(availability_source)
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
        if (
            availability
            and appointment.starts_at
            and appointment.ends_at
            and not _slot_matches_patient_availability(appointment.starts_at, appointment.ends_at, availability)
        ):
            continue
        if appointment.starts_at and appointment.ends_at and _overlaps_busy(appointment.starts_at, _with_session_buffer(appointment.ends_at), google_busy):
            continue
        if appointment.starts_at and appointment.ends_at and not _therapist_has_weekly_capacity(
            session,
            therapist.id,
            appointment.starts_at,
            appointment.ends_at,
            exclude_appointment_id=appointment.id,
        ):
            continue
        proposals.append(appointment_to_dict(appointment))
        if len(proposals) >= limit:
            break

    if len(proposals) < limit:
        for starts_at, ends_at, block in _generate_slots(therapist.availability_blocks, limit * 16):
            if len(proposals) >= limit:
                break
            if availability and not _slot_matches_patient_availability(starts_at, ends_at, availability):
                continue
            if _appointment_conflicts(session, therapist.id, starts_at, ends_at):
                continue
            if _overlaps_busy(starts_at, _with_session_buffer(ends_at), google_busy):
                continue
            if not _therapist_has_weekly_capacity(session, therapist.id, starts_at, ends_at):
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
        if not _review_task_exists(session, referral.id, "slot_offer_approval", statuses=("open", "approved")):
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


def list_patients_for_therapist(session: Session, therapist_id: str) -> list[dict[str, Any]]:
    appointments = session.scalars(
        select(Appointment)
        .where(
            Appointment.therapist_id == therapist_id,
            Appointment.status != "cancelled",
        )
        .order_by(Appointment.starts_at.asc(), Appointment.created_at.asc())
    )
    patient_ids: list[str] = []
    seen_patient_ids: set[str] = set()
    for appointment in appointments:
        patient_id = appointment.patient_id
        if patient_id is None and appointment.referral_id:
            referral = session.get(Referral, appointment.referral_id)
            patient_id = referral.patient_id if referral is not None else None
        if patient_id and patient_id not in seen_patient_ids:
            patient_ids.append(patient_id)
            seen_patient_ids.add(patient_id)

    if not patient_ids:
        return []

    patients = session.scalars(select(Patient).where(Patient.id.in_(patient_ids)))
    patients_by_id = {patient.id: patient for patient in patients}
    return [patient_to_dict(patients_by_id[patient_id]) for patient_id in patient_ids if patient_id in patients_by_id]


def therapist_calendar_capacity(session: Session, tenant_id: str | None = None) -> dict[str, Any]:
    therapists = list(session.scalars(select(Therapist).where(Therapist.tenant_id == tenant_id).order_by(Therapist.name))) if tenant_id else list(
        session.scalars(select(Therapist).order_by(Therapist.name))
    )
    window_start = utc_now()
    window_end = window_start + timedelta(days=14)
    google_enabled = google_workspace.is_enabled()
    busy_periods: list[dict[str, Any]] = []
    lumen_events: list[dict[str, Any]] = []
    provider_error: str | None = None

    if google_enabled:
        try:
            busy_periods = [
                {
                    "start": iso_or_none(item.get("start")),
                    "end": iso_or_none(item.get("end")),
                    "source": "google_calendar",
                    "summary": "Google Calendar busy block",
                }
                for item in google_workspace.query_calendar_busy(time_min=window_start, time_max=window_end)
            ]
            try:
                lumen_events = google_workspace.list_lumen_appointment_events(time_min=window_start, time_max=window_end)
            except Exception:
                lumen_events = []
        except Exception as exc:
            provider_error = google_workspace.provider_error_message(exc)

    event_appointment_ids = {
        str(event.get("lumen_appointment_id"))
        for event in lumen_events
        if event.get("lumen_appointment_id")
    }
    local_query = select(Appointment).where(Appointment.status.in_(["proposed", "confirmed"]))
    if tenant_id:
        local_query = local_query.where(Appointment.tenant_id == tenant_id)
    local_appointment_ids = {appointment.id for appointment in session.scalars(local_query)}
    unmatched_events = [
        _calendar_event_to_dict(event)
        for event in lumen_events
        if event.get("lumen_appointment_id") and str(event.get("lumen_appointment_id")) not in local_appointment_ids
    ]
    malformed_events = [
        _calendar_event_to_dict(event)
        for event in lumen_events
        if not event.get("lumen_appointment_id") or not event.get("lumen_therapist_id")
    ]

    return {
        "tenant_id": tenant_id,
        "window_start": iso_or_none(window_start),
        "window_end": iso_or_none(window_end),
        "google_enabled": google_enabled,
        "provider_error": provider_error,
        "therapists": [
            _therapist_calendar_capacity_summary(
                session,
                therapist,
                busy_periods=busy_periods,
                google_enabled=google_enabled,
                provider_error=provider_error,
                event_appointment_ids=event_appointment_ids,
            )
            for therapist in therapists
        ],
        "unmatched_calendar_events": unmatched_events,
        "malformed_calendar_events": malformed_events,
    }


def create_manual_appointment_proposal(
    session: Session,
    *,
    referral_id: str,
    therapist_id: str,
    starts_at: datetime,
    ends_at: datetime | None = None,
) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    therapist = session.get(Therapist, therapist_id)
    if therapist is None:
        raise KeyError(f"Unknown therapist: {therapist_id}")
    if therapist.tenant_id != referral.tenant_id:
        raise ValueError("Therapist and referral tenants do not match.")
    starts_at = _aware_utc(starts_at)
    ends_at = _aware_utc(ends_at) if ends_at else starts_at + timedelta(minutes=SESSION_LENGTH_MINUTES)
    _validate_appointment_window(session, therapist.id, starts_at, ends_at)

    appointment = Appointment(
        tenant_id=referral.tenant_id,
        patient_id=referral.patient_id,
        therapist_id=therapist.id,
        referral_id=referral.id,
        starts_at=starts_at,
        ends_at=ends_at,
        status="proposed",
        source="therapist_calendar_drag_drop",
    )
    session.add(appointment)
    session.flush()
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="create",
        entity_type="appointment",
        entity_id=appointment.id,
        after=appointment_to_dict(appointment),
    )
    transition_referral_status(
        session,
        referral,
        "slot_options_ready",
        reason="Appointment slot proposed from therapist calendar.",
    )
    task = create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=referral.id,
        patient_id=referral.patient_id,
        task_type="slot_offer_approval",
        reason="Proposed appointment slot requires admin approval before being offered to the patient.",
        payload_key=f"slot_option:{appointment.id[:8]}",
        source_payload={"appointments": [appointment_to_dict(appointment)]},
    )
    return {"appointment": appointment_to_dict(appointment), "task": review_task_to_dict(task)}


def request_appointment_reschedule(
    session: Session,
    *,
    appointment_id: str,
    starts_at: datetime,
    ends_at: datetime | None = None,
    reason: str = "Appointment reschedule requires admin approval.",
) -> HumanReviewTask:
    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        raise KeyError(f"Unknown appointment: {appointment_id}")
    if not appointment.referral_id:
        raise ValueError("Appointment is not linked to a referral.")
    referral = session.get(Referral, appointment.referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {appointment.referral_id}")
    starts_at = _aware_utc(starts_at)
    ends_at = _aware_utc(ends_at) if ends_at else starts_at + timedelta(minutes=SESSION_LENGTH_MINUTES)
    _validate_appointment_window(
        session,
        appointment.therapist_id,
        starts_at,
        ends_at,
        exclude_appointment_id=appointment.id,
    )
    return create_review_task(
        session,
        tenant_id=appointment.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=appointment.referral_id,
        patient_id=appointment.patient_id,
        task_type="appointment_reschedule_approval",
        reason=reason,
        payload_key=f"appointment_reschedule:{appointment.id[:8]}",
        source_payload={
            "appointment_id": appointment.id,
            "proposed_starts_at": iso_or_none(starts_at),
            "proposed_ends_at": iso_or_none(ends_at),
            "reason": reason,
        },
        draft_text=reason,
    )


def confirm_appointment(session: Session, appointment_id: str) -> dict[str, Any]:
    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        raise KeyError(f"Unknown appointment: {appointment_id}")
    if not appointment.starts_at or not appointment.ends_at:
        raise ValueError("Appointment has no proposed time.")
    if int((appointment.ends_at - appointment.starts_at).total_seconds() // 60) != SESSION_LENGTH_MINUTES:
        raise ValueError("Appointments must use the 60-minute Lumen session length.")
    if google_workspace.is_enabled() and not appointment.google_calendar_event_id:
        raise ValueError("Google Calendar event creation is required before local appointment confirmation.")
    if _appointment_conflicts(
        session,
        appointment.therapist_id or "",
        appointment.starts_at,
        appointment.ends_at,
        exclude_appointment_id=appointment.id,
    ):
        raise ValueError("Appointment conflicts with an existing proposed or confirmed slot.")
    if not _therapist_has_weekly_capacity(
        session,
        appointment.therapist_id,
        appointment.starts_at,
        appointment.ends_at,
        exclude_appointment_id=appointment.id,
    ):
        raise ValueError("Therapist weekly patient-contact cap would be exceeded.")
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
    return [
        intake_template_to_dict(template, attachment_state=_intake_template_attachment_state(session, template))
        for template in session.scalars(query)
    ]


def create_intake_template_file(
    session: Session,
    *,
    template_id: str,
    item_key: str,
    title: str,
    storage_uri: str,
    metadata: dict[str, Any],
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    template = session.get(IntakeTemplate, template_id)
    if template is None:
        raise KeyError(f"Unknown intake template: {template_id}")
    spec = _intake_template_item_spec(template, item_key)
    if spec is None:
        raise ValueError("Intake template item was not found.")
    key = _intake_template_item_key(spec)
    previous_document_id: str | None = None
    for document in _intake_template_file_documents(session, template):
        document_metadata = dict(document.metadata_json or {})
        if document_metadata.get("item_key") != key or not document_metadata.get("active"):
            continue
        previous_document_id = document.id
        document.metadata_json = json_safe(
            {
                **document_metadata,
                "active": False,
                "replaced_at": iso_or_none(utc_now()),
            }
        )
        document.updated_at = utc_now()

    file_name = str(metadata.get("file_name") or title or "intake-template-file").strip()
    document = Document(
        tenant_id=template.tenant_id,
        patient_id=None,
        document_type=INTAKE_TEMPLATE_FILE_DOCUMENT_TYPE,
        title=file_name,
        storage_uri=storage_uri,
        metadata_json=json_safe(
            {
                **metadata,
                "template_id": template.id,
                "template_name": template.name,
                "item_key": key,
                "item_label": str(spec.get("label") or key.replace("_", " ").title()),
                "item_type": str(spec.get("type") or "form"),
                "filename": file_name,
                "mime_type": metadata.get("content_type") or metadata.get("mime_type"),
                "checksum": metadata.get("sha256") or metadata.get("checksum"),
                "storage_path": storage_uri,
                "active": True,
                "previous_document_id": previous_document_id,
            }
        ),
    )
    session.add(document)
    session.flush()
    write_audit(
        session,
        tenant_id=template.tenant_id,
        actor_user_id=actor_user_id,
        action="upload_intake_template_file",
        entity_type="document",
        entity_id=document.id,
        before={"previous_document_id": previous_document_id} if previous_document_id else None,
        after=document_to_dict(document),
    )
    return document_to_dict(document)


def document_download_info(session: Session, document_id: str) -> dict[str, Any]:
    document = session.get(Document, document_id)
    if document is None:
        raise KeyError(f"Unknown document: {document_id}")
    path = _local_document_path(document.storage_uri)
    if path is None:
        raise ValueError("Document is not backed by a downloadable local file.")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Document file is missing from local storage.")
    metadata = document.metadata_json or {}
    return {
        "document": document_to_dict(document),
        "path": path,
        "file_name": str(metadata.get("file_name") or metadata.get("filename") or document.title or "document"),
        "media_type": str(metadata.get("content_type") or metadata.get("mime_type") or "application/octet-stream"),
    }


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

    _refresh_referral_intake_status(session, referral.id)
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
    item_template_id = next((item.template_id for item in items if item.template_id), None)
    if item_template_id:
        template = session.get(IntakeTemplate, item_template_id) or template
    attachment_state = _intake_template_attachment_state(session, template)
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
    review_tasks = list(
        session.scalars(
            select(HumanReviewTask)
            .where(HumanReviewTask.referral_id == referral.id)
            .order_by(HumanReviewTask.created_at.desc())
        )
    )
    consents = []
    patient_files: list[dict[str, Any]] = []
    if referral.patient_id:
        consents = list(
            session.scalars(
                select(ConsentRecord)
                .where(ConsentRecord.tenant_id == referral.tenant_id, ConsentRecord.patient_id == referral.patient_id)
                .order_by(ConsentRecord.scope)
            )
        )
        patient_documents = list(
            session.scalars(
                select(Document)
                .where(Document.tenant_id == referral.tenant_id, Document.patient_id == referral.patient_id)
                .order_by(Document.created_at.desc())
            )
        )
        documents = [document for document in patient_documents if (document.metadata_json or {}).get("referral_id") == referral.id]
        source_document_ids = {
            source_id
            for source_id in [
                *(item.source_document_id for item in items if _intake_done(item.status)),
                *(consent.source_document_id for consent in consents if _intake_done(consent.status)),
            ]
            if source_id
        }
        patient_files = _intake_patient_files(
            [document for document in patient_documents if document.id in source_document_ids],
            items,
            consents,
        )
    packet_state = _intake_packet_state(session, referral, tasks=review_tasks, drafts=drafts)
    if _supersede_premature_intake_reminder_tasks(
        session,
        referral,
        packet_state=packet_state,
        tasks=review_tasks,
        drafts=drafts,
    ):
        packet_state = _intake_packet_state(session, referral, tasks=review_tasks, drafts=drafts)
    status = _intake_status(items, consents)
    return {
        "referral": referral_summary(referral),
        "template": intake_template_to_dict(template, attachment_state=attachment_state) if template else None,
        "outbound_attachment_manifest": attachment_state["outbound_attachment_manifest"],
        "missing_template_files": attachment_state["missing_template_files"],
        "intake_packet_state": packet_state["state"],
        "intake_packet": packet_state,
        "can_draft_intake_packet": packet_state["state"] == "not_drafted",
        "can_draft_intake_reminder": packet_state["state"] == "sent" and status not in {"not_started", "complete"},
        "items": [intake_item_to_dict(item) for item in items],
        "consents": [consent_record_to_dict(consent) for consent in consents],
        "questionnaires": [questionnaire_response_to_dict(response) for response in responses],
        "documents": [document_to_dict(document) for document in documents],
        "patient_files": patient_files,
        "communication_drafts": [_intake_communication_draft_to_dict(session, draft) for draft in drafts],
        "intake_submission_reviews": [
            review_task_to_dict(task)
            for task in review_tasks
            if task.task_type == "intake_submission_review"
        ],
        "prep_briefs": [prep_brief_to_dict(brief) for brief in briefs],
        "status": status,
    }


def _intake_patient_files(
    documents: list[Document],
    items: list[IntakeChecklistItem],
    consents: list[ConsentRecord],
) -> list[dict[str, Any]]:
    item_labels_by_document: dict[str, list[str]] = {}
    consent_labels_by_document: dict[str, list[str]] = {}
    for item in items:
        if not item.source_document_id or not _intake_done(item.status):
            continue
        item_labels_by_document.setdefault(item.source_document_id, []).append(item.label)
    for consent in consents:
        if not consent.source_document_id or not _intake_done(consent.status):
            continue
        consent_labels_by_document.setdefault(consent.source_document_id, []).append(consent.scope.replace("_", " "))

    files: list[dict[str, Any]] = []
    for document in documents:
        if document.document_type == INTAKE_TEMPLATE_FILE_DOCUMENT_TYPE:
            continue
        if document.id not in item_labels_by_document and document.id not in consent_labels_by_document:
            continue
        path = _local_document_path(document.storage_uri)
        if path is None or not path.exists() or not path.is_file():
            continue
        metadata = document.metadata_json or {}
        file_name = str(metadata.get("file_name") or metadata.get("filename") or document.title)
        item_labels = item_labels_by_document.get(document.id, [])
        consent_labels = consent_labels_by_document.get(document.id, [])
        files.append(
            {
                "id": document.id,
                "document_id": document.id,
                "download_id": document.id,
                "download_url": f"/api/documents/{document.id}/download",
                "title": document.title,
                "display_name": file_name or document.title,
                "file_name": file_name,
                "document_type": document.document_type,
                "intake_item_labels": item_labels,
                "consent_labels": consent_labels,
                "matched_labels": [*item_labels, *consent_labels],
                "size_bytes": metadata.get("size_bytes"),
                "mime_type": metadata.get("content_type") or metadata.get("mime_type") or "application/octet-stream",
                "metadata": json_safe(metadata),
                "uploaded_at": iso_or_none(document.created_at),
            }
        )
    return files


def generate_missing_intake_reminder(session: Session, referral_id: str) -> dict[str, Any]:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    patient = _ensure_patient_for_referral(session, referral)
    workspace = intake_workspace(session, referral_id)
    if workspace.get("intake_packet_state") != "sent":
        raise ValueError("Send the intake packet before drafting an intake reminder.")
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
        recipient_email=_outbound_patient_email(referral, patient),
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
    consents = []
    documents: list[Document] = []
    if referral.patient_id:
        consents = list(
            session.scalars(
                select(ConsentRecord).where(
                    ConsentRecord.tenant_id == referral.tenant_id,
                    ConsentRecord.patient_id == referral.patient_id,
                )
            )
        )
        source_document_ids = {
            source_id
            for source_id in [
                *(item.source_document_id for item in items if _intake_done(item.status)),
                *(consent.source_document_id for consent in consents if _intake_done(consent.status)),
            ]
            if source_id
        }
        documents = [
            document
            for document in session.scalars(
                select(Document)
                .where(Document.tenant_id == referral.tenant_id, Document.patient_id == referral.patient_id)
                .order_by(Document.created_at.desc())
            )
            if document.id in source_document_ids or (document.metadata_json or {}).get("referral_id") == referral.id
        ]
    missing_items = [item.label for item in items if not _intake_done(item.status)]
    completed_items = [item.label for item in items if _intake_done(item.status)]
    missing_fields = [str(field).replace("_", " ") for field in referral.missing_fields or []]
    confirmed_appointment = next((appointment for appointment in appointments if appointment.status == "confirmed"), None)
    intake_status = _intake_status(items, consents)
    readiness_blockers = _pre_prep_readiness_blockers(session, referral)
    appointment_summary = (
        f"{iso_or_none(confirmed_appointment.starts_at)} to {iso_or_none(confirmed_appointment.ends_at)}"
        if confirmed_appointment and confirmed_appointment.starts_at
        else "No confirmed appointment recorded."
    )
    document_titles = [document.title for document in documents[:6]]
    raw_summary = " ".join((referral.raw_text or "").strip().split())
    if len(raw_summary) > 420:
        raw_summary = f"{raw_summary[:417]}..."
    questionnaire_scores = [
        f"{response.questionnaire_name}: {response.score_summary.get('total_score', 0)}"
        for response in responses
    ]
    open_items = [
        *missing_fields,
        *missing_items,
        *readiness_blockers,
    ]
    if not open_items:
        open_items = ["No open readiness blockers recorded."]
    lines = [
        f"# Therapist Prep Brief: {referral.patient_name or patient.display_name or 'Referral'}",
        "",
        "## Patient Snapshot",
        f"- Patient: {referral.patient_name or patient.display_name or patient.id}",
        f"- Contact: {referral.contact_email or patient.contact_email or 'not recorded'} / {referral.contact_phone or patient.contact_phone or 'not recorded'}",
        f"- Date of birth: {referral.date_of_birth or patient.date_of_birth or 'not recorded'}",
        f"- Source: {referral.source_channel or 'not recorded'}; referrer: {referral.referring_entity or 'not recorded'}",
        f"- Insurance: {referral.insurer or 'not recorded'}",
        f"- Language and modality: {referral.language_preference or patient.language or 'not recorded'} / {referral.modality_preference or 'not recorded'}",
        "",
        "## Appointment And Readiness",
        f"- Confirmed appointment: {appointment_summary}",
        f"- Current workflow status: {referral.status}",
        f"- Intake status: {intake_status.replace('_', ' ')}",
        "",
        "## Risk And Safety",
        f"- Risk category: {referral.risk_category or 'pending'}",
        f"- Urgency: {referral.urgency or 'pending'}",
        f"- Elevated risk signal: {'yes' if referral.risk_present else 'no'}",
        "",
        "## Intake And Documents",
        f"- Completed or waived: {', '.join(completed_items) if completed_items else 'none recorded'}",
        f"- Outstanding: {', '.join(missing_items) if missing_items else 'none recorded'}",
        f"- Patient files reviewed: {', '.join(document_titles) if document_titles else 'none recorded'}",
        "",
        "## Questionnaire Scores",
        f"- {', '.join(questionnaire_scores) if questionnaire_scores else 'No questionnaire scores recorded.'}",
        "",
        "## First-session Focus",
        "- Confirm the presenting concern, goals, and practical barriers in the patient's own words.",
        "- Review the completed intake and clarify any missing or ambiguous history.",
        "- Recheck current risk, protective factors, and support plan at the start of session.",
        "- Agree initial treatment priorities and the next administrative or clinical step.",
        "",
        "## Open Items",
        *[f"- {item}" for item in open_items],
    ]
    if raw_summary:
        lines.extend(["", "## Referral Note", raw_summary])
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
            "document_count": len(documents),
            "intake_status": intake_status,
        },
    )
    session.add(brief)
    session.flush()
    if not _maybe_mark_first_session_ready(session, referral):
        prep_blockers = _pre_prep_readiness_blockers(session, referral)
        if not prep_blockers:
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


def _trusted_referral_dedupe_candidates(
    session: Session,
    referral: Referral,
    candidates: Any,
    *,
    existing: list[str] | None = None,
    workflow_run_id: str | None = None,
) -> list[str]:
    if candidates is None:
        candidates = existing or []
    valid: list[str] = []
    discarded: list[str] = []
    for raw_candidate in candidates or []:
        candidate_id = str(raw_candidate or "").strip()
        if not candidate_id:
            continue
        if candidate_id == referral.id:
            discarded.append(candidate_id)
            continue
        candidate = session.get(Referral, candidate_id)
        if candidate is None or candidate.tenant_id != referral.tenant_id:
            discarded.append(candidate_id)
            continue
        valid.append(candidate_id)
    valid = list(dict.fromkeys(valid))
    if discarded:
        write_audit(
            session,
            tenant_id=referral.tenant_id,
            action="discard_untrusted_dedupe_candidates",
            entity_type="referral",
            entity_id=referral.id,
            after={
                "workflow_run_id": workflow_run_id,
                "discarded_candidates": list(dict.fromkeys(discarded)),
                "trusted_candidates": valid,
            },
        )
    return valid


def update_referral_from_result(session: Session, run: WorkflowRun) -> None:
    referral = session.get(Referral, run.referral_id)
    if referral is None:
        return
    before = referral_summary(referral)
    result = run.result or {}
    outputs = result.get("outputs") or {}
    raw_input = (run.request_payload or {}).get("raw_input") if isinstance(run.request_payload, dict) else {}
    source_channel = str((raw_input or {}).get("source_channel") or referral.source_channel or "").strip().lower()
    raw_text = str((raw_input or {}).get("raw_text") or referral.raw_text or "")

    referral.status = _referral_status_from_result(result, run.status, run.approvals)
    referral.workflow_run_id = run.id
    referral.updated_at = utc_now()

    referral_output, cleanup_missing = _clean_demo_extracted_referral_output(
        session,
        referral,
        outputs.get("referral") or {},
        raw_text=raw_text,
        source_channel=source_channel,
        workflow_run_id=run.id,
    )
    referral.patient_name = referral_output.get("patient_name") or referral.patient_name
    referral.date_of_birth = referral_output.get("date_of_birth") or referral.date_of_birth
    referral.contact_email = referral_output.get("contact_email") or referral.contact_email
    referral.contact_phone = referral_output.get("contact_phone") or referral.contact_phone
    referral.insurer = referral_output.get("insurer") or referral.insurer
    referral.referring_entity = referral_output.get("referring_entity") or referral.referring_entity
    referral.duplicate_candidates = _trusted_referral_dedupe_candidates(
        session,
        referral,
        referral_output.get("dedupe_candidates"),
        existing=referral.duplicate_candidates,
        workflow_run_id=run.id,
    )

    signals = outputs.get("clinical_signals") or {}
    referral.language_preference = signals.get("language_preference") or referral.language_preference
    referral.modality_preference = signals.get("modality_preference") or referral.modality_preference
    referral.missing_fields = _merge_missing_fields(
        referral.missing_fields,
        [*(signals.get("missing_required_fields") or []), *cleanup_missing],
    )
    _normalise_email_optional_contact_missing_fields(session, referral)

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
            recipient_email=_outbound_patient_email(referral, session.get(Patient, run.patient_id or "") if run.patient_id else None),
        )
        session.add(draft)
        session.flush()
        if source_channel == "email":
            _materialize_agent_draft_slots(session, referral, draft, raw_input=raw_input or {})
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


def _materialize_agent_draft_slots(
    session: Session,
    referral: Referral,
    draft: CommunicationDraft,
    *,
    raw_input: dict[str, Any],
) -> None:
    options = [item for item in (raw_input.get("appointment_options") or []) if isinstance(item, dict)]
    if not options:
        return
    selected = _selected_agent_slot_options(options, draft.proposed_slots or [], draft.body)
    if not selected:
        draft.proposed_slots = []
        return

    appointment_ids: list[str] = []
    appointments_by_id: dict[str, Appointment] = {}
    patient = _ensure_patient_for_referral(session, referral)
    for option in selected:
        therapist_id = str(option.get("therapist_id") or "").strip()
        starts_at = _parse_iso_datetime(option.get("starts_at"))
        ends_at = _parse_iso_datetime(option.get("ends_at"))
        if not therapist_id or starts_at is None or ends_at is None:
            continue
        appointment = session.scalar(
            select(Appointment)
            .where(
                Appointment.referral_id == referral.id,
                Appointment.therapist_id == therapist_id,
                Appointment.starts_at == starts_at,
                Appointment.ends_at == ends_at,
                Appointment.status.in_(["proposed", "confirmed"]),
            )
            .limit(1)
        )
        if appointment is None:
            appointment = Appointment(
                tenant_id=referral.tenant_id,
                patient_id=patient.id,
                therapist_id=therapist_id,
                referral_id=referral.id,
                starts_at=starts_at,
                ends_at=ends_at,
                status="proposed",
                source="langgraph_candidate",
            )
            session.add(appointment)
            session.flush()
            write_audit(
                session,
                tenant_id=referral.tenant_id,
                action="create",
                entity_type="appointment",
                entity_id=appointment.id,
                after=appointment_to_dict(appointment),
            )
        appointment_ids.append(appointment.id)
        appointments_by_id[appointment.id] = appointment

    ordered_ids = list(dict.fromkeys(appointment_ids))
    ordered_appointments = [appointments_by_id[item] for item in ordered_ids if item in appointments_by_id]
    ordered_appointments.sort(key=lambda item: item.starts_at or utc_now())
    draft.proposed_slots = [appointment.id for appointment in ordered_appointments]
    _normalise_email_slot_offer_body(session, referral, draft, ordered_appointments)
    draft.updated_at = utc_now()


def _selected_agent_slot_options(
    options: list[dict[str, Any]],
    proposed_slots: list[str],
    body: str,
) -> list[dict[str, Any]]:
    references = " ".join([str(item) for item in proposed_slots] + [str(body or "")]).lower()
    selected = []
    for option in options:
        tokens = {
            str(option.get("slot_id") or "").lower(),
            str(option.get("option_code") or "").lower(),
            f"option {option.get('option_number')}".lower(),
        }
        tokens = {token for token in tokens if token and token != "option none"}
        if any(token in references for token in tokens):
            selected.append(option)
    return selected


def _normalise_email_slot_offer_body(
    session: Session,
    referral: Referral,
    draft: CommunicationDraft,
    appointments: list[Appointment],
) -> None:
    if not appointments:
        return
    patient_name = referral.patient_name or "there"
    lines = [
        f"Dear {patient_name},",
        "",
        "We can offer the following appointment options:",
        "",
    ]
    for index, appointment in enumerate(appointments, start=1):
        therapist = session.get(Therapist, appointment.therapist_id or "") if appointment.therapist_id else None
        therapist_name = therapist.name if therapist else "your therapist"
        starts = appointment.starts_at
        if starts:
            when = f"{starts.strftime('%A')}, {starts.date().isoformat()} at {starts.strftime('%H:%M')}"
        else:
            when = "time to be confirmed"
        lines.append(f"Option {index}: {therapist_name}, {when}.")
    missing = [_missing_field_label(field) for field in referral.missing_fields or []]
    if missing:
        lines.extend(
            [
                "",
                "Please also reply with:",
                *[f"- {field}" for field in missing],
            ]
        )
    lines.extend(
        [
            "",
            "Please reply with one option number, for example: Option 1.",
            "",
            "Best regards,",
            "Lumen Patient Communication & Scheduling Team",
        ]
    )
    draft.body = "\n".join(lines)


def _missing_field_label(field: str) -> str:
    labels = {
        "date_of_birth": "date of birth",
        "dob": "date of birth",
        "contact_phone": "phone number",
        "contact_phone_or_date_of_birth": "phone number or date of birth",
        "contact_email": "email address",
        "insurer": "insurer",
        "insurance": "insurer",
        "referring_entity": "referrer",
        "patient_name": "patient name",
    }
    return labels.get(str(field or ""), str(field or "").replace("_", " "))


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def persist_human_review_tasks(session: Session, run: WorkflowRun) -> None:
    result = run.result or {}
    outputs = result.get("outputs") or {}
    for item in result.get("human_review_queue") or []:
        task_type = item.get("gate") or "admin_review"
        if task_type == "clinical_review":
            task_type = "clinical_risk_review"
        payload_key = item.get("payload_key") or "workflow"
        source_payload = outputs.get(payload_key) or item
        if task_type == "send_approval":
            source_payload = _linked_communication_draft_payload(session, run, source_payload)
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


def _linked_communication_draft_payload(session: Session, run: WorkflowRun, source_payload: Any) -> Any:
    if isinstance(source_payload, dict) and source_payload.get("id"):
        return source_payload
    draft = session.scalar(
        select(CommunicationDraft)
        .where(
            CommunicationDraft.tenant_id == run.tenant_id,
            CommunicationDraft.workflow_run_id == run.id,
            CommunicationDraft.referral_id == run.referral_id,
        )
        .order_by(CommunicationDraft.created_at.desc())
        .limit(1)
    )
    if draft is None and run.referral_id:
        referral = session.get(Referral, run.referral_id)
        if referral and referral.communication_draft_id:
            draft = session.get(CommunicationDraft, referral.communication_draft_id)
    if draft is None:
        return source_payload
    payload = communication_draft_to_dict(draft)
    if isinstance(source_payload, dict):
        payload.update({key: value for key, value in source_payload.items() if key not in payload or payload[key] in (None, "", [])})
    return payload


def approval_payload_for_task(session: Session, task: HumanReviewTask) -> dict[str, Any] | None:
    if not task.workflow_run_id:
        return None
    if task.task_type == "match_approval" and task.referral_id:
        referral = session.get(Referral, task.referral_id)
        if referral and referral.source_channel == "email" and _existing_slot_contact_draft(session, referral):
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


def _referral_journey_card(
    session: Session,
    referral: Referral,
    open_tasks: list[HumanReviewTask],
) -> dict[str, Any]:
    summary = referral_summary(referral)
    next_action, next_action_display = _task_aware_next_action(summary, open_tasks)
    blockers = _journey_blockers(session, referral, open_tasks)
    blocker_codes = [blocker["code"] for blocker in blockers]
    summary["next_action"] = next_action
    summary["next_action_label"] = next_action_display
    summary["stage_id"] = REFERRAL_STAGE_BY_STATUS.get(summary["status"], "triage")
    summary["open_review_task_count"] = len(open_tasks)
    summary["blockers"] = blockers
    summary["blocker_codes"] = blocker_codes
    summary["secondary_flags"] = list(dict.fromkeys([*(summary.get("secondary_flags") or []), *blocker_codes]))
    return summary


def _task_aware_next_action(
    referral_data: dict[str, Any],
    open_tasks: list[HumanReviewTask],
) -> tuple[str, str]:
    if not open_tasks:
        return referral_data["next_action"], referral_data["next_action_label"]

    priority = {task_type: index for index, task_type in enumerate(REVIEW_TASK_PRIORITY)}
    task = sorted(open_tasks, key=lambda item: priority.get(item.task_type, len(priority)))[0]
    action, label = REVIEW_TASK_NEXT_ACTIONS.get(
        task.task_type,
        ("review_referral", f"Review {task.task_type.replace('_', ' ')}"),
    )
    if task.task_type == "send_approval":
        payload_key = str(task.payload_key or "")
        if payload_key.startswith("intake_packet_draft"):
            return "approve_contact", "Approve intake packet"
        if payload_key.startswith("intake_reminder"):
            return "complete_intake", "Approve intake reminder"
        return "approve_contact", "Approve patient contact"
    return action, label


def _journey_blockers(
    session: Session,
    referral: Referral,
    open_tasks: list[HumanReviewTask],
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    status = canonical_referral_status(referral.status)
    flags = set(secondary_flags_for_referral(referral))
    missing_fields = [str(field).replace("_", " ") for field in referral.missing_fields or []]

    if missing_fields:
        _append_blocker(blockers, "missing_info", f"Missing: {', '.join(missing_fields)}", "warning")
    elif flags & {"missing_contact", "missing_dob", "insurance_unclear"}:
        _append_blocker(blockers, "missing_info", "Missing or unclear referral details", "warning")
    if "duplicate_candidate" in flags:
        _append_blocker(blockers, "duplicate_candidate", "Duplicate candidate", "warning")
    if flags & {"risk_unknown", "risk_elevated"}:
        _append_blocker(blockers, "risk_review", "Risk review needed", "danger")
    if status in {"needs_clinical_review", "clinical_escalation_review"} or any(
        task.task_type in {"clinical_risk_review", "suitability_review"} for task in open_tasks
    ):
        _append_blocker(blockers, "clinical_escalation", "Clinical review pending", "danger")
    if status in {"contact_sent", "awaiting_patient_reply"}:
        _append_blocker(blockers, "awaiting_patient_reply", "Awaiting patient reply", "info")
    if _patient_question_pending(session, referral):
        _append_blocker(blockers, "patient_question_pending", "Patient question pending", "warning")

    items, consents = _intake_requirements_for_referral(session, referral)
    intake_status = _intake_status(items, consents)
    intake_related = bool(items) or status in {
        "intake_packet_sent",
        "intake_incomplete",
        "intake_complete",
        "prep_brief_ready",
        "first_session_ready",
    }
    if intake_related:
        if intake_status not in {"not_started", "complete"}:
            _append_blocker(blockers, "intake_incomplete", "Intake incomplete", "warning")
        if any(item.status == "waived" for item in items) or any(consent.status == "waived" for consent in consents):
            _append_blocker(blockers, "intake_exception_recorded", "Intake exception recorded", "info")

    if _calendar_conflict_for_referral(session, referral):
        _append_blocker(blockers, "calendar_conflict", "Calendar conflict", "danger")
    if _calendar_sync_issue_for_referral(session, referral):
        _append_blocker(blockers, "calendar_sync_issue", "Calendar sync issue", "danger")
    provider_error = _provider_error_for_referral(session, referral)
    if provider_error:
        _append_blocker(blockers, "provider_error", provider_error, "danger")

    for task in open_tasks:
        _append_blocker(blockers, f"review_{task.task_type}", f"Open review: {task.task_type.replace('_', ' ')}", "warning")

    return blockers


def _append_blocker(blockers: list[dict[str, str]], code: str, label: str, severity: str) -> None:
    if any(blocker["code"] == code for blocker in blockers):
        return
    blockers.append({"code": code, "label": label, "severity": severity})


def _intake_requirements_for_referral(
    session: Session,
    referral: Referral,
) -> tuple[list[IntakeChecklistItem], list[ConsentRecord]]:
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
    return items, consents


def _patient_question_pending(session: Session, referral: Referral) -> bool:
    if not referral.patient_id:
        return False
    documents = [
        document
        for document in session.scalars(
            select(Document)
            .where(
                Document.tenant_id == referral.tenant_id,
                Document.patient_id == referral.patient_id,
                Document.document_type == "patient_reply",
            )
            .order_by(Document.created_at.desc())
        )
        if (document.metadata_json or {}).get("referral_id") == referral.id
    ]
    if not documents:
        return False
    reply_type = str((documents[0].metadata_json or {}).get("reply_type") or "")
    return reply_type in {"asked_question", "unclear", "alternative_requested"}


def _calendar_conflict_for_referral(session: Session, referral: Referral) -> bool:
    appointments = list(
        session.scalars(
            select(Appointment).where(
                Appointment.referral_id == referral.id,
                Appointment.status.in_(["proposed", "confirmed"]),
            )
        )
    )
    for appointment in appointments:
        if not appointment.therapist_id or not appointment.starts_at or not appointment.ends_at:
            continue
        conflict = session.scalar(
            select(Appointment)
            .where(
                Appointment.id != appointment.id,
                Appointment.therapist_id == appointment.therapist_id,
                Appointment.status.in_(["proposed", "confirmed"]),
                Appointment.starts_at < appointment.ends_at,
                Appointment.ends_at > appointment.starts_at,
            )
            .limit(1)
        )
        if conflict is not None:
            return True
    return False


def _calendar_sync_issue_for_referral(session: Session, referral: Referral) -> bool:
    if not google_workspace.is_enabled():
        return False
    return (
        session.scalar(
            select(Appointment)
            .where(
                Appointment.referral_id == referral.id,
                Appointment.status == "confirmed",
                Appointment.source != DEMO_STAGE_SEED_SOURCE,
                Appointment.google_calendar_event_id.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def _provider_error_for_referral(session: Session, referral: Referral) -> str | None:
    draft = session.scalar(
        select(CommunicationDraft)
        .where(
            CommunicationDraft.referral_id == referral.id,
            CommunicationDraft.last_provider_error.is_not(None),
        )
        .order_by(CommunicationDraft.updated_at.desc())
        .limit(1)
    )
    if draft and draft.last_provider_error:
        return draft.last_provider_error
    appointment = session.scalar(
        select(Appointment)
        .where(
            Appointment.referral_id == referral.id,
            Appointment.last_provider_error.is_not(None),
        )
        .order_by(Appointment.updated_at.desc())
        .limit(1)
    )
    return appointment.last_provider_error if appointment and appointment.last_provider_error else None


def _journey_card_needs_action(card: dict[str, Any]) -> bool:
    if card["status"] == "first_session_ready":
        return False
    if card["next_action"] in {"wait_patient_reply", "ready", "closed"}:
        return False
    return True


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
        "provider_error": (task.source_payload or {}).get("provider_error") if isinstance(task.source_payload, dict) else None,
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
        "recipient_email": draft.recipient_email,
        "sent_at": iso_or_none(draft.sent_at),
        "provider": draft.provider,
        "gmail_message_id": draft.gmail_message_id,
        "gmail_thread_id": draft.gmail_thread_id,
        "last_provider_error": draft.last_provider_error,
        "created_at": iso_or_none(draft.created_at),
        "updated_at": iso_or_none(draft.updated_at),
    }


def _intake_communication_draft_to_dict(session: Session, draft: CommunicationDraft) -> dict[str, Any]:
    data = communication_draft_to_dict(draft)
    if not draft.referral_id:
        return data
    tasks = list(
        session.scalars(
            select(HumanReviewTask)
            .where(
                HumanReviewTask.referral_id == draft.referral_id,
                HumanReviewTask.task_type.in_(["send_approval", "intake_reminder_approval"]),
            )
            .order_by(HumanReviewTask.created_at.desc())
        )
    )
    for task in tasks:
        payload = task.source_payload if isinstance(task.source_payload, dict) else {}
        if payload.get("id") != draft.id:
            continue
        for key in (
            "outbound_attachment_manifest",
            "missing_template_files",
            "sent_attachment_records",
            "provider_error",
        ):
            if key in payload:
                data[key] = json_safe(payload.get(key))
        break
    return data


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
    calendar_sync_issue = bool(
        google_workspace.is_enabled()
        and appointment.status == "confirmed"
        and appointment.source != DEMO_STAGE_SEED_SOURCE
        and not appointment.google_calendar_event_id
    )
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
        "google_calendar_id": appointment.google_calendar_id,
        "google_calendar_event_id": appointment.google_calendar_event_id,
        "google_calendar_event_link": appointment.google_calendar_event_link,
        "google_calendar_synced_at": iso_or_none(appointment.google_calendar_synced_at),
        "last_provider_error": appointment.last_provider_error,
        "calendar_sync_issue": calendar_sync_issue,
        "created_at": iso_or_none(appointment.created_at),
        "updated_at": iso_or_none(appointment.updated_at),
    }


def intake_template_to_dict(
    template: IntakeTemplate,
    *,
    attachment_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required_items = json_safe(template.required_items)
    if attachment_state:
        files_by_key = {
            str(item.get("item_key") or ""): item
            for item in attachment_state.get("template_files", [])
            if isinstance(item, dict)
        }
        enriched_items = []
        for item in required_items:
            if not isinstance(item, dict):
                enriched_items.append(item)
                continue
            key = str(item.get("key") or item.get("label") or "").strip()
            enriched_items.append({**item, "template_file": files_by_key.get(key)})
        required_items = enriched_items
    return {
        "id": template.id,
        "tenant_id": template.tenant_id,
        "name": template.name,
        "patient_type": template.patient_type,
        "insurer": template.insurer,
        "age_band": template.age_band,
        "modality": template.modality,
        "source_channel": template.source_channel,
        "required_items": required_items,
        "questionnaire_schema": json_safe(template.questionnaire_schema),
        "active": template.active,
        "template_files": json_safe((attachment_state or {}).get("template_files", [])),
        "missing_template_files": json_safe((attachment_state or {}).get("missing_template_files", [])),
        "outbound_attachment_manifest": json_safe((attachment_state or {}).get("outbound_attachment_manifest", [])),
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
    week_start, week_end = _week_bounds(utc_now())
    active_minutes = _therapist_patient_contact_minutes(
        session,
        therapist.id,
        week_start,
        week_end,
        statuses=("proposed", "confirmed"),
    )
    active_hours = round(active_minutes / 60, 2)
    if active_minutes >= THERAPIST_WEEKLY_PATIENT_CONTACT_CAP_HOURS * 60:
        exclusions.append("weekly patient-contact cap is full")

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
        "capacity_used_this_week": active_hours,
        "capacity_per_week": THERAPIST_WEEKLY_PATIENT_CONTACT_CAP_HOURS,
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
    if not blocks:
        return slots
    for day_offset in range(1, 29):
        candidate_date = (now + timedelta(days=day_offset)).date()
        for block in blocks:
            weekday = weekday_map.get(str(block.get("weekday") or "").strip().lower())
            if weekday is None or candidate_date.weekday() != weekday:
                continue
            block_start = datetime.combine(candidate_date, _parse_time(block.get("start")) or time(8, 0), tzinfo=timezone.utc)
            block_end = datetime.combine(candidate_date, _parse_time(block.get("end")) or time(21, 0), tzinfo=timezone.utc)
            starts_at = block_start
            while starts_at + timedelta(minutes=SESSION_LENGTH_MINUTES) <= block_end:
                ends_at = starts_at + timedelta(minutes=SESSION_LENGTH_MINUTES)
                slots.append((starts_at, ends_at, block))
                if len(slots) >= max_candidates:
                    return slots
                starts_at = _with_session_buffer(ends_at)
    return slots


def _patient_availability_constraints(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").lower()
    if not text.strip():
        return None
    weekdays = _extract_weekday_constraints(text)
    windows = _extract_time_windows(text)
    all_day_weekdays = _extract_all_day_weekday_constraints(text)
    if not weekdays and not windows and not all_day_weekdays:
        return None
    return {"weekdays": sorted(weekdays), "windows": windows, "all_day_weekdays": sorted(all_day_weekdays)}


def _extract_weekday_constraints(text: str) -> set[int]:
    day_map = {
        "monday": 0,
        "mon": 0,
        "tuesday": 1,
        "tue": 1,
        "wednesday": 2,
        "wed": 2,
        "thursday": 3,
        "thu": 3,
        "friday": 4,
        "fri": 4,
        "saturday": 5,
        "sat": 5,
        "sunday": 6,
        "sun": 6,
        "segunda": 0,
        "terca": 1,
        "quarta": 2,
        "quinta": 3,
        "sexta": 4,
        "sabado": 5,
        "domingo": 6,
    }
    tokens = re.findall(r"[a-z]+", text)
    days = {day_map[token] for token in tokens if token in day_map}
    if "weekday" in text or "weekdays" in text:
        days.update({0, 1, 2, 3, 4})
    if "weekend" in text or "weekends" in text:
        days.update({5, 6})
    return days


def _extract_time_windows(text: str) -> list[tuple[int, int]]:
    normalized = re.sub(r"(\d{1,2})h(\d{2})?", lambda m: f"{m.group(1)}:{m.group(2) or '00'}", text)
    windows = []
    keywords = {
        "morning": (8 * 60, 12 * 60),
        "afternoon": (12 * 60, 17 * 60),
        "evening": (17 * 60, 21 * 60),
        "manha": (8 * 60, 12 * 60),
        "tarde": (12 * 60, 17 * 60),
        "noite": (17 * 60, 21 * 60),
    }
    for key, window in keywords.items():
        if key in normalized:
            windows.append(window)

    range_pattern = re.compile(
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|until|through|thru|ate|as)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
    )
    for match in range_pattern.finditer(normalized):
        start_ampm = match.group(3)
        end_ampm = match.group(6) or start_ampm
        start_minutes = _parse_clock_value(match.group(1), match.group(2), start_ampm)
        end_minutes = _parse_clock_value(match.group(4), match.group(5), end_ampm)
        if start_minutes is None or end_minutes is None:
            continue
        if end_minutes <= start_minutes:
            continue
        windows.append((start_minutes, end_minutes))

    deduped = list(dict.fromkeys(windows))
    return deduped


def _extract_all_day_weekday_constraints(text: str) -> set[int]:
    day_map = {
        "monday": 0,
        "mon": 0,
        "tuesday": 1,
        "tue": 1,
        "wednesday": 2,
        "wed": 2,
        "thursday": 3,
        "thu": 3,
        "friday": 4,
        "fri": 4,
        "saturday": 5,
        "sat": 5,
        "sunday": 6,
        "sun": 6,
        "segunda": 0,
        "terca": 1,
        "quarta": 2,
        "quinta": 3,
        "sexta": 4,
        "sabado": 5,
        "domingo": 6,
    }
    days: set[int] = set()
    for match in re.finditer(r"\ball\s+day\s+(?:on\s+)?([a-z,\s]+)", text):
        segment = re.split(r"\b(?:for|from|at|before|after|appointment|session)\b", match.group(1), maxsplit=1)[0]
        days.update(day_map[token] for token in re.findall(r"[a-z]+", segment) if token in day_map)
    return days


def _parse_clock_value(hour_text: str, minute_text: str | None, ampm: str | None) -> int | None:
    try:
        hour = int(hour_text)
        minute = int(minute_text or 0)
    except ValueError:
        return None
    if hour < 0 or hour > 24 or minute < 0 or minute >= 60:
        return None
    if ampm:
        ampm_clean = ampm.lower()
        if ampm_clean == "pm" and hour < 12:
            hour += 12
        if ampm_clean == "am" and hour == 12:
            hour = 0
    if hour == 24:
        hour = 0
    return hour * 60 + minute


def _slot_matches_patient_availability(
    starts_at: datetime,
    ends_at: datetime,
    availability: dict[str, Any] | None,
) -> bool:
    if not availability:
        return True
    weekdays = availability.get("weekdays") or []
    if weekdays and starts_at.weekday() not in weekdays:
        return False
    all_day_weekdays = availability.get("all_day_weekdays") or []
    if starts_at.weekday() in all_day_weekdays:
        return True
    windows = availability.get("windows") or []
    if not windows:
        return True
    start_minutes = starts_at.hour * 60 + starts_at.minute
    end_minutes = ends_at.hour * 60 + ends_at.minute
    for window_start, window_end in windows:
        if start_minutes >= window_start and end_minutes <= window_end:
            return True
    return False


def _appointment_conflicts(
    session: Session,
    therapist_id: str,
    starts_at: datetime,
    ends_at: datetime,
    exclude_appointment_id: str | None = None,
) -> bool:
    if not therapist_id:
        return False
    conflict_start = starts_at
    conflict_end = _with_session_buffer(ends_at)
    query = select(Appointment).where(
        Appointment.therapist_id == therapist_id,
        Appointment.status.in_(["proposed", "confirmed"]),
        Appointment.starts_at < conflict_end,
        Appointment.ends_at > conflict_start - timedelta(minutes=SESSION_BUFFER_MINUTES),
    )
    if exclude_appointment_id:
        query = query.where(Appointment.id != exclude_appointment_id)
    return session.scalar(query.limit(1)) is not None


def _google_busy_intervals_for_slot_proposal(
    time_min: datetime | None = None,
    time_max: datetime | None = None,
) -> list[dict[str, datetime]]:
    start = time_min or utc_now()
    end = time_max or start + timedelta(days=29)
    try:
        return google_workspace.query_calendar_busy(time_min=start, time_max=end)
    except Exception as exc:
        raise ValueError(
            f"Google Calendar availability could not be checked: {google_workspace.provider_error_message(exc)}"
        ) from exc


def _overlaps_busy(starts_at: datetime, ends_at: datetime, busy_intervals: list[dict[str, datetime]]) -> bool:
    for interval in busy_intervals or []:
        busy_start = interval.get("start")
        busy_end = interval.get("end")
        if busy_start and busy_end and starts_at < busy_end and ends_at > busy_start:
            return True
    return False


def _google_busy_window(starts_at: datetime, ends_at: datetime) -> list[dict[str, datetime]]:
    if not google_workspace.is_enabled():
        return []
    return google_workspace.query_calendar_busy(time_min=starts_at, time_max=ends_at)


def _with_session_buffer(ends_at: datetime) -> datetime:
    return ends_at + timedelta(minutes=SESSION_BUFFER_MINUTES)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _reschedule_window_from_task(task: HumanReviewTask) -> tuple[datetime | None, datetime | None]:
    payload = task.source_payload if isinstance(task.source_payload, dict) else {}
    starts_at = _parse_datetime(payload.get("proposed_starts_at"))
    ends_at = _parse_datetime(payload.get("proposed_ends_at"))
    if starts_at and not ends_at:
        ends_at = starts_at + timedelta(minutes=SESSION_LENGTH_MINUTES)
    return starts_at, ends_at


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware_utc(value)
    if not value:
        return None
    try:
        return _aware_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _outbound_patient_email(referral: Referral, patient: Patient | None = None) -> str | None:
    override = os.getenv("LUMEN_OUTBOUND_PATIENT_EMAIL_OVERRIDE", "").strip()
    return override or DEMO_OUTBOUND_PATIENT_EMAIL


def _therapist_has_weekly_capacity(
    session: Session,
    therapist_id: str | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
    *,
    exclude_appointment_id: str | None = None,
) -> bool:
    if not therapist_id or not starts_at or not ends_at:
        return True
    week_start, week_end = _week_bounds(starts_at)
    used_minutes = _therapist_patient_contact_minutes(
        session,
        therapist_id,
        week_start,
        week_end,
        statuses=("proposed", "confirmed"),
        exclude_appointment_id=exclude_appointment_id,
    )
    candidate_minutes = max(0, int((ends_at - starts_at).total_seconds() // 60))
    cap_minutes = THERAPIST_WEEKLY_PATIENT_CONTACT_CAP_HOURS * 60
    return used_minutes + candidate_minutes <= cap_minutes


def _therapist_patient_contact_minutes(
    session: Session,
    therapist_id: str,
    week_start: datetime,
    week_end: datetime,
    *,
    statuses: tuple[str, ...] = ("confirmed",),
    exclude_appointment_id: str | None = None,
) -> int:
    query = select(Appointment).where(
        Appointment.therapist_id == therapist_id,
        Appointment.status.in_(list(statuses)),
        Appointment.starts_at >= week_start,
        Appointment.starts_at < week_end,
    )
    if exclude_appointment_id:
        query = query.where(Appointment.id != exclude_appointment_id)
    minutes = 0
    for appointment in session.scalars(query):
        if appointment.starts_at and appointment.ends_at:
            minutes += max(0, int((appointment.ends_at - appointment.starts_at).total_seconds() // 60))
    return minutes


def _week_bounds(value: datetime) -> tuple[datetime, datetime]:
    value = _aware_utc(value)
    start = value - timedelta(days=value.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def _validate_appointment_window(
    session: Session,
    therapist_id: str | None,
    starts_at: datetime,
    ends_at: datetime,
    *,
    exclude_appointment_id: str | None = None,
) -> None:
    if ends_at <= starts_at:
        raise ValueError("Appointment end time must be after start time.")
    if int((ends_at - starts_at).total_seconds() // 60) != SESSION_LENGTH_MINUTES:
        raise ValueError("Appointments must use the 60-minute Lumen session length.")
    if _appointment_conflicts(session, therapist_id or "", starts_at, ends_at, exclude_appointment_id=exclude_appointment_id):
        raise ValueError("Appointment conflicts with an existing proposed or confirmed slot.")
    if google_workspace.is_enabled():
        try:
            google_busy = _google_busy_window(starts_at, _with_session_buffer(ends_at))
        except Exception as exc:
            raise ValueError(
                f"Google Calendar availability could not be checked: {google_workspace.provider_error_message(exc)}"
            ) from exc
        if _overlaps_busy(starts_at, _with_session_buffer(ends_at), google_busy):
            raise ValueError("Appointment conflicts with Google Calendar busy time.")
    if not _therapist_has_weekly_capacity(
        session,
        therapist_id,
        starts_at,
        ends_at,
        exclude_appointment_id=exclude_appointment_id,
    ):
        raise ValueError("Therapist weekly patient-contact cap would be exceeded.")


def _therapist_calendar_capacity_summary(
    session: Session,
    therapist: Therapist,
    *,
    busy_periods: list[dict[str, Any]],
    google_enabled: bool,
    provider_error: str | None,
    event_appointment_ids: set[str],
) -> dict[str, Any]:
    week_start, week_end = _week_bounds(utc_now())
    confirmed_minutes = _therapist_patient_contact_minutes(
        session,
        therapist.id,
        week_start,
        week_end,
        statuses=("confirmed",),
    )
    used_hours = round(confirmed_minutes / 60, 2)
    remaining_hours = max(0, THERAPIST_WEEKLY_PATIENT_CONTACT_CAP_HOURS - used_hours)
    appointments = list(
        session.scalars(
            select(Appointment)
            .where(
                Appointment.tenant_id == therapist.tenant_id,
                Appointment.therapist_id == therapist.id,
                Appointment.status.in_(["proposed", "confirmed"]),
            )
            .order_by(Appointment.starts_at.asc())
        )
    )
    sync_issues = []
    for appointment in appointments:
        if google_enabled and appointment.status == "confirmed" and not appointment.google_calendar_event_id:
            sync_issues.append(
                {
                    "code": "calendar_sync_issue",
                    "appointment_id": appointment.id,
                    "message": "Confirmed local appointment is missing a Google Calendar event ID.",
                }
            )
        if appointment.last_provider_error:
            sync_issues.append(
                {
                    "code": "provider_error",
                    "appointment_id": appointment.id,
                    "message": appointment.last_provider_error,
                }
            )
        if (
            appointment.google_calendar_event_id
            and appointment.id not in event_appointment_ids
            and google_enabled
            and appointment.source != DEMO_STAGE_SEED_SOURCE
        ):
            sync_issues.append(
                {
                    "code": "calendar_event_not_seen",
                    "appointment_id": appointment.id,
                    "message": "Linked Google event was not returned in the current sync window.",
                }
            )

    last_sync = max(
        (_aware_utc(appointment.google_calendar_synced_at) for appointment in appointments if appointment.google_calendar_synced_at),
        default=None,
    )
    available_slots = _available_slots_for_therapist(session, therapist, busy_periods, limit=8)
    status = "manual"
    if google_enabled:
        status = "failed" if provider_error else "sync_issue" if sync_issues else "ready"
    return {
        "therapist_id": therapist.id,
        "therapist_name": therapist.name,
        "sync_status": status,
        "last_sync": iso_or_none(last_sync),
        "sync_errors": sync_issues,
        "busy_periods": busy_periods,
        "next_available_slot": available_slots[0] if available_slots else None,
        "available_slots": available_slots,
        "weekly_patient_contact_hours_used": used_hours,
        "weekly_patient_contact_hours_remaining": remaining_hours,
        "weekly_patient_contact_cap_hours": THERAPIST_WEEKLY_PATIENT_CONTACT_CAP_HOURS,
        "active_appointments": [appointment_to_dict(appointment) for appointment in appointments],
    }


def _available_slots_for_therapist(
    session: Session,
    therapist: Therapist,
    busy_periods: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    parsed_busy = [
        {"start": _parse_datetime(item.get("start")), "end": _parse_datetime(item.get("end"))}
        for item in busy_periods
    ]
    slots = []
    for starts_at, ends_at, block in _generate_slots(therapist.availability_blocks, limit * 10):
        if _appointment_conflicts(session, therapist.id, starts_at, ends_at):
            continue
        if _overlaps_busy(starts_at, _with_session_buffer(ends_at), parsed_busy):
            continue
        if not _therapist_has_weekly_capacity(session, therapist.id, starts_at, ends_at):
            continue
        slots.append(
            {
                "starts_at": iso_or_none(starts_at),
                "ends_at": iso_or_none(ends_at),
                "buffer_until": iso_or_none(_with_session_buffer(ends_at)),
                "weekday": block.get("weekday"),
                "source": "google_backed_availability",
            }
        )
        if len(slots) >= limit:
            break
    return slots


def _calendar_event_to_dict(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event.get("id"),
        "summary": event.get("summary"),
        "event_link": event.get("htmlLink"),
        "start": iso_or_none(event.get("start")) if isinstance(event.get("start"), datetime) else event.get("start"),
        "end": iso_or_none(event.get("end")) if isinstance(event.get("end"), datetime) else event.get("end"),
        "lumen_appointment_id": event.get("lumen_appointment_id"),
        "lumen_referral_id": event.get("lumen_referral_id"),
        "lumen_therapist_id": event.get("lumen_therapist_id"),
    }


def _parse_time(value: Any) -> time | None:
    if not value:
        return None
    try:
        hour, minute = str(value).split(":", 1)
        return time(int(hour), int(minute[:2]))
    except (ValueError, TypeError):
        return None


def _recipient_email_for_referral(referral: Referral, patient: Patient | None = None) -> str | None:
    return (referral.contact_email or (patient.contact_email if patient else None) or "").strip() or None


def _is_valid_email(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()))


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
    _normalise_email_optional_contact_missing_fields(session, referral)
    missing_fields = list(referral.missing_fields or [])
    if not missing_fields:
        return None
    payload = {
        "missing_fields": missing_fields,
        "patient_name": referral.patient_name,
        "contact_email": referral.contact_email,
        "contact_phone": referral.contact_phone,
    }
    task = create_review_task(
        session,
        tenant_id=referral.tenant_id,
        workflow_run_id=referral.workflow_run_id,
        referral_id=referral.id,
        patient_id=referral.patient_id,
        task_type="admin_missing_info_review",
        reason="Referral has missing information that must be resolved before the admin workflow can continue.",
        payload_key="missing_information",
        source_payload=payload,
    )
    task.source_payload = json_safe(payload)
    task.updated_at = utc_now()
    return task


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


def _referral_all_documents(session: Session, referral: Referral) -> list[dict[str, Any]]:
    if not referral.patient_id:
        return []
    documents = [
        document
        for document in session.scalars(
            select(Document)
            .where(Document.tenant_id == referral.tenant_id, Document.patient_id == referral.patient_id)
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
            clean[normalised_key] = _validate_missing_info_update(normalised_key, text)
    return clean


def _validate_missing_info_update(key: str, value: str) -> str:
    clean = str(value or "").strip()
    placeholder_values = {"already captured", "captured", "same as above", "unknown", "tbc"}
    if _normal(clean) in placeholder_values:
        raise ValueError(f"{key.replace('_', ' ')} must contain the actual value, not '{clean}'.")
    target = {"dob": "date_of_birth", "email": "contact_email", "phone": "contact_phone"}.get(key, key)
    if target == "contact_email":
        email = _extract_email_address(clean)
        if not _is_valid_email(email):
            raise ValueError("contact email must be a valid email address.")
        return email or clean
    if target == "date_of_birth":
        parsed = _extract_date_of_birth(f"date of birth: {clean}")
        if not parsed:
            raise ValueError("date of birth must be a valid past date.")
        return parsed
    if target == "contact_phone":
        digits = re.sub(r"\D", "", clean)
        if len(digits) < 7:
            raise ValueError("contact phone must include at least 7 digits.")
        return clean
    if target == "referring_entity" and _normal(clean) in {"none", "no", "n/a", "na", "self", "self referral", "self-referral"}:
        return "Self-referral"
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


def _matching_blocking_missing_fields(referral: Referral) -> list[str]:
    missing = []
    for field in referral.missing_fields or []:
        clean = str(field or "").strip()
        if clean and clean not in EMAIL_FOLLOWUP_NON_BLOCKING_MISSING_FIELDS:
            missing.append(clean)
    if not (referral.contact_email or referral.contact_phone) and "contact_email" not in missing:
        missing.append("contact_email")
    return list(dict.fromkeys(missing))


def _normalise_email_optional_contact_missing_fields(session: Session, referral: Referral) -> bool:
    if str(referral.source_channel or "").strip().lower() != "email":
        return False
    if not (referral.contact_email and referral.date_of_birth):
        return False
    optional_contact_fields = {"contact_phone", "phone", "contact_phone_or_date_of_birth"}
    existing = [str(field or "").strip() for field in referral.missing_fields or [] if str(field or "").strip()]
    remaining = [field for field in existing if field not in optional_contact_fields]
    if remaining == existing:
        return False

    before = referral_summary(referral)
    referral.missing_fields = list(dict.fromkeys(remaining))
    referral.updated_at = utc_now()
    if not referral.missing_fields:
        _close_open_review_tasks(
            session,
            referral,
            task_types=("admin_missing_info_review",),
            status="completed",
            reason="Email referral has patient email and date of birth; phone is optional for the Gmail demo workflow.",
        )
        if canonical_referral_status(referral.status) in {"needs_admin_review", "waiting_for_missing_info"}:
            transition_referral_status(
                session,
                referral,
                _next_admin_gate_status(referral),
                reason="Only optional email contact fields remained missing.",
            )
    write_audit(
        session,
        tenant_id=referral.tenant_id,
        action="normalise_email_optional_contact_missing_fields",
        entity_type="referral",
        entity_id=referral.id,
        before=before,
        after=referral_summary(referral),
    )
    session.flush()
    return True


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


def _intake_template_item_key(spec: dict[str, Any]) -> str:
    return str(spec.get("key") or spec.get("label") or "").strip()


def _intake_template_item_spec(template: IntakeTemplate, item_key: str) -> dict[str, Any] | None:
    wanted = str(item_key or "").strip()
    for spec in template.required_items or []:
        if not isinstance(spec, dict):
            continue
        key = _intake_template_item_key(spec)
        if key == wanted:
            return dict(spec)
    return None


def _intake_template_required_item_specs(template: IntakeTemplate) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for spec in template.required_items or []:
        if not isinstance(spec, dict):
            continue
        key = _intake_template_item_key(spec)
        if key:
            specs.append(dict(spec))
    return specs


def _intake_template_file_documents(session: Session, template: IntakeTemplate) -> list[Document]:
    documents = list(
        session.scalars(
            select(Document)
            .where(
                Document.tenant_id == template.tenant_id,
                Document.document_type == INTAKE_TEMPLATE_FILE_DOCUMENT_TYPE,
            )
            .order_by(Document.created_at.desc())
        )
    )
    return [
        document
        for document in documents
        if (document.metadata_json or {}).get("template_id") == template.id
    ]


def _active_intake_template_files_by_key(session: Session, template: IntakeTemplate) -> dict[str, Document]:
    files_by_key: dict[str, Document] = {}
    for document in _intake_template_file_documents(session, template):
        metadata = document.metadata_json or {}
        if not metadata.get("active"):
            continue
        key = str(metadata.get("item_key") or "").strip()
        if key and key not in files_by_key:
            files_by_key[key] = document
    return files_by_key


def _intake_template_attachment_state(session: Session, template: IntakeTemplate | None) -> dict[str, Any]:
    if template is None:
        return {
            "template_files": [],
            "missing_template_files": [],
            "outbound_attachment_manifest": [],
        }
    files_by_key = _active_intake_template_files_by_key(session, template)
    template_files: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for spec in _intake_template_required_item_specs(template):
        key = _intake_template_item_key(spec)
        document = files_by_key.get(key)
        if document is None:
            missing.append(_missing_template_file_entry(template, spec))
            continue
        template_files.append(_template_file_manifest_entry(document, spec))
    return {
        "template_id": template.id,
        "template_name": template.name,
        "template_files": template_files,
        "missing_template_files": missing,
        "outbound_attachment_manifest": template_files,
    }


def _template_file_manifest_entry(document: Document, spec: dict[str, Any]) -> dict[str, Any]:
    metadata = document.metadata_json or {}
    key = str(metadata.get("item_key") or _intake_template_item_key(spec)).strip()
    return {
        "document_id": document.id,
        "template_id": metadata.get("template_id"),
        "item_key": key,
        "item_label": metadata.get("item_label") or spec.get("label") or key.replace("_", " ").title(),
        "item_type": metadata.get("item_type") or spec.get("type") or "form",
        "file_name": metadata.get("file_name") or metadata.get("filename") or document.title,
        "mime_type": metadata.get("content_type") or metadata.get("mime_type") or "application/octet-stream",
        "size_bytes": metadata.get("size_bytes"),
        "checksum": metadata.get("sha256") or metadata.get("checksum"),
        "storage_uri": document.storage_uri,
        "uploaded_at": iso_or_none(document.created_at),
        "active": bool(metadata.get("active")),
    }


def _missing_template_file_entry(template: IntakeTemplate, spec: dict[str, Any]) -> dict[str, Any]:
    key = _intake_template_item_key(spec)
    return {
        "template_id": template.id,
        "template_name": template.name,
        "item_key": key,
        "item_label": str(spec.get("label") or key.replace("_", " ").title()),
        "item_type": str(spec.get("type") or "form"),
        "required": True,
        "reason": "No active blank template file is configured for this intake item.",
    }


def _local_document_path(storage_uri: str | None) -> Path | None:
    if not storage_uri:
        return None
    storage_text = str(storage_uri)
    if storage_text.startswith(f"{INBOUND_GMAIL_STORAGE_PREFIX}"):
        return None
    candidate = (REPO_ROOT / storage_text).resolve()
    root = REPO_ROOT.resolve()
    try:
        if not candidate.is_relative_to(root):
            return None
    except AttributeError:  # pragma: no cover - Python < 3.9 compatibility
        if root not in candidate.parents and candidate != root:
            return None
    return candidate


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
    blockers = _pre_prep_readiness_blockers(session, referral)

    prep_briefs = session.scalar(
        select(func.count(TherapistPrepBrief.id)).where(TherapistPrepBrief.referral_id == referral.id)
    )
    if not prep_briefs:
        blockers.append("Therapist prep brief is not generated.")
    return blockers


def _pre_prep_readiness_blockers(session: Session, referral: Referral) -> list[str]:
    blockers: list[str] = []
    confirmed_appointments = list(
        session.scalars(
            select(Appointment).where(
                Appointment.referral_id == referral.id,
                Appointment.status == "confirmed",
            )
        )
    )
    if not confirmed_appointments:
        blockers.append("No confirmed appointment.")
    elif google_workspace.is_enabled() and not any(
        appointment.google_calendar_event_id for appointment in confirmed_appointments
    ):
        blockers.append("Confirmed appointment is missing a linked Google Calendar event.")

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
        has_prep_brief = session.scalar(
            select(func.count(TherapistPrepBrief.id)).where(TherapistPrepBrief.referral_id == referral.id)
        )
        if not has_prep_brief and not _pre_prep_readiness_blockers(session, referral):
            generate_prep_brief(session, referral.id)
            return
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
    if "send_approval" in review_gates:
        return "awaiting_patient_contact"
    if "match_approval" in review_gates:
        return "match_recommended"
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
