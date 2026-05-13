"""Background job orchestration for the Lumen web API."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from backend.lumen_agentic.agents import build_agent_runtime
from backend.lumen_agentic.graph import build_lumen_graph

from .db import init_database, session_scope
from .repositories import (
    append_workflow_event,
    appointment_options_for_workflow,
    create_referral_for_request,
    create_workflow_run,
    ensure_patient,
    ensure_tenant,
    finish_deterministic_email_initial_handoff,
    finish_workflow_run,
    recover_stale_workflow_runs,
    set_workflow_execution_input,
    therapist_facts_for_tenant,
    update_workflow_status,
    workflow_events_since,
    workflow_snapshot,
)


TERMINAL_STATUSES = {"completed", "needs_review", "failed"}
OUTPUT_KEYS = (
    "referral",
    "clinical_signals",
    "risk_review",
    "match_recommendation",
    "communication_draft",
    "consent_summary",
    "protocol_coverage",
    "report_draft",
)
NODE_TOOLS = {
    "referral": ["schema validation"],
    "clinical_signals": ["schema validation"],
    "risk_review": ["risk classifier"],
    "match_recommendation": ["matching rules"],
    "communication_draft": ["policy check"],
    "consent_summary": ["consent checklist"],
    "protocol_matcher": ["retrieve_clinical_context"],
    "protocol_coverage": ["retrieve_clinical_context"],
    "report_writer": ["retrieve_clinical_context", "validate_report_citations"],
    "report_draft": ["retrieve_clinical_context", "validate_report_citations"],
    "record_update": ["governed persistence gate"],
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump(mode="json"))
    return value


@dataclass(frozen=True)
class WorkflowRequest:
    workflow_type: str
    tenant_id: str
    raw_input: dict[str, Any]
    patient_id: str | None = None
    approvals: dict[str, bool] = field(default_factory=dict)
    referral_id: str | None = None


@dataclass
class StreamTracker:
    audit_count: int = 0
    error_count: int = 0
    review_count: int = 0
    seen_outputs: set[str] = field(default_factory=set)


class WorkflowJobManager:
    """Runs LangGraph workflows and persists job state to the application DB."""

    def __init__(self, max_workers: int | None = None) -> None:
        init_database()
        with session_scope() as session:
            recover_stale_workflow_runs(session)
        workers = max_workers or int(os.getenv("LUMEN_WORKER_COUNT", "2"))
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lumen-workflow")
        self._lock = Lock()
        self._graph: Any | None = None
        self._graph_lock = Lock()

    def submit(self, request: WorkflowRequest) -> dict[str, Any]:
        self._validate_request(request)
        job_id = str(uuid4())
        input_summary = self._summarize_input(request.raw_input)

        with self._lock, session_scope() as session:
            ensure_tenant(session, request.tenant_id)
            ensure_patient(session, request.tenant_id, request.patient_id)
            referral = None if request.referral_id else create_referral_for_request(session, request)
            referral_id = request.referral_id or (referral.id if referral else None)
            create_workflow_run(
                session,
                job_id=job_id,
                request=request,
                input_summary=input_summary,
                referral_id=referral_id,
            )
            append_workflow_event(
                session,
                job_id=job_id,
                event_type="workflow",
                status="queued",
                message="Workflow queued for background execution.",
                node="queue",
            )
            if self._uses_deterministic_email_handoff(request):
                finish_deterministic_email_initial_handoff(
                    session,
                    job_id=job_id,
                    raw_input=request.raw_input,
                )
                return workflow_snapshot(session, job_id)

        self._executor.submit(self._run_job, job_id, request)
        return self.snapshot(job_id)

    def snapshot(self, job_id: str) -> dict[str, Any]:
        with session_scope() as session:
            return workflow_snapshot(session, job_id)

    def events_since(self, job_id: str, cursor: int = 0) -> tuple[list[dict[str, Any]], str]:
        with session_scope() as session:
            return workflow_events_since(session, job_id, max(0, cursor))

    def _run_job(self, job_id: str, request: WorkflowRequest) -> None:
        self._set_status(job_id, "running")
        self._append_event(job_id, "workflow", "running", "Workflow started.", node="orchestrator")

        raw_input = self._raw_input_for_execution(request)
        self._set_execution_input(job_id, raw_input)
        initial_state = {
            "workflow_id": job_id,
            "workflow_type": request.workflow_type,
            "tenant_id": request.tenant_id,
            "raw_input": raw_input,
            "approvals": request.approvals,
            "audit_events": [],
            "errors": [],
            "human_review_queue": [],
        }
        if request.patient_id:
            initial_state["patient_id"] = request.patient_id

        tracker = StreamTracker()
        final_state: dict[str, Any] | None = None

        try:
            self._append_event(
                job_id,
                "workflow",
                "running",
                "Preparing agent graph and model runtime.",
                node="graph_setup",
            )
            graph = self._get_graph()
            self._append_event(
                job_id,
                "workflow",
                "running",
                "Agent graph ready; starting workflow execution.",
                node="graph_setup",
            )
            for snapshot in graph.stream(initial_state, stream_mode="values"):
                final_state = json_safe(snapshot)
                self._ingest_snapshot(job_id, final_state, tracker)

            if final_state is None:
                final_state = json_safe(graph.invoke(initial_state))
                self._ingest_snapshot(job_id, final_state, tracker)

            public_result = self._public_result(final_state)
            final_status, message = self._terminal_status(public_result)
            self._finish_job(job_id, final_status, public_result, message)
        except Exception as exc:
            message = self._friendly_error(exc)
            self._append_event(job_id, "error", "failed", message, node="workflow")
            self._finish_job(job_id, "failed", None, message)

    def _raw_input_for_execution(self, request: WorkflowRequest) -> dict[str, Any]:
        raw_input = dict(request.raw_input)
        if request.workflow_type != "new_referral":
            return raw_input
        with session_scope() as session:
            therapist_profiles = therapist_facts_for_tenant(session, request.tenant_id)
            appointment_options = appointment_options_for_workflow(session, request.tenant_id, raw_input)
        if therapist_profiles:
            raw_input["therapist_profiles"] = therapist_profiles
        if appointment_options:
            raw_input["appointment_options"] = appointment_options
        return raw_input

    def _uses_deterministic_email_handoff(self, request: WorkflowRequest) -> bool:
        if request.workflow_type != "new_referral":
            return False
        return str((request.raw_input or {}).get("source_channel") or "").strip().lower() == "email"

    def _set_execution_input(self, job_id: str, raw_input: dict[str, Any]) -> None:
        with self._lock, session_scope() as session:
            set_workflow_execution_input(session, job_id, raw_input)

    def _get_graph(self) -> Any:
        with self._graph_lock:
            if self._graph is None:
                runtime = build_agent_runtime()
                self._graph = build_lumen_graph(runtime)
            return self._graph

    def _ingest_snapshot(self, job_id: str, snapshot: dict[str, Any], tracker: StreamTracker) -> None:
        audit_events = snapshot.get("audit_events") or []
        for audit in audit_events[tracker.audit_count :]:
            node = audit.get("node_name", "workflow")
            self._append_event(
                job_id=job_id,
                event_type="agent",
                status=audit.get("status", "running"),
                message=audit.get("message", "Agent step completed."),
                node=node,
                agent=audit.get("agent_name"),
                confidence=audit.get("confidence"),
                tools=NODE_TOOLS.get(node, []),
                payload=audit,
            )
        tracker.audit_count = len(audit_events)

        for key in OUTPUT_KEYS:
            if key in snapshot and key not in tracker.seen_outputs:
                tracker.seen_outputs.add(key)
                self._append_event(
                    job_id=job_id,
                    event_type="handoff",
                    status="ok",
                    message=f"Produced {key.replace('_', ' ')}.",
                    node=key,
                    tools=NODE_TOOLS.get(key, []),
                    payload=snapshot[key],
                )

        errors = snapshot.get("errors") or []
        for error in errors[tracker.error_count :]:
            self._append_event(
                job_id=job_id,
                event_type="error",
                status="failed",
                message=error.get("message", "Agent step failed."),
                node=error.get("code", "agent_failure"),
                payload=error,
            )
        tracker.error_count = len(errors)

        reviews = snapshot.get("human_review_queue") or []
        for review in reviews[tracker.review_count :]:
            self._append_event(
                job_id=job_id,
                event_type="human_review",
                status="needs_review",
                message=review.get("reason", "Human review required."),
                node=review.get("gate", "human_review"),
                payload=review,
            )
        tracker.review_count = len(reviews)

    def _finish_job(
        self,
        job_id: str,
        status: str,
        result: dict[str, Any] | None,
        message: str,
    ) -> None:
        with self._lock, session_scope() as session:
            finish_workflow_run(
                session,
                job_id=job_id,
                status=status,
                result=result,
                error=message if status == "failed" else None,
            )
            append_workflow_event(
                session,
                job_id=job_id,
                event_type="complete" if status != "failed" else "error",
                status=status,
                message=message,
                node="workflow",
                payload={"result": result} if status != "failed" else None,
            )

    def _set_status(self, job_id: str, status: str) -> None:
        with self._lock, session_scope() as session:
            update_workflow_status(session, job_id, status)

    def _append_event(
        self,
        job_id: str,
        event_type: str,
        status: str,
        message: str,
        node: str,
        agent: str | None = None,
        confidence: float | None = None,
        tools: list[str] | None = None,
        payload: Any | None = None,
    ) -> None:
        with self._lock, session_scope() as session:
            append_workflow_event(
                session,
                job_id=job_id,
                event_type=event_type,
                status=status,
                message=message,
                node=node,
                agent=agent,
                confidence=confidence,
                tools=tools,
                payload=payload,
            )

    def _public_result(self, state: dict[str, Any]) -> dict[str, Any]:
        output = {key: state[key] for key in OUTPUT_KEYS if key in state}
        return {
            "workflow_id": state.get("workflow_id"),
            "workflow_type": state.get("workflow_type"),
            "next_action": state.get("next_action", "complete"),
            "outputs": output,
            "human_review_queue": state.get("human_review_queue", []),
            "errors": state.get("errors", []),
            "audit_events": state.get("audit_events", []),
        }

    def _terminal_status(self, result: dict[str, Any]) -> tuple[str, str]:
        errors = result.get("errors") or []
        reviews = result.get("human_review_queue") or []
        next_action = str(result.get("next_action") or "")
        if errors:
            return "failed", "Workflow stopped because an agent failed. Review the error details before retrying."
        if reviews or next_action.startswith("await_"):
            return "needs_review", "Workflow paused at a required human review gate."
        return "completed", "Workflow completed successfully."

    def _validate_request(self, request: WorkflowRequest) -> None:
        if request.workflow_type not in {"new_referral", "session_completed"}:
            raise ValueError("workflow_type must be 'new_referral' or 'session_completed'.")
        if not request.tenant_id:
            raise ValueError("tenant_id is required.")
        if not request.raw_input:
            raise ValueError("raw_input is required.")
        if request.workflow_type == "new_referral" and not str(request.raw_input.get("raw_text", "")).strip():
            raise ValueError("Referral intake requires referral text or an uploaded text file.")
        if request.workflow_type == "session_completed":
            note_text = str(request.raw_input.get("note_text", "")).strip()
            report_request = str(request.raw_input.get("report_request", "")).strip()
            if not note_text and not report_request:
                raise ValueError("Session report workflow requires a session note, report request, or uploaded text file.")

    def _summarize_input(self, raw_input: dict[str, Any]) -> str:
        text = str(raw_input.get("raw_text") or raw_input.get("note_text") or raw_input)
        return text.strip().replace("\n", " ")[:180]

    def _friendly_error(self, exc: Exception) -> str:
        text = str(exc).strip()
        if not text:
            text = exc.__class__.__name__
        if "connection" in text.lower() or "connect" in text.lower():
            return (
                "The workflow could not reach the configured model server. "
                "Check /api/health/models and confirm the provider URL and model names."
            )
        return f"The workflow could not complete: {text}"
