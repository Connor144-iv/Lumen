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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.lumen_web.db import session_scope
from backend.lumen_web.model_health import check_configured_models
from backend.lumen_web.repositories import (
    apply_review_action,
    approval_payload_for_task,
    complete_consent_record,
    complete_intake_item,
    confirm_appointment,
    approve_session_note,
    create_clinical_library_record,
    create_email_referral,
    create_referral_document,
    create_session_note,
    create_therapist,
    draft_feedback_metrics,
    export_report_draft,
    deterministic_match_for_referral,
    generate_missing_intake_reminder,
    generate_prep_brief,
    generate_report_draft,
    governance_posture,
    get_therapist,
    import_referral_batch,
    integration_health,
    intake_workspace,
    list_referral_import_batches,
    list_referral_import_errors,
    list_appointments,
    list_clinical_library_records,
    list_intake_templates,
    list_referrals,
    list_review_tasks,
    list_therapists,
    list_workflow_runs,
    patient_workspace,
    propose_appointment_slots,
    referral_detail,
    record_draft_feedback,
    review_task_to_dict,
    save_questionnaire_response,
    search_retrieval_chunks,
    security_context,
    sign_off_report_draft,
    start_intake_for_referral,
    update_report_draft,
    update_therapist,
)
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


class ReviewActionRequest(BaseModel):
    action: Literal["approve", "reject", "request_changes", "escalate"]
    final_text: str | None = None
    rejection_reason: str | None = None
    reviewer_id: str | None = None


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


class StartIntakeRequest(BaseModel):
    template_id: str | None = None


class CompleteIntakeItemRequest(BaseModel):
    notes: str | None = None


class CompleteConsentRequest(BaseModel):
    expires_at: str | None = None


class QuestionnaireRequest(BaseModel):
    questionnaire_name: str
    answers: dict[str, Any]


class PrepBriefRequest(BaseModel):
    therapist_id: str | None = None


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


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/overview", include_in_schema=False)
@app.get("/workflows", include_in_schema=False)
@app.get("/referrals", include_in_schema=False)
@app.get("/review", include_in_schema=False)
@app.get("/therapists", include_in_schema=False)
@app.get("/intake", include_in_schema=False)
@app.get("/clinical", include_in_schema=False)
@app.get("/integrations", include_in_schema=False)
@app.get("/system", include_in_schema=False)
def app_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health/models")
def model_health() -> dict[str, object]:
    return check_configured_models()


@app.get("/api/examples")
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


@app.post("/api/run-workflow", status_code=202)
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


@app.get("/api/status/{job_id}")
def workflow_status(job_id: str) -> dict[str, Any]:
    try:
        return jobs.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/workflows")
