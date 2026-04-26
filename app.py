"""FastAPI web server for the Lumen multi-agent workflow."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.lumen_web.db import session_scope
from backend.lumen_web.model_health import check_configured_models
from backend.lumen_web.repositories import (
    apply_review_action,
    approval_payload_for_task,
    list_referrals,
    list_review_tasks,
    list_therapists,
    list_workflow_runs,
    referral_detail,
    review_task_to_dict,
)
from backend.lumen_web.seed import DEMO_TENANT_ID
from backend.lumen_web.workflow_jobs import TERMINAL_STATUSES, WorkflowJobManager, WorkflowRequest


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
SAMPLES_DIR = BASE_DIR / "samples"
MAX_UPLOAD_BYTES = int(os.getenv("LUMEN_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024)))

app = FastAPI(title="Lumen Workflow API", version="0.1.0")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

jobs = WorkflowJobManager()


class ReviewActionRequest(BaseModel):
    action: Literal["approve", "reject", "request_changes", "escalate"]
    final_text: str | None = None
    rejection_reason: str | None = None
    reviewer_id: str | None = None


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
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


def format_sse(event: dict[str, Any]) -> str:
    event_type = event.get("type", "message")
    event_id = event.get("index", 0)
    data = json.dumps(event, ensure_ascii=False, default=str)
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
