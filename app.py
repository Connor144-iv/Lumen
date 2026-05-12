"""FastAPI web server for the Lumen multi-agent workflow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.lumen_web.db import session_scope
from backend.lumen_web import google_workspace
from backend.lumen_web.documentation import (
    add_documentation_session_text_for_therapist,
    create_documentation_session_for_therapist,
    documentation_patient_dashboard_for_therapist,
    documentation_patients_overview_for_therapist,
    documentation_session_detail_for_therapist,
    generate_progress_overview_for_therapist,
    generate_documentation_note_for_therapist,
    list_documentation_patients_for_therapist,
    list_documentation_sessions_for_therapist,
    save_reviewed_documentation_note_for_therapist,
    save_reviewed_documentation_note_update_for_therapist,
    transcribe_documentation_session_audio_for_therapist,
    update_documentation_session_text_for_therapist,
)
from backend.lumen_web.model_health import check_configured_models
from backend.lumen_web.repositories import (
    apply_review_action,
    approval_payload_for_task,
    complete_consent_record,
    complete_intake_item,
    continue_email_referral_workflow,
    approve_session_note,
    create_clinical_library_record,
    create_clinical_escalation_review,
    create_duplicate_resolution_review,
    create_intake_template_file,
    create_manual_appointment_proposal,
    create_referral_document,
    create_session_note,
    create_suitability_review,
    create_therapist,
    draft_first_contact_message,
    draft_intake_packet,
    draft_missing_info_request,
    draft_feedback_metrics,
    export_report_draft,
    deterministic_match_for_referral,
    document_download_info,
    generate_missing_intake_reminder,
    generate_prep_brief,
    generate_report_draft,
    governance_posture,
    get_therapist,
    import_referral_batch,
    integration_health,
    ingest_gmail_message,
    intake_workspace,
    list_intake_tracker,
    list_inbound_gmail_messages,
    gmail_referral_workflow_input,
    list_patients_for_therapist,
    list_referral_import_batches,
    list_referral_import_errors,
    list_appointments,
    list_clinical_library_records,
    list_escalation_queue,
    list_intake_templates,
    list_referrals,
    list_review_tasks,
    list_therapists,
    list_workflow_runs,
    patient_workspace,
    propose_appointment_slots,
    referral_journey_dashboard,
    referral_detail,
    referral_retry_workflow_input,
    referral_workbench_state,
    record_draft_feedback,
    record_missing_info_reply,
    record_simulated_patient_reply,
    reset_clean_demo_referral,
    reset_gmail_patient_demo,
    review_task_to_dict,
    seed_clara_demo_documentation_transcripts,
    mark_inbound_gmail_referral_workflow_started,
    request_consent_exception,
    request_intake_item_exception,
    save_questionnaire_response,
    search_retrieval_chunks,
    security_context,
    sign_off_report_draft,
    start_intake_for_referral,
    request_appointment_reschedule,
    therapist_for_user,
    therapist_calendar_capacity,
    update_report_draft,
    update_therapist,
)
from backend.lumen_web.models import User
from backend.lumen_web.seed import DEMO_TENANT_ID, DEMO_THERAPIST_USER_ID, DEMO_USER_ID
from backend.lumen_web.workflow_jobs import TERMINAL_STATUSES, WorkflowJobManager, WorkflowRequest


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
SAMPLES_DIR = BASE_DIR / "samples"
UPLOAD_DIR = BASE_DIR / "storage" / "uploads" / "intake"
IMPORT_UPLOAD_DIR = BASE_DIR / "storage" / "uploads" / "imports"
MAX_UPLOAD_BYTES = int(os.getenv("LUMEN_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024)))
ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".pdf", ".docx", ".csv", ".xlsx", ".json"}
ALLOWED_UPLOAD_TYPES = {
    "application/json",
    "application/pdf",
    "application/octet-stream",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/csv",
    "text/plain",
}

app = FastAPI(title="Lumen Workflow API", version="0.1.0")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

jobs = WorkflowJobManager()


async def require_admin(request: Request) -> None:
    with session_scope() as session:
        user = session.get(User, current_user_id(request, fallback=DEMO_USER_ID))
        if user is None or not user.active or user.role != "admin":
            raise HTTPException(status_code=403, detail="Current user is not an admin.")


class ReviewActionRequest(BaseModel):
    action: Literal["approve", "reject", "request_changes", "escalate"]
    final_text: str | None = None
    rejection_reason: str | None = None
    reviewer_id: str | None = None
    appointment_id: str | None = None
    document_id: str | None = None
    intake_item_id: str | None = None
    consent_id: str | None = None
    questionnaire_name: str | None = None


class TherapistPayload(BaseModel):
    name: str
    email: str | None = None
    specialties: list[str] = []
    age_groups: list[str] = []
    languages: list[str] = []
    modalities: list[str] = []
    insurers: list[str] = []
    capacity_per_week: int = 0
    active: bool = True
    availability_blocks: list[dict[str, Any]] = []


class AppointmentProposalRequest(BaseModel):
    therapist_id: str | None = None
    limit: int = 3
    availability_text: str | None = None


class ManualAppointmentProposalRequest(BaseModel):
    referral_id: str
    therapist_id: str
    starts_at: datetime
    ends_at: datetime | None = None


class AppointmentRescheduleRequest(BaseModel):
    starts_at: datetime
    ends_at: datetime | None = None
    reason: str = "Appointment reschedule requires admin approval."


class StartIntakeRequest(BaseModel):
    template_id: str | None = None


class CompleteIntakeItemRequest(BaseModel):
    notes: str | None = None


class CompleteConsentRequest(BaseModel):
    expires_at: str | None = None


class IntakeExceptionRequest(BaseModel):
    reason: str = "Authorised exception requested by clinic admin."


class QuestionnaireRequest(BaseModel):
    questionnaire_name: str
    answers: dict[str, Any]


class PrepBriefRequest(BaseModel):
    therapist_id: str | None = None


class MissingInfoDraftRequest(BaseModel):
    recipient: Literal["patient", "referrer", "internal_admin"] = "patient"
    note: str = ""


class MissingInfoReplyRequest(BaseModel):
    source: Literal["patient", "referrer", "internal_admin"] = "patient"
    updates: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class ClinicalReviewRequest(BaseModel):
    reason: str = "Clinical risk or suitability review is required before matching."


class ReviewCreateRequest(BaseModel):
    reason: str = ""
    candidate_referral_id: str | None = None


class DraftMessageRequest(BaseModel):
    note: str = ""
    template_id: str | None = None


class PatientReplyRequest(BaseModel):
    reply_type: Literal["accepted_slot", "declined", "alternative_requested", "asked_question", "unclear", "no_response"]
    appointment_id: str | None = None
    notes: str = ""


class GmailSyncRequest(BaseModel):
    tenant_id: str | None = DEMO_TENANT_ID
    sender: str | None = None
    max_results: int = 10
    include_recent_read: bool = True
    recent_query: str | None = None


class GmailInboxConvertRequest(BaseModel):
    document_id: str
    tenant_id: str | None = DEMO_TENANT_ID


class SessionNoteRequest(BaseModel):
    therapist_id: str | None = None
    appointment_id: str | None = None
    title: str = "Session note"
    body: str
    status: Literal["draft", "pending_approval", "approved"] = "draft"


class ClinicalLibraryRequest(BaseModel):
    tenant_id: str | None = DEMO_TENANT_ID
    record_type: Literal["protocol", "template", "insurer_rule", "clinical_reference"]
    title: str
    body: str
    version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReportDraftRequest(BaseModel):
    report_type: Literal[
        "session_summary",
        "treatment_review",
        "assessment_report",
        "discharge_summary",
        "insurance_evidence_pack",
    ] = "session_summary"
    title: str = ""
    request_text: str = ""
    therapist_id: str | None = None


class ReportDraftUpdateRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    claim_evidence_map: list[dict[str, Any]] | None = None
    reviewer_id: str | None = None
    usable_for_practice_memory: bool = False


class DraftFeedbackRequest(BaseModel):
    feedback_type: str = "review_outcome"
    final_text: str | None = None
    reviewer_id: str | None = None
    usable_for_practice_memory: bool = False


class EmailReferralRequest(BaseModel):
    tenant_id: str | None = DEMO_TENANT_ID
    sender: str = ""
    subject: str = ""
    body: str


class DocumentationSessionCreateRequest(BaseModel):
    patient_id: str
    title: str = "Documentation session"
    appointment_id: str | None = None
    referral_id: str | None = None


class DocumentationTextRequest(BaseModel):
    text: str
    input_type: str = "manual_text"
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentationReviewedNoteRequest(BaseModel):
    note_json: dict[str, Any]
    source_text_id: str | None = None


class DocumentationGenerateNoteRequest(BaseModel):
    source_text_id: str | None = None


class DocumentationReviewedNoteUpdateRequest(BaseModel):
    reviewed_json: dict[str, Any]


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/overview", include_in_schema=False)
@app.get("/workbench", include_in_schema=False)
@app.get("/new-referral", include_in_schema=False)
@app.get("/workflows", include_in_schema=False)
@app.get("/referrals", include_in_schema=False)
@app.get("/review", include_in_schema=False)
@app.get("/therapists", include_in_schema=False)
@app.get("/intake", include_in_schema=False)
@app.get("/clinical", include_in_schema=False)
@app.get("/documentation", include_in_schema=False)
@app.get("/my-patients", include_in_schema=False)
@app.get("/patients/{patient_key}/dashboard", include_in_schema=False)
@app.get("/integrations", include_in_schema=False)
@app.get("/system", include_in_schema=False)
def app_page(patient_key: str | None = None) -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health/models", dependencies=[Depends(require_admin)])
def model_health() -> dict[str, object]:
    return check_configured_models()


@app.get("/api/examples", dependencies=[Depends(require_admin)])
def examples() -> dict[str, Any]:
    items = []
    if SAMPLES_DIR.exists():
        for path in sorted(SAMPLES_DIR.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            items.append({"name": path.stem.replace("_", " ").title(), "file": path.name, "payload": payload})
    return {"examples": items}


@app.post("/api/run-workflow", status_code=202, dependencies=[Depends(require_admin)])
async def run_workflow(request: Request) -> JSONResponse:
    try:
        workflow_request = await parse_workflow_request(request)
        job = jobs.submit(workflow_request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job["job_id"],
            "referral_id": job.get("referral_id"),
            "status": job["status"],
            "status_url": f"/api/status/{job['job_id']}",
            "events_url": f"/api/events/{job['job_id']}",
        },
    )


@app.get("/api/status/{job_id}", dependencies=[Depends(require_admin)])
def workflow_status(job_id: str) -> dict[str, Any]:
    try:
        return jobs.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/workflows", dependencies=[Depends(require_admin)])
def workflows(tenant_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    with session_scope() as session:
        return {"workflows": list_workflow_runs(session, tenant_id=tenant_id, limit=limit)}


@app.get("/api/referrals", dependencies=[Depends(require_admin)])
def referrals(tenant_id: str | None = None, status: str | None = None) -> dict[str, Any]:
    with session_scope() as session:
        return {"referrals": list_referrals(session, tenant_id=tenant_id, status=status)}


@app.get("/api/referral-journey", dependencies=[Depends(require_admin)])
def referral_journey(tenant_id: str | None = None) -> dict[str, Any]:
    with session_scope() as session:
        return referral_journey_dashboard(session, tenant_id=tenant_id)


@app.get("/api/referrals/{referral_id}", dependencies=[Depends(require_admin)])
def referral(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return referral_detail(session, referral_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/referrals/{referral_id}/workbench", dependencies=[Depends(require_admin)])
def referral_workbench(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"workbench_state": referral_workbench_state(session, referral_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/retry-extraction", status_code=202, dependencies=[Depends(require_admin)])
def referral_retry_extraction(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            retry = referral_retry_workflow_input(session, referral_id)
        job = jobs.submit(
            WorkflowRequest(
                workflow_type="new_referral",
                tenant_id=retry["tenant_id"],
                patient_id=retry.get("patient_id"),
                raw_input=retry["raw_input"],
                referral_id=retry["referral_id"],
            )
        )
        return {
            "status": "workflow_started",
            "conversion_status": "workflow_started",
            "job_id": job["job_id"],
            "referral_id": job.get("referral_id"),
            "status_url": f"/api/status/{job['job_id']}",
            "events_url": f"/api/events/{job['job_id']}",
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/continue-email-workflow", dependencies=[Depends(require_admin)])
def referral_continue_email_workflow(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            result = continue_email_referral_workflow(session, referral_id)
            result["workbench_state"] = referral_workbench_state(session, referral_id)
        workflow_request = None
        workflow = result.get("result") or {}
        if workflow.get("action") == "start_workflow":
            workflow_request = WorkflowRequest(
                workflow_type="new_referral",
                tenant_id=workflow["tenant_id"],
                patient_id=workflow.get("patient_id"),
                raw_input=workflow["raw_input"],
                referral_id=workflow.get("referral_id"),
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if workflow_request is not None:
        job = jobs.submit(workflow_request)
        result["status"] = "workflow_started"
        result["job"] = job
        result["result"] = {
            **workflow,
            "status": "workflow_started",
            "job_id": job["job_id"],
            "status_url": f"/api/status/{job['job_id']}",
            "events_url": f"/api/events/{job['job_id']}",
        }
    return result


@app.post("/api/referrals/{referral_id}/match", dependencies=[Depends(require_admin)])
def referral_match(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return deterministic_match_for_referral(session, referral_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/appointment-proposals", dependencies=[Depends(require_admin)])
def appointment_proposals(referral_id: str, body: AppointmentProposalRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            proposals = propose_appointment_slots(
                session,
                referral_id=referral_id,
                therapist_id=body.therapist_id,
                limit=max(1, min(body.limit, 10)),
                availability_text=body.availability_text,
            )
            return {"appointments": proposals}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/missing-info-draft", status_code=201, dependencies=[Depends(require_admin)])
def referral_missing_info_draft(referral_id: str, body: MissingInfoDraftRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"draft": draft_missing_info_request(session, referral_id, recipient=body.recipient, note=body.note)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/missing-info-replies", status_code=201, dependencies=[Depends(require_admin)])
def referral_missing_info_reply(referral_id: str, body: MissingInfoReplyRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return record_missing_info_reply(
                session,
                referral_id,
                source=body.source,
                updates=body.updates,
                notes=body.notes,
            )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/clinical-review", status_code=201, dependencies=[Depends(require_admin)])
def referral_clinical_review(referral_id: str, body: ClinicalReviewRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            task = create_clinical_escalation_review(session, referral_id, reason=body.reason)
            return {"task": review_task_to_dict(task)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/duplicate-review", status_code=201, dependencies=[Depends(require_admin)])
def referral_duplicate_review(referral_id: str, body: ReviewCreateRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            task = create_duplicate_resolution_review(
                session,
                referral_id,
                candidate_referral_id=body.candidate_referral_id,
                reason=body.reason or "Potential duplicate referral requires admin resolution before matching.",
            )
            return {"task": review_task_to_dict(task)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/suitability-review", status_code=201, dependencies=[Depends(require_admin)])
def referral_suitability_review(referral_id: str, body: ReviewCreateRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            task = create_suitability_review(
                session,
                referral_id,
                reason=body.reason or "Suitability review is required before therapist matching.",
            )
            return {"task": review_task_to_dict(task)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/contact-draft", status_code=201, dependencies=[Depends(require_admin)])
def referral_contact_draft(referral_id: str, body: DraftMessageRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"draft": draft_first_contact_message(session, referral_id, note=body.note)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/patient-replies", status_code=201, dependencies=[Depends(require_admin)])
def referral_patient_reply(referral_id: str, body: PatientReplyRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return record_simulated_patient_reply(
                session,
                referral_id=referral_id,
                reply_type=body.reply_type,
                appointment_id=body.appointment_id,
                notes=body.notes,
            )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/appointments", dependencies=[Depends(require_admin)])
def appointments(
    tenant_id: str | None = None,
    referral_id: str | None = None,
    therapist_id: str | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        return {
            "appointments": list_appointments(
                session,
                tenant_id=tenant_id,
                referral_id=referral_id,
                therapist_id=therapist_id,
            )
        }


@app.post("/api/appointments/{appointment_id}/confirm", dependencies=[Depends(require_admin)])
def appointment_confirm(appointment_id: str) -> dict[str, Any]:
    raise HTTPException(
        status_code=400,
        detail="Appointment confirmation must be completed by approving the appointment confirmation review task.",
    )


@app.post("/api/appointments/proposals", status_code=201, dependencies=[Depends(require_admin)])
def appointment_manual_proposal(body: ManualAppointmentProposalRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return create_manual_appointment_proposal(
                session,
                referral_id=body.referral_id,
                therapist_id=body.therapist_id,
                starts_at=body.starts_at,
                ends_at=body.ends_at,
            )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/appointments/{appointment_id}/reschedule-request", status_code=201, dependencies=[Depends(require_admin)])
def appointment_reschedule_request(appointment_id: str, body: AppointmentRescheduleRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            task = request_appointment_reschedule(
                session,
                appointment_id=appointment_id,
                starts_at=body.starts_at,
                ends_at=body.ends_at,
                reason=body.reason,
            )
            return {"task": review_task_to_dict(task)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/review-tasks", dependencies=[Depends(require_admin)])
def review_tasks(tenant_id: str | None = None, status: str | None = "open") -> dict[str, Any]:
    with session_scope() as session:
        return {"tasks": list_review_tasks(session, tenant_id=tenant_id, status=status)}


@app.get("/api/escalations", dependencies=[Depends(require_admin)])
def escalations(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return {"items": list_escalation_queue(session, tenant_id=tenant_id)}


@app.post("/api/demo/clean-referral/reset", status_code=201, dependencies=[Depends(require_admin)])
def demo_clean_referral_reset(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return reset_clean_demo_referral(session, tenant_id or DEMO_TENANT_ID)


@app.post("/api/demo/gmail-patient/reset", status_code=201, dependencies=[Depends(require_admin)])
def demo_gmail_patient_reset(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return reset_gmail_patient_demo(session, tenant_id or DEMO_TENANT_ID)


@app.post("/api/demo/clara-documentation-transcripts/seed", status_code=201, dependencies=[Depends(require_admin)])
def demo_clara_documentation_transcripts_seed(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return seed_clara_demo_documentation_transcripts(session, tenant_id or DEMO_TENANT_ID)


@app.post("/api/review-tasks/{task_id}/actions", dependencies=[Depends(require_admin)])
def review_task_action(task_id: str, body: ReviewActionRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            task = apply_review_action(
                session,
                task_id=task_id,
                action=body.action,
                final_text=body.final_text,
                rejection_reason=body.rejection_reason,
                reviewer_id=body.reviewer_id,
                appointment_id=body.appointment_id,
                document_id=body.document_id,
                intake_item_id=body.intake_item_id,
                consent_id=body.consent_id,
                questionnaire_name=body.questionnaire_name,
            )
            task_payload = review_task_to_dict(task)
            resume_payload = (
                approval_payload_for_task(session, task)
                if body.action == "approve"
                and task.status == "approved"
                and task.task_type in {"match_approval", "therapist_signoff"}
                else None
            )
            referral_payload = referral_detail(session, task.referral_id) if task.referral_id else None
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resumed_job = None
    if resume_payload:
        resumed_job = jobs.submit(
            WorkflowRequest(
                workflow_type=resume_payload["workflow_type"],
                tenant_id=resume_payload["tenant_id"],
                patient_id=resume_payload.get("patient_id"),
                raw_input=resume_payload["raw_input"],
                approvals=resume_payload["approvals"],
                referral_id=resume_payload.get("referral_id"),
            )
        )

    return {
        "task": task_payload,
        "resumed_job": resumed_job,
        "referral": referral_payload,
        "workbench_state": referral_payload.get("workbench_state") if referral_payload else None,
        "message": _review_action_message(body.action, task_payload, referral_payload),
    }


def _review_action_message(action: str, task: dict[str, Any], referral: dict[str, Any] | None) -> str:
    if action == "approve" and task.get("status") == "open" and task.get("provider_error"):
        return f"{str(task.get('task_type') or 'Review task').replace('_', ' ').title()} could not complete: {task['provider_error']}"
    action_labels = {
        "approve": "approved",
        "reject": "rejected",
        "request_changes": "sent back for changes",
        "escalate": "escalated",
    }
    task_type = str(task.get("task_type") or "review task").replace("_", " ")
    if referral:
        state = referral.get("workbench_state") or {}
        next_action = state.get("primary_action_label") or referral.get("next_action_label") or "Review next action"
        status = state.get("primary_status_label") or referral.get("status_label") or referral.get("status")
        return f"{task_type.title()} {action_labels.get(action, 'updated')}. Referral is now {status}; next action: {next_action}."
    return f"{task_type.title()} {action_labels.get(action, 'updated')}."


@app.get("/api/therapists", dependencies=[Depends(require_admin)])
def therapists(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return {"therapists": list_therapists(session, tenant_id=tenant_id)}


def _current_active_therapist(session, request: Request) -> dict[str, Any]:
    user_id = current_user_id(request, fallback=DEMO_USER_ID)
    user = session.get(User, user_id)
    if user is None or user.role != "therapist" or not user.active:
        raise HTTPException(status_code=403, detail="Current user is not an active therapist.")
    therapist = therapist_for_user(session, user)
    if therapist is None or not therapist.get("active"):
        raise HTTPException(status_code=404, detail="No active therapist record is linked to the current user.")
    return therapist


@app.get("/api/me/therapist")
def my_therapist(request: Request) -> dict[str, Any]:
    with session_scope() as session:
        return {"therapist": _current_active_therapist(session, request)}


@app.get("/api/me/therapist/patients")
def my_therapist_patients(request: Request) -> dict[str, Any]:
    with session_scope() as session:
        therapist = _current_active_therapist(session, request)
        return {"patients": list_patients_for_therapist(session, therapist["id"])}


@app.get("/api/documentation/patients")
async def documentation_patients(request: Request) -> dict[str, Any]:
    with session_scope() as session:
        therapist = _current_active_therapist(session, request)
        return {"patients": list_documentation_patients_for_therapist(session, therapist["id"])}


@app.get("/api/documentation/therapists/all/patients/overview")
async def documentation_therapist_patients_overview(request: Request) -> dict[str, Any]:
    with session_scope() as session:
        therapist = _current_active_therapist(session, request)
        return documentation_patients_overview_for_therapist(session, therapist["id"])


@app.get("/api/documentation/patients/{patient_key}/dashboard")
async def documentation_patient_dashboard(patient_key: str, request: Request) -> dict[str, Any]:
    try:
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return documentation_patient_dashboard_for_therapist(
                session,
                therapist_id=therapist["id"],
                patient_key=patient_key,
            )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/documentation/sessions")
async def documentation_sessions(request: Request, patient_id: str | None = None) -> dict[str, Any]:
    try:
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return {
                "sessions": list_documentation_sessions_for_therapist(
                    session,
                    therapist_id=therapist["id"],
                    patient_id=patient_id,
                )
            }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/documentation/sessions", status_code=201)
async def documentation_session_create(body: DocumentationSessionCreateRequest, request: Request) -> dict[str, Any]:
    try:
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return {
                "session": create_documentation_session_for_therapist(
                    session,
                    therapist_id=therapist["id"],
                    patient_id=body.patient_id,
                    title=body.title,
                    appointment_id=body.appointment_id,
                    referral_id=body.referral_id,
                )
            }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/documentation/sessions/{session_id}")
async def documentation_session_get(session_id: str, request: Request) -> dict[str, Any]:
    try:
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return documentation_session_detail_for_therapist(
                session,
                therapist_id=therapist["id"],
                documentation_session_id=session_id,
            )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/documentation/sessions/{session_id}/texts", status_code=201)
async def documentation_session_text_create(
    session_id: str,
    body: DocumentationTextRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return {
                "text": add_documentation_session_text_for_therapist(
                    session,
                    therapist_id=therapist["id"],
                    documentation_session_id=session_id,
                    text=body.text,
                    input_type=body.input_type,
                    source_metadata=body.source_metadata,
                )
            }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/documentation/sessions/{session_id}/audio/transcribe", status_code=201)
async def documentation_session_audio_transcribe(session_id: str, request: Request) -> dict[str, Any]:
    try:
        form = await request.form()
        audio = form.get("audio")
        if audio is None or not hasattr(audio, "read"):
            raise ValueError("Audio file is required.")
        audio_bytes = await audio.read()
        filename = getattr(audio, "filename", None) or "session-audio"
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return {
                "text": transcribe_documentation_session_audio_for_therapist(
                    session,
                    therapist_id=therapist["id"],
                    documentation_session_id=session_id,
                    audio_bytes=audio_bytes,
                    filename=filename,
                )
            }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/documentation/sessions/{session_id}/texts/{text_id}")
async def documentation_session_text_update(
    session_id: str,
    text_id: str,
    body: DocumentationTextRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return {
                "text": update_documentation_session_text_for_therapist(
                    session,
                    therapist_id=therapist["id"],
                    documentation_session_id=session_id,
                    text_id=text_id,
                    text=body.text,
                    input_type=body.input_type,
                    source_metadata=body.source_metadata,
                )
            }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/documentation/sessions/{session_id}/notes/reviewed", status_code=201)
async def documentation_session_reviewed_note_create(
    session_id: str,
    body: DocumentationReviewedNoteRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return {
                "note": save_reviewed_documentation_note_for_therapist(
                    session,
                    therapist_id=therapist["id"],
                    documentation_session_id=session_id,
                    note_json=body.note_json,
                    source_text_id=body.source_text_id,
                    reviewer_id=current_user_id(request, fallback=DEMO_THERAPIST_USER_ID),
                )
            }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/documentation/sessions/{session_id}/notes/generate", status_code=201)
async def documentation_session_note_generate(
    session_id: str,
    body: DocumentationGenerateNoteRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return {
                "note": generate_documentation_note_for_therapist(
                    session,
                    therapist_id=therapist["id"],
                    documentation_session_id=session_id,
                    source_text_id=body.source_text_id,
                )
            }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/documentation/notes/{note_id}/reviewed")
async def documentation_note_reviewed_update(
    note_id: str,
    body: DocumentationReviewedNoteUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return {
                "note": save_reviewed_documentation_note_update_for_therapist(
                    session,
                    therapist_id=therapist["id"],
                    note_id=note_id,
                    reviewed_json=body.reviewed_json,
                    reviewer_id=current_user_id(request, fallback=DEMO_THERAPIST_USER_ID),
                )
            }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/documentation/patients/{patient_key}/progress-overview/generate")
async def documentation_patient_progress_overview_generate(patient_key: str, request: Request) -> dict[str, Any]:
    try:
        with session_scope() as session:
            therapist = _current_active_therapist(session, request)
            return generate_progress_overview_for_therapist(
                session,
                therapist_id=therapist["id"],
                patient_key=patient_key,
            )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/therapists/calendar-capacity", dependencies=[Depends(require_admin)])
def therapists_calendar_capacity(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return therapist_calendar_capacity(session, tenant_id=tenant_id)


@app.post("/api/therapists", status_code=201, dependencies=[Depends(require_admin)])
def therapist_create(body: TherapistPayload, tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"therapist": create_therapist(session, tenant_id or DEMO_TENANT_ID, body.model_dump())}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/therapists/{therapist_id}", dependencies=[Depends(require_admin)])
def therapist_get(therapist_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"therapist": get_therapist(session, therapist_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/therapists/{therapist_id}", dependencies=[Depends(require_admin)])
def therapist_update(therapist_id: str, body: TherapistPayload) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"therapist": update_therapist(session, therapist_id, body.model_dump())}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/intake/templates", dependencies=[Depends(require_admin)])
def intake_templates(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return {"templates": list_intake_templates(session, tenant_id=tenant_id)}


@app.post("/api/intake/templates/{template_id}/items/{item_key}/file", status_code=201, dependencies=[Depends(require_admin)])
async def intake_template_file_upload(template_id: str, item_key: str, request: Request) -> dict[str, Any]:
    try:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise ValueError("A file field is required.")
        content = await upload.read()
        file_name = str(getattr(upload, "filename", "") or "intake-template-file")
        content_type = str(getattr(upload, "content_type", "") or "application/octet-stream")
        file_meta = store_uploaded_document(f"template-{template_id}-{item_key}", file_name, content_type, content)
        with session_scope() as session:
            document = create_intake_template_file(
                session,
                template_id=template_id,
                item_key=item_key,
                title=file_meta["file_name"],
                storage_uri=file_meta["storage_uri"],
                metadata=file_meta,
                actor_user_id=current_user_id(request, fallback=DEMO_USER_ID),
            )
        return {"document": document}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/intake/tracker", dependencies=[Depends(require_admin)])
def intake_tracker(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return {"items": list_intake_tracker(session, tenant_id=tenant_id)}


@app.get("/api/referrals/{referral_id}/intake", dependencies=[Depends(require_admin)])
def referral_intake(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return intake_workspace(session, referral_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/intake", dependencies=[Depends(require_admin)])
def referral_intake_start(referral_id: str, body: StartIntakeRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return start_intake_for_referral(session, referral_id, body.template_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/intake-packet-draft", status_code=201, dependencies=[Depends(require_admin)])
def referral_intake_packet_draft(referral_id: str, body: DraftMessageRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {
                "draft": draft_intake_packet(
                    session,
                    referral_id,
                    note=body.note,
                    template_id=body.template_id,
                )
            }
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/intake-reminder", status_code=201, dependencies=[Depends(require_admin)])
def referral_intake_reminder(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"draft": generate_missing_intake_reminder(session, referral_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/documents", status_code=201, dependencies=[Depends(require_admin)])
async def referral_document_upload(referral_id: str, request: Request) -> dict[str, Any]:
    try:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise ValueError("A file field is required.")
        content = await upload.read()
        file_name = str(getattr(upload, "filename", "") or "upload")
        content_type = str(getattr(upload, "content_type", "") or "application/octet-stream")
        file_meta = store_uploaded_document(referral_id, file_name, content_type, content)
        item_id = str(form.get("item_id") or "").strip() or None
        document_type = str(form.get("document_type") or "intake_document").strip() or "intake_document"
        with session_scope() as session:
            document = create_referral_document(
                session,
                referral_id=referral_id,
                title=file_meta["file_name"],
                document_type=document_type,
                storage_uri=file_meta["storage_uri"],
                metadata=file_meta,
                item_id=item_id,
            )
        return {"document": document}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/documents/{document_id}/download", dependencies=[Depends(require_admin)])
def document_download(document_id: str) -> FileResponse:
    try:
        with session_scope() as session:
            download = document_download_info(session, document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        path=download["path"],
        filename=download["file_name"],
        media_type=download["media_type"],
    )


@app.post("/api/intake/items/{item_id}/complete", dependencies=[Depends(require_admin)])
def intake_item_complete(item_id: str, body: CompleteIntakeItemRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"item": complete_intake_item(session, item_id, notes=body.notes)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/intake/items/{item_id}/exception-request", status_code=201, dependencies=[Depends(require_admin)])
def intake_item_exception_request(item_id: str, body: IntakeExceptionRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            task = request_intake_item_exception(session, item_id, reason=body.reason)
            return {"task": review_task_to_dict(task)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/consent-records/{consent_id}/complete", dependencies=[Depends(require_admin)])
def consent_complete(consent_id: str, body: CompleteConsentRequest) -> dict[str, Any]:
    try:
        expires_at = None
        if body.expires_at:
            expires_at = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
        with session_scope() as session:
            return {"consent": complete_consent_record(session, consent_id, expires_at=expires_at)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/consent-records/{consent_id}/exception-request", status_code=201, dependencies=[Depends(require_admin)])
def consent_exception_request(consent_id: str, body: IntakeExceptionRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            task = request_consent_exception(session, consent_id, reason=body.reason)
            return {"task": review_task_to_dict(task)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/questionnaires", dependencies=[Depends(require_admin)])
def questionnaire_save(referral_id: str, body: QuestionnaireRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {
                "questionnaire": save_questionnaire_response(
                    session,
                    referral_id=referral_id,
                    questionnaire_name=body.questionnaire_name,
                    answers=body.answers,
                )
            }
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/prep-brief", dependencies=[Depends(require_admin)])
def prep_brief_generate(referral_id: str, body: PrepBriefRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"prep_brief": generate_prep_brief(session, referral_id, therapist_id=body.therapist_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/patients/{patient_id}/workspace", dependencies=[Depends(require_admin)])
def patient_workspace_get(patient_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return patient_workspace(session, patient_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/session-notes", status_code=201, dependencies=[Depends(require_admin)])
def session_note_create(referral_id: str, body: SessionNoteRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {
                "session_note": create_session_note(
                    session,
                    referral_id=referral_id,
                    therapist_id=body.therapist_id,
                    appointment_id=body.appointment_id,
                    title=body.title,
                    body=body.body,
                    status=body.status,
                )
            }
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/session-notes/{note_id}/approve", dependencies=[Depends(require_admin)])
def session_note_approve(note_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"session_note": approve_session_note(session, note_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/clinical-library", dependencies=[Depends(require_admin)])
def clinical_library(tenant_id: str | None = DEMO_TENANT_ID, record_type: str | None = None) -> dict[str, Any]:
    with session_scope() as session:
        return {
            "records": list_clinical_library_records(
                session,
                tenant_id=tenant_id,
                record_type=record_type,
            )
        }


@app.post("/api/clinical-library", status_code=201, dependencies=[Depends(require_admin)])
def clinical_library_create(body: ClinicalLibraryRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {
                "record": create_clinical_library_record(
                    session,
                    tenant_id=body.tenant_id or DEMO_TENANT_ID,
                    record_type=body.record_type,
                    title=body.title,
                    body=body.body,
                    version=body.version,
                    metadata=body.metadata,
                )
            }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/retrieval/search", dependencies=[Depends(require_admin)])
def retrieval_search(
    query: str,
    tenant_id: str | None = DEMO_TENANT_ID,
    patient_id: str | None = None,
    document_type: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    with session_scope() as session:
        return {
            "chunks": search_retrieval_chunks(
                session,
                tenant_id=tenant_id or DEMO_TENANT_ID,
                query_text=query,
                patient_id=patient_id,
                document_types=[document_type] if document_type else None,
                limit=limit,
            )
        }


@app.post("/api/referrals/{referral_id}/reports/draft", status_code=201, dependencies=[Depends(require_admin)])
def report_draft_create(referral_id: str, body: ReportDraftRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {
                "report_draft": generate_report_draft(
                    session,
                    referral_id=referral_id,
                    report_type=body.report_type,
                    title=body.title,
                    request_text=body.request_text,
                    therapist_id=body.therapist_id,
                )
            }
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/report-drafts/{report_id}", dependencies=[Depends(require_admin)])
def report_draft_update(report_id: str, body: ReportDraftUpdateRequest, request: Request) -> dict[str, Any]:
    try:
        reviewer_id = body.reviewer_id or current_user_id(request, fallback=DEMO_THERAPIST_USER_ID)
        with session_scope() as session:
            return {
                "report_draft": update_report_draft(
                    session,
                    report_id,
                    title=body.title,
                    body=body.body,
                    claim_evidence_map=body.claim_evidence_map,
                    reviewer_id=reviewer_id,
                    usable_for_practice_memory=body.usable_for_practice_memory,
                )
            }
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/report-drafts/{report_id}/sign-off", dependencies=[Depends(require_admin)])
def report_draft_sign_off(report_id: str, request: Request) -> dict[str, Any]:
    try:
        reviewer_id = current_user_id(request, fallback=DEMO_THERAPIST_USER_ID)
        with session_scope() as session:
            return {"report_draft": sign_off_report_draft(session, report_id, reviewer_id=reviewer_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/report-drafts/{report_id}/export", dependencies=[Depends(require_admin)])
def report_draft_export(report_id: str, format: str = "markdown") -> Response:
    try:
        with session_scope() as session:
            exported = export_report_draft(session, report_id, export_format=format)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=exported["content"],
        media_type=exported["media_type"],
        headers={"Content-Disposition": f'attachment; filename="{exported["file_name"]}"'},
    )


@app.post("/api/report-drafts/{report_id}/feedback", status_code=201, dependencies=[Depends(require_admin)])
def report_draft_feedback(report_id: str, body: DraftFeedbackRequest, request: Request) -> dict[str, Any]:
    try:
        reviewer_id = body.reviewer_id or current_user_id(request, fallback=DEMO_THERAPIST_USER_ID)
        with session_scope() as session:
            return {
                "feedback": record_draft_feedback(
                    session,
                    report_id,
                    feedback_type=body.feedback_type,
                    final_text=body.final_text,
                    reviewer_id=reviewer_id,
                    usable_for_practice_memory=body.usable_for_practice_memory,
                )
            }
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/feedback/metrics", dependencies=[Depends(require_admin)])
def feedback_metrics(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return draft_feedback_metrics(session, tenant_id=tenant_id)


@app.get("/api/integrations/health", dependencies=[Depends(require_admin)])
def integrations_health(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return integration_health(session, tenant_id=tenant_id)


@app.get("/api/integrations/google/status", dependencies=[Depends(require_admin)])
def google_integration_status() -> dict[str, Any]:
    return google_workspace.google_workspace_status(refresh=True)


@app.post("/api/integrations/google/test-calendar-read", dependencies=[Depends(require_admin)])
def google_test_calendar_read() -> dict[str, Any]:
    try:
        return google_workspace.test_calendar_read()
    except google_workspace.GoogleWorkspaceError as exc:
        raise HTTPException(status_code=400, detail=google_workspace.provider_error_message(exc)) from exc


@app.get("/api/integrations/referral-batches", dependencies=[Depends(require_admin)])
def referral_import_batches(tenant_id: str | None = DEMO_TENANT_ID, limit: int = 20) -> dict[str, Any]:
    with session_scope() as session:
        return {"batches": list_referral_import_batches(session, tenant_id=tenant_id, limit=limit)}


@app.get("/api/integrations/import-errors", dependencies=[Depends(require_admin)])
def referral_import_errors(
    tenant_id: str | None = DEMO_TENANT_ID,
    batch_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    with session_scope() as session:
        return {"errors": list_referral_import_errors(session, tenant_id=tenant_id, batch_id=batch_id, limit=limit)}


@app.post("/api/integrations/referral-batches", status_code=201, dependencies=[Depends(require_admin)])
async def referral_import_batch_create(request: Request) -> dict[str, Any]:
    try:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise ValueError("A CSV file field is required.")
        content = await upload.read()
        file_name = str(getattr(upload, "filename", "") or "referrals.csv")
        content_type = str(getattr(upload, "content_type", "") or "text/csv")
        file_meta = store_uploaded_import(file_name, content_type, content)
        tenant_id = str(form.get("tenant_id") or DEMO_TENANT_ID)
        source_channel = str(form.get("source_channel") or "csv_import")
        with session_scope() as session:
            return import_referral_batch(
                session,
                tenant_id=tenant_id,
                file_name=file_meta["file_name"],
                content_text=file_meta["extracted_text"],
                source_channel=source_channel,
                storage_uri=file_meta["storage_uri"],
                metadata={key: value for key, value in file_meta.items() if key != "extracted_text"},
                actor_user_id=current_user_id(request, fallback=DEMO_USER_ID),
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/integrations/email-referrals", status_code=201, dependencies=[Depends(require_admin)])
def email_referral_create(body: EmailReferralRequest, request: Request) -> dict[str, Any]:
    try:
        raw_input = email_referral_raw_input(sender=body.sender, subject=body.subject, body=body.body)
        job = jobs.submit(
            WorkflowRequest(
                workflow_type="new_referral",
                tenant_id=body.tenant_id or DEMO_TENANT_ID,
                raw_input=raw_input,
            )
        )
        return {
            "status": "workflow_started",
            "conversion_status": "workflow_started",
            "job_id": job["job_id"],
            "referral_id": job.get("referral_id"),
            "status_url": f"/api/status/{job['job_id']}",
            "events_url": f"/api/events/{job['job_id']}",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/integrations/gmail-sync", dependencies=[Depends(require_admin)])
def gmail_sync(body: GmailSyncRequest) -> dict[str, Any]:
    if not google_workspace.is_enabled():
        raise HTTPException(status_code=400, detail="Google Workspace integration is not enabled.")
    try:
        account_mismatch = google_workspace.gmail_account_mismatch_message()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=google_workspace.provider_error_message(exc)) from exc
    if account_mismatch:
        raise HTTPException(status_code=400, detail=account_mismatch)

    max_results = max(1, min(body.max_results, 50))
    unread_messages = google_workspace.list_unread_gmail_messages(
        sender_email=body.sender,
        max_results=max_results,
        unread_only=True,
    )
    messages_by_id: dict[str, dict[str, Any]] = {}
    for item in unread_messages:
        message_id = str(item.get("id") or "").strip()
        if message_id:
            messages_by_id[message_id] = item
    recent_messages: list[dict[str, Any]] = []
    if body.include_recent_read:
        try:
            recent_query = body.recent_query or "newer_than:14d"
            if not body.sender and "from:" not in recent_query.lower():
                recent_query = f"{recent_query} from:lumenpatientdemo@gmail.com"
            recent_messages = google_workspace.list_unread_gmail_messages(
                sender_email=body.sender,
                query=recent_query,
                max_results=max_results,
                unread_only=False,
            )
            for item in recent_messages:
                message_id = str(item.get("id") or "").strip()
                if message_id and message_id not in messages_by_id:
                    messages_by_id[message_id] = item
        except Exception as exc:
            recent_messages = []
            errors = [
                {
                    "stage": "recent_inbox_list",
                    "error": google_workspace.provider_error_message(exc),
                }
            ]
        else:
            errors = []
    else:
        errors = []
    messages = list(messages_by_id.values())
    processed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in messages:
        message_id = str(item.get("id") or "").strip()
        if not message_id:
            skipped.append({"status": "skipped", "reason": "missing_message_id"})
            continue
        try:
            raw_message = google_workspace.get_gmail_message(message_id=message_id, format="full")
            parsed = google_workspace.parse_gmail_message(raw_message)
            parsed["attachments"] = _download_gmail_attachments_for_intake(parsed)
        except Exception as exc:
            errors.append(
                {
                    "message_id": message_id,
                    "error": google_workspace.provider_error_message(exc),
                }
            )
            continue

        try:
            with session_scope() as session:
                result = ingest_gmail_message(
                    session,
                    tenant_id=body.tenant_id or DEMO_TENANT_ID,
                    message=parsed,
                )
        except Exception as exc:
            errors.append({"message_id": message_id, "error": str(exc)})
            continue

        if result.get("status") == "processed":
            try:
                google_workspace.mark_gmail_message_read(message_id=message_id)
            except Exception as exc:
                errors.append(
                    {
                        "message_id": message_id,
                        "error": google_workspace.provider_error_message(exc),
                        "stage": "mark_read",
                    }
                )
                continue
            processed.append(result)
        else:
            skipped.append(result)

    return {
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "total_seen": len(messages),
        "unread_seen": len(unread_messages),
        "recent_seen": len(recent_messages),
        "recent_query": recent_query if body.include_recent_read else None,
    }


def _download_gmail_attachments_for_intake(message: dict[str, Any]) -> list[dict[str, Any]]:
    message_id = str(message.get("message_id") or "").strip()
    stored: list[dict[str, Any]] = []
    for attachment in message.get("attachments") or []:
        item = dict(attachment)
        attachment_id = str(item.get("attachment_id") or "").strip()
        if not attachment_id:
            stored.append({**item, "download_status": "failed", "error": "Attachment ID was missing from Gmail metadata."})
            continue
        try:
            content = google_workspace.download_gmail_attachment(message_id=message_id, attachment_id=attachment_id)
            file_name = str(item.get("file_name") or "attachment")
            content_type = str(item.get("mime_type") or "application/octet-stream")
            storage_meta = store_uploaded_document(f"gmail-{message_id}", file_name, content_type, content)
            stored.append({**item, **storage_meta, "download_status": "stored"})
        except Exception as exc:
            stored.append(
                {
                    **item,
                    "download_status": "failed",
                    "error": google_workspace.provider_error_message(exc),
                }
            )
    return stored


@app.get("/api/integrations/gmail-inbox", dependencies=[Depends(require_admin)])
def gmail_inbox(tenant_id: str | None = DEMO_TENANT_ID, limit: int = 50) -> dict[str, Any]:
    with session_scope() as session:
        return {"messages": list_inbound_gmail_messages(session, tenant_id=tenant_id, limit=limit)}


@app.post("/api/integrations/gmail-inbox/convert", status_code=201, dependencies=[Depends(require_admin)])
def gmail_inbox_convert(body: GmailInboxConvertRequest, request: Request) -> dict[str, Any]:
    try:
        with session_scope() as session:
            prepared = gmail_referral_workflow_input(
                session,
                document_id=body.document_id,
                tenant_id=body.tenant_id or DEMO_TENANT_ID,
            )
        if prepared["status"] == "already_converted":
            return {
                "status": "already_converted",
                "conversion_status": "already_converted",
                "job_id": prepared.get("job_id"),
                "referral_id": prepared.get("referral_id"),
                "events_url": f"/api/events/{prepared['job_id']}" if prepared.get("job_id") else None,
                "referral": prepared.get("referral"),
                "document": prepared.get("document"),
            }

        job = jobs.submit(
            WorkflowRequest(
                workflow_type="new_referral",
                tenant_id=body.tenant_id or DEMO_TENANT_ID,
                raw_input=prepared["raw_input"],
            )
        )
        with session_scope() as session:
            document = mark_inbound_gmail_referral_workflow_started(
                session,
                document_id=body.document_id,
                tenant_id=body.tenant_id or DEMO_TENANT_ID,
                referral_id=job.get("referral_id"),
                job_id=job["job_id"],
                actor_user_id=current_user_id(request, fallback=DEMO_USER_ID),
            )
        return {
            "status": "workflow_started",
            "conversion_status": "workflow_started",
            "job_id": job["job_id"],
            "referral_id": job.get("referral_id"),
            "status_url": f"/api/status/{job['job_id']}",
            "events_url": f"/api/events/{job['job_id']}",
            "document": document,
        }
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/security/context")
def security_context_get(request: Request) -> dict[str, Any]:
    with session_scope() as session:
        return security_context(session, current_user_id(request, fallback=DEMO_USER_ID))


@app.get("/api/security/posture", dependencies=[Depends(require_admin)])
def security_posture(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return governance_posture(session, tenant_id=tenant_id)


@app.get("/api/events/{job_id}", dependencies=[Depends(require_admin)])
async def workflow_events(job_id: str, request: Request, cursor: int = 0) -> StreamingResponse:
    try:
        jobs.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def event_stream():
        next_index = max(0, cursor)
        while True:
            if await request.is_disconnected():
                break
            events, status = jobs.events_since(job_id, next_index)
            for event in events:
                next_index = event["index"] + 1
                yield format_sse(event)
            if status in TERMINAL_STATUSES and not events:
                break
            if not events:
                yield format_sse(
                    {
                        "index": next_index,
                        "type": "heartbeat",
                        "status": status,
                        "message": "waiting",
                        "node": "sse",
                    }
                )
            await asyncio.sleep(0.75)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def parse_workflow_request(request: Request) -> WorkflowRequest:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        data = await parse_multipart_payload(request)
    else:
        try:
            data = await request.json()
        except Exception as exc:
            raise ValueError("Expected a JSON body or multipart form data.") from exc

    if not isinstance(data, dict):
        raise ValueError("Request body must be an object.")

    workflow_type = str(data.get("workflow_type", "new_referral"))
    tenant_id = str(data.get("tenant_id") or DEMO_TENANT_ID)
    patient_id = data.get("patient_id") or None
    referral_id = data.get("referral_id") or None
    approvals = normalize_approvals(data.get("approvals", {}))
    raw_input = data.get("raw_input") if isinstance(data.get("raw_input"), dict) else normalize_raw_input(workflow_type, data)

    return WorkflowRequest(
        workflow_type=workflow_type,
        tenant_id=tenant_id,
        patient_id=str(patient_id) if patient_id else None,
        raw_input=raw_input,
        approvals=approvals,
        referral_id=str(referral_id) if referral_id else None,
    )


async def parse_multipart_payload(request: Request) -> dict[str, Any]:
    form = await request.form()
    data = dict(form)
    upload = form.get("file")
    if upload is not None and hasattr(upload, "read"):
        content = await upload.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(f"Uploaded file exceeds {MAX_UPLOAD_BYTES} bytes.")
        file_text = content.decode("utf-8", errors="replace")
        data["uploaded_file_name"] = getattr(upload, "filename", None)
        data["uploaded_file_text"] = file_text
    return data


def store_uploaded_document(referral_id: str, file_name: str, content_type: str, content: bytes) -> dict[str, Any]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Uploaded file exceeds {MAX_UPLOAD_BYTES} bytes.")
    safe_name = safe_upload_name(file_name)
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError("Unsupported file type. Allowed types are TXT, PDF, DOCX, CSV, XLSX, and JSON.")
    if content_type not in ALLOWED_UPLOAD_TYPES:
        raise ValueError(f"Unsupported content type: {content_type}.")
    if content.startswith(b"MZ"):
        raise ValueError("Executable uploads are not allowed.")
    if extension in {".txt", ".csv", ".json"} and b"\x00" in content[:2048]:
        raise ValueError("Text uploads cannot contain binary data.")

    digest = hashlib.sha256(content).hexdigest()
    target_dir = UPLOAD_DIR / safe_upload_name(referral_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{digest[:12]}-{safe_name}"
    target_path.write_bytes(content)
    metadata = {
        "file_name": safe_name,
        "content_type": content_type,
        "size_bytes": len(content),
        "sha256": digest,
        "storage_uri": str(target_path.relative_to(BASE_DIR)),
        "virus_scan": {
            "status": "mvp_file_policy_passed",
            "checks": ["allowlisted_extension", "allowlisted_content_type", "blocked_executable_signature"],
        },
    }
    if extension in {".txt", ".csv", ".json"}:
        metadata["extracted_text"] = content.decode("utf-8", errors="replace")
        metadata["parser"] = "utf8_text_mvp"
    else:
        metadata["parser"] = "metadata_only_mvp"
    return metadata


def store_uploaded_import(file_name: str, content_type: str, content: bytes) -> dict[str, Any]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Uploaded file exceeds {MAX_UPLOAD_BYTES} bytes.")
    safe_name = safe_upload_name(file_name)
    extension = Path(safe_name).suffix.lower()
    if extension not in {".csv", ".txt"}:
        raise ValueError("Referral batch import currently accepts CSV or plain text CSV files.")
    if content_type not in ALLOWED_UPLOAD_TYPES:
        raise ValueError(f"Unsupported content type: {content_type}.")
    if content.startswith(b"MZ") or b"\x00" in content[:2048]:
        raise ValueError("Import uploads must be text files.")

    text = content.decode("utf-8-sig", errors="replace")
    digest = hashlib.sha256(content).hexdigest()
    IMPORT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target_path = IMPORT_UPLOAD_DIR / f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{digest[:12]}-{safe_name}"
    target_path.write_bytes(content)
    return {
        "file_name": safe_name,
        "content_type": content_type,
        "size_bytes": len(content),
        "sha256": digest,
        "storage_uri": str(target_path.relative_to(BASE_DIR)),
        "extracted_text": text,
        "parser": "csv_text_mvp",
    }


def safe_upload_name(value: str) -> str:
    name = Path(value or "upload").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return name[:120] or "upload"


def normalize_raw_input(workflow_type: str, data: dict[str, Any]) -> dict[str, Any]:
    source_channel = str(data.get("source_channel") or "webform")
    uploaded_text = str(data.get("uploaded_file_text") or "")

    if workflow_type == "session_completed":
        note_text = "\n\n".join(part for part in [str(data.get("note_text") or ""), uploaded_text] if part.strip())
        return {
            "source_channel": source_channel,
            "note_text": note_text,
            "report_request": str(data.get("report_request") or ""),
            "selected_protocol": str(data.get("selected_protocol") or ""),
            "therapist_id": str(data.get("therapist_id") or ""),
            "protocol_id": str(data.get("protocol_id") or ""),
            "uploaded_file_name": data.get("uploaded_file_name"),
        }

    raw_text = "\n\n".join(part for part in [str(data.get("raw_text") or ""), uploaded_text] if part.strip())
    return {
        "source_channel": source_channel,
        "raw_text": raw_text,
        "uploaded_file_name": data.get("uploaded_file_name"),
    }


def email_referral_raw_input(*, sender: str, subject: str, body: str) -> dict[str, Any]:
    clean_body = str(body or "").strip()
    if not clean_body:
        raise ValueError("Email body is required.")
    sender_text = str(sender or "").strip()
    subject_text = str(subject or "").strip()
    raw_text = "\n".join(
        part
        for part in [
            f"From: {sender_text}" if sender_text else "",
            f"Subject: {subject_text}" if subject_text else "",
            clean_body,
        ]
        if part
    )
    contact_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", sender_text)
    return {
        "source_channel": "email",
        "raw_text": raw_text,
        "sender": sender_text,
        "subject": subject_text,
        "contact_email": contact_match.group(0).lower() if contact_match else None,
    }


def normalize_approvals(value: Any) -> dict[str, bool]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): normalize_bool(item) for key, item in value.items()}


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def current_user_id(request: Request, fallback: str = DEMO_USER_ID) -> str:
    return request.headers.get("x-lumen-user-id") or fallback


def format_sse(event: dict[str, Any]) -> str:
    event_type = event.get("type", "message")
    event_id = event.get("index", 0)
    data = json.dumps(event, ensure_ascii=False, default=str)
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