def workflows(tenant_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    with session_scope() as session:
        return {"workflows": list_workflow_runs(session, tenant_id=tenant_id, limit=limit)}


@app.get("/api/referrals")
def referrals(tenant_id: str | None = None, status: str | None = None) -> dict[str, Any]:
    with session_scope() as session:
        return {"referrals": list_referrals(session, tenant_id=tenant_id, status=status)}


@app.get("/api/referrals/{referral_id}")
def referral(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return referral_detail(session, referral_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/match")
def referral_match(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return deterministic_match_for_referral(session, referral_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/appointment-proposals")
def appointment_proposals(referral_id: str, body: AppointmentProposalRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            proposals = propose_appointment_slots(
                session,
                referral_id=referral_id,
                therapist_id=body.therapist_id,
                limit=max(1, min(body.limit, 10)),
            )
            return {"appointments": proposals}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/appointments")
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


@app.post("/api/appointments/{appointment_id}/confirm")
def appointment_confirm(appointment_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"appointment": confirm_appointment(session, appointment_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/review-tasks")
def review_tasks(tenant_id: str | None = None, status: str | None = "open") -> dict[str, Any]:
    with session_scope() as session:
        return {"tasks": list_review_tasks(session, tenant_id=tenant_id, status=status)}


@app.post("/api/review-tasks/{task_id}/actions")
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
            )
            task_payload = review_task_to_dict(task)
            resume_payload = (
                approval_payload_for_task(session, task)
                if body.action == "approve" and task.task_type in {"match_approval", "send_approval", "therapist_signoff"}
                else None
            )
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
        "message": "Review action recorded.",
    }


@app.get("/api/therapists")
def therapists(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return {"therapists": list_therapists(session, tenant_id=tenant_id)}


@app.post("/api/therapists", status_code=201)
def therapist_create(body: TherapistPayload, tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"therapist": create_therapist(session, tenant_id or DEMO_TENANT_ID, body.model_dump())}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/therapists/{therapist_id}")
def therapist_get(therapist_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"therapist": get_therapist(session, therapist_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/therapists/{therapist_id}")
def therapist_update(therapist_id: str, body: TherapistPayload) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"therapist": update_therapist(session, therapist_id, body.model_dump())}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/intake/templates")
def intake_templates(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return {"templates": list_intake_templates(session, tenant_id=tenant_id)}


@app.get("/api/referrals/{referral_id}/intake")
def referral_intake(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return intake_workspace(session, referral_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/intake")
def referral_intake_start(referral_id: str, body: StartIntakeRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return start_intake_for_referral(session, referral_id, body.template_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/intake-reminder", status_code=201)
def referral_intake_reminder(referral_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"draft": generate_missing_intake_reminder(session, referral_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/documents", status_code=201)
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


@app.post("/api/intake/items/{item_id}/complete")
def intake_item_complete(item_id: str, body: CompleteIntakeItemRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"item": complete_intake_item(session, item_id, notes=body.notes)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/consent-records/{consent_id}/complete")
def consent_complete(consent_id: str, body: CompleteConsentRequest) -> dict[str, Any]:
    try:
        expires_at = None
        if body.expires_at:
            expires_at = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
        with session_scope() as session:
            return {"consent": complete_consent_record(session, consent_id, expires_at=expires_at)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/questionnaires")
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


@app.post("/api/referrals/{referral_id}/prep-brief")
def prep_brief_generate(referral_id: str, body: PrepBriefRequest) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"prep_brief": generate_prep_brief(session, referral_id, therapist_id=body.therapist_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/patients/{patient_id}/workspace")
def patient_workspace_get(patient_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return patient_workspace(session, patient_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/referrals/{referral_id}/session-notes", status_code=201)
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


@app.post("/api/session-notes/{note_id}/approve")
def session_note_approve(note_id: str) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return {"session_note": approve_session_note(session, note_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/clinical-library")
def clinical_library(tenant_id: str | None = DEMO_TENANT_ID, record_type: str | None = None) -> dict[str, Any]:
    with session_scope() as session:
        return {
            "records": list_clinical_library_records(
                session,
                tenant_id=tenant_id,
                record_type=record_type,
            )
        }


@app.post("/api/clinical-library", status_code=201)
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


@app.get("/api/retrieval/search")
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


@app.post("/api/referrals/{referral_id}/reports/draft", status_code=201)
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


@app.put("/api/report-drafts/{report_id}")
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


@app.post("/api/report-drafts/{report_id}/sign-off")
def report_draft_sign_off(report_id: str, request: Request) -> dict[str, Any]:
    try:
        reviewer_id = current_user_id(request, fallback=DEMO_THERAPIST_USER_ID)
        with session_scope() as session:
            return {"report_draft": sign_off_report_draft(session, report_id, reviewer_id=reviewer_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/report-drafts/{report_id}/export")
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


@app.post("/api/report-drafts/{report_id}/feedback", status_code=201)
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


@app.get("/api/feedback/metrics")
def feedback_metrics(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return draft_feedback_metrics(session, tenant_id=tenant_id)


@app.get("/api/integrations/health")
def integrations_health(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return integration_health(session, tenant_id=tenant_id)


@app.get("/api/integrations/referral-batches")
def referral_import_batches(tenant_id: str | None = DEMO_TENANT_ID, limit: int = 20) -> dict[str, Any]:
    with session_scope() as session:
        return {"batches": list_referral_import_batches(session, tenant_id=tenant_id, limit=limit)}


@app.get("/api/integrations/import-errors")
def referral_import_errors(
    tenant_id: str | None = DEMO_TENANT_ID,
    batch_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    with session_scope() as session:
        return {"errors": list_referral_import_errors(session, tenant_id=tenant_id, batch_id=batch_id, limit=limit)}


@app.post("/api/integrations/referral-batches", status_code=201)
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


@app.post("/api/integrations/email-referrals", status_code=201)
def email_referral_create(body: EmailReferralRequest, request: Request) -> dict[str, Any]:
    try:
        with session_scope() as session:
            return create_email_referral(
                session,
                tenant_id=body.tenant_id or DEMO_TENANT_ID,
                sender=body.sender,
                subject=body.subject,
                body=body.body,
                actor_user_id=current_user_id(request, fallback=DEMO_USER_ID),
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/security/context")
def security_context_get(request: Request) -> dict[str, Any]:
    with session_scope() as session:
        return security_context(session, current_user_id(request, fallback=DEMO_USER_ID))


@app.get("/api/security/posture")
def security_posture(tenant_id: str | None = DEMO_TENANT_ID) -> dict[str, Any]:
    with session_scope() as session:
        return governance_posture(session, tenant_id=tenant_id)


@app.get("/api/events/{job_id}")
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
