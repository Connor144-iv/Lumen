"""LangGraph orchestration for Lumen's two core workflows."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .agents import AgentRuntime
from .schemas import (
    AuditEvent,
    CommunicationDraft,
    HumanApprovalRequest,
    LumenGraphState,
    ReportDraft,
    RiskReview,
)
from .tools import ReportCitationValidationInput, RetrievalQuery, validate_report_citations


def build_lumen_graph(runtime: AgentRuntime):
    """Build the deterministic supervisor graph described in Phase 3."""

    from langgraph.graph import END, StateGraph

    graph = StateGraph(LumenGraphState)
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("referral_intake", _agent_node(runtime.referral_intake, "referral"))
    graph.add_node("clinical_signal", _agent_node(runtime.clinical_signal, "clinical_signals"))
    graph.add_node("risk_review", risk_review_node(runtime))
    graph.add_node("human_clinical_review", human_gate_node("clinical_review", "risk_review"))
    graph.add_node("therapist_matching", _agent_node(runtime.therapist_matching, "match_recommendation"))
    graph.add_node("human_match_approval", human_gate_node("match_approval", "match_recommendation"))
    graph.add_node("communication_drafter", communication_node(runtime))
    graph.add_node("human_send_approval", human_gate_node("send_approval", "communication_draft"))
    graph.add_node("consent_collector", _agent_node(runtime.consent_collector, "consent_summary"))
    graph.add_node("protocol_matcher", protocol_matcher_node(runtime))
    graph.add_node("report_writer", report_writer_node(runtime))
    graph.add_node("therapist_signoff", human_gate_node("therapist_signoff", "report_draft"))
    graph.add_node("record_update", governed_record_update_node)
    graph.add_node("failed", failed_node)

    graph.set_entry_point("orchestrator")
    graph.add_conditional_edges(
        "orchestrator",
        start_router,
        {
            "new_referral": "referral_intake",
            "session_completed": "protocol_matcher",
            "failed": "failed",
        },
    )

    graph.add_edge("referral_intake", "clinical_signal")
    graph.add_edge("clinical_signal", "risk_review")
    graph.add_conditional_edges(
        "risk_review",
        risk_router,
        {
            "clinical_review": "human_clinical_review",
            "matching": "therapist_matching",
            "report": "report_writer",
            "failed": "failed",
        },
    )

    graph.add_edge("human_clinical_review", END)
    graph.add_edge("therapist_matching", "human_match_approval")
    graph.add_conditional_edges(
        "human_match_approval",
        approval_router("match_approval", "communication"),
        {"await_human": END, "communication": "communication_drafter"},
    )
    graph.add_edge("communication_drafter", "human_send_approval")
    graph.add_conditional_edges(
        "human_send_approval",
        approval_router("send_approval", "consent"),
        {"await_human": END, "consent": "consent_collector"},
    )
    graph.add_edge("consent_collector", END)

    graph.add_conditional_edges(
        "protocol_matcher",
        review_or_route("risk_review"),
        {"await_human": END, "risk_review": "risk_review"},
    )
    graph.add_conditional_edges(
        "report_writer",
        review_or_route("therapist_signoff"),
        {"await_human": END, "therapist_signoff": "therapist_signoff"},
    )
    graph.add_conditional_edges(
        "therapist_signoff",
        approval_router("therapist_signoff", "record_update"),
        {"await_human": END, "record_update": "record_update"},
    )
    graph.add_edge("record_update", END)
    graph.add_edge("failed", END)
    return graph.compile()


def orchestrator_node(state: LumenGraphState) -> dict[str, Any]:
    workflow_id = state.get("workflow_id") or str(uuid4())
    status = "ok" if state.get("workflow_type") in {"new_referral", "session_completed"} else "failed"
    return {
        "workflow_id": workflow_id,
        "audit_events": [
            AuditEvent(
                workflow_id=workflow_id,
                node_name="orchestrator",
                agent_name="workflow_orchestrator",
                status=status,
                message="Initialized typed workflow state.",
            ).model_dump(mode="json")
        ],
    }


def start_router(state: LumenGraphState) -> str:
    workflow_type = state.get("workflow_type")
    if workflow_type in {"new_referral", "session_completed"}:
        return workflow_type
    return "failed"


def _agent_node(agent: Any, state_key: str):
    def _node(state: LumenGraphState) -> dict[str, Any]:
        workflow_id = state.get("workflow_id", str(uuid4()))
        try:
            result = agent.invoke(_minimum_payload(state, state_key))
            return {
                state_key: result.model_dump(mode="json"),
                "audit_events": [
                    AuditEvent(
                        workflow_id=workflow_id,
                        node_name=state_key,
                        agent_name=agent.name,
                        status="ok",
                        confidence=_confidence_for(state_key, result),
                        message=f"{agent.name} produced validated {state_key}.",
                    ).model_dump(mode="json")
                ],
            }
        except (ValidationError, Exception) as exc:
            return _fail_closed(
                workflow_id=workflow_id,
                node_name=state_key,
                agent_name=getattr(agent, "name", state_key),
                message=str(exc),
            )

    return _node


def risk_review_node(runtime: AgentRuntime):
    def _node(state: LumenGraphState) -> dict[str, Any]:
        workflow_id = state.get("workflow_id", str(uuid4()))
        text = _risk_text(state)
        try:
            review = runtime.risk_classifier.classify(text)
        except Exception as exc:
            review = RiskReview(
                risk_present=True,
                risk_category="unknown",
                urgency="unknown",
                confidence=0.0,
                trigger_spans=[],
                required_handoff="clinician_review",
            )
            return {
                "risk_review": review.model_dump(mode="json"),
                "errors": [{"code": "risk_classifier_failure", "message": str(exc), "recoverable": True}],
                "audit_events": [
                    AuditEvent(
                        workflow_id=workflow_id,
                        node_name="risk_review",
                        agent_name="risk_urgency_reviewer",
                        status="needs_human_review",
                        confidence=0.0,
                        message="Risk classifier failed; routed to clinician review.",
                    ).model_dump(mode="json")
                ],
            }
        status = "ok" if review.required_handoff == "continue" else "needs_human_review"
        return {
            "risk_review": review.model_dump(mode="json"),
            "audit_events": [
                AuditEvent(
                    workflow_id=workflow_id,
                    node_name="risk_review",
                    agent_name="risk_urgency_reviewer",
                    status=status,
                    confidence=review.confidence,
                    message=f"Risk review completed with handoff={review.required_handoff}.",
                ).model_dump(mode="json")
            ],
        }

    return _node


def risk_router(state: LumenGraphState) -> str:
    review = state.get("risk_review") or {}
    if review.get("required_handoff") != "continue":
        return "clinical_review"
    if state.get("workflow_type") == "new_referral":
        return "matching"
    if state.get("workflow_type") == "session_completed":
        return "report"
    return "failed"


def protocol_matcher_node(runtime: AgentRuntime):
    def _node(state: LumenGraphState) -> dict[str, Any]:
        workflow_id = state.get("workflow_id", str(uuid4()))
        raw = state.get("raw_input", {})
        query = RetrievalQuery(
            query_text=" ".join(
                str(raw.get(key, "")) for key in ("presenting_problem", "selected_protocol", "note_text")
            ).strip()
            or str(raw),
            tenant_id=state["tenant_id"],
            patient_id=state.get("patient_id"),
            therapist_id=raw.get("therapist_id"),
            protocol_id=raw.get("protocol_id"),
            document_types=["protocol", "session_note", "score"],
            top_k=8,
        )
        retrieval = runtime.clinical_retriever.retrieve(query)
        if retrieval.weak_evidence:
            return _human_review(
                workflow_id,
                gate="admin_review",
                payload_key="raw_input",
                reason="Protocol matcher could not retrieve sufficient protocol or patient-history evidence.",
                node_name="protocol_matcher",
            )
        payload = {"session": raw, "retrieved_chunks": retrieval.model_dump(mode="json")}
        return _invoke_rag_agent(runtime.protocol_matcher, "protocol_coverage", payload, workflow_id)

    return _node


def report_writer_node(runtime: AgentRuntime):
    def _node(state: LumenGraphState) -> dict[str, Any]:
        workflow_id = state.get("workflow_id", str(uuid4()))
        raw = state.get("raw_input", {})
        query = RetrievalQuery(
            query_text=str(raw.get("report_request", raw.get("note_text", ""))),
            tenant_id=state["tenant_id"],
            patient_id=state.get("patient_id"),
            therapist_id=raw.get("therapist_id"),
            document_types=["template", "session_note", "protocol", "insurer_rule"],
            top_k=8,
        )
        retrieval = runtime.clinical_retriever.retrieve(query)
        if retrieval.weak_evidence:
            return _human_review(
                workflow_id,
                gate="admin_review",
                payload_key="protocol_coverage",
                reason="Report writer could not retrieve sufficient templates or source evidence.",
                node_name="report_writer",
            )
        payload = {
            "session": raw,
            "protocol_coverage": state.get("protocol_coverage"),
            "retrieved_chunks": retrieval.model_dump(mode="json"),
        }
        result = _invoke_rag_agent(runtime.report_writer, "report_draft", payload, workflow_id)
        draft_data = result.get("report_draft")
        if draft_data:
            validation = validate_report_citations(
                ReportCitationValidationInput(report=ReportDraft.model_validate(draft_data))
            )
            if not validation.valid:
                return _human_review(
                    workflow_id,
                    gate="therapist_signoff",
                    payload_key="report_draft",
                    reason=validation.message,
                    node_name="report_writer",
                    extra={"report_draft": draft_data},
                )
        return result

    return _node


def communication_node(runtime: AgentRuntime):
    def _node(state: LumenGraphState) -> dict[str, Any]:
        result = _agent_node(runtime.communication_drafter, "communication_draft")(state)
        draft = result.get("communication_draft")
        if draft:
            parsed = CommunicationDraft.model_validate(draft)
            if not parsed.requires_human_send or not parsed.prohibited_content_check_passed:
                return _human_review(
                    state.get("workflow_id", str(uuid4())),
                    gate="send_approval",
                    payload_key="communication_draft",
                    reason="Communication draft requires human send approval or policy correction.",
                    node_name="communication_drafter",
                    extra=result,
                )
        return result

    return _node


def human_gate_node(gate: str, payload_key: str):
    def _node(state: LumenGraphState) -> dict[str, Any]:
        if state.get("approvals", {}).get(gate):
            return {
                "next_action": "continue",
                "audit_events": [
                    AuditEvent(
                        workflow_id=state.get("workflow_id", ""),
                        node_name=gate,
                        status="ok",
                        message=f"Human approval received for {gate}.",
                    ).model_dump(mode="json")
                ],
            }
        return _human_review(
            state.get("workflow_id", str(uuid4())),
            gate=gate,
            payload_key=payload_key,
            reason=f"Awaiting required human gate: {gate}.",
            node_name=gate,
        )

    return _node


def approval_router(gate: str, approved_route: str):
    def _router(state: LumenGraphState) -> str:
        return approved_route if state.get("approvals", {}).get(gate) else "await_human"

    return _router


def review_or_route(route: str):
    def _router(state: LumenGraphState) -> str:
        if state.get("errors") or str(state.get("next_action") or "").startswith("await_"):
            return "await_human"
        return route

    return _router


def governed_record_update_node(state: LumenGraphState) -> dict[str, Any]:
    """Placeholder for approved persistence only.

    The blueprint forbids direct model writes to final patient records. This
    node is reached only after therapist_signoff approval is present.
    """

    return {
        "next_action": "ready_for_governed_record_write",
        "audit_events": [
            AuditEvent(
                workflow_id=state.get("workflow_id", ""),
                node_name="record_update",
                status="ok",
                message="Approved draft is ready for governed application persistence.",
            ).model_dump(mode="json")
        ],
    }


def failed_node(state: LumenGraphState) -> dict[str, Any]:
    return _fail_closed(
        workflow_id=state.get("workflow_id", str(uuid4())),
        node_name="failed",
        agent_name=None,
        message="Workflow could not be routed from the supplied state.",
    )


def _invoke_rag_agent(agent: Any, state_key: str, payload: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    try:
        result = agent.invoke(payload)
        return {
            state_key: result.model_dump(mode="json"),
            "audit_events": [
                AuditEvent(
                    workflow_id=workflow_id,
                    node_name=state_key,
                    agent_name=agent.name,
                    status="ok",
                    confidence=_confidence_for(state_key, result),
                    message=f"{agent.name} produced validated {state_key}.",
                ).model_dump(mode="json")
            ],
        }
    except Exception as exc:
        return _fail_closed(workflow_id, state_key, getattr(agent, "name", state_key), str(exc))


def _minimum_payload(state: LumenGraphState, target: str) -> dict[str, Any]:
    if target == "referral":
        return state.get("raw_input", {})
    if target == "clinical_signals":
        return {"referral": state.get("referral"), "raw_input": state.get("raw_input")}
    if target == "match_recommendation":
        raw_input = state.get("raw_input", {})
        return {
            "clinical_signals": state.get("clinical_signals"),
            "risk_review": state.get("risk_review"),
            "therapist_profiles": raw_input.get("therapist_profiles", []),
        }
    if target == "communication_draft":
        return {"match": state.get("match_recommendation"), "referral": state.get("referral")}
    if target == "consent_summary":
        return {"patient_id": state.get("patient_id"), "referral": state.get("referral")}
    return dict(state)


def _risk_text(state: LumenGraphState) -> str:
    raw = state.get("raw_input", {})
    parts = [
        raw.get("raw_text", ""),
        raw.get("note_text", ""),
        str(state.get("referral", "")),
        str(state.get("clinical_signals", "")),
    ]
    return "\n".join(part for part in parts if part)


def _confidence_for(state_key: str, result: Any) -> float | None:
    if state_key == "referral":
        return getattr(result, "extraction_confidence", None)
    if state_key == "report_draft":
        return 0.9 if not getattr(result, "unsupported_claims", []) else 0.0
    if state_key == "protocol_coverage":
        unsupported = getattr(result, "unsupported_inferences", [])
        return 0.8 if not unsupported else 0.5
    return None


def _human_review(
    workflow_id: str,
    gate: str,
    payload_key: str,
    reason: str,
    node_name: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = HumanApprovalRequest(gate=gate, reason=reason, payload_key=payload_key)
    result = {
        "next_action": f"await_{gate}",
        "human_review_queue": [request.model_dump(mode="json")],
        "audit_events": [
            AuditEvent(
                workflow_id=workflow_id,
                node_name=node_name,
                status="needs_human_review",
                message=reason,
            ).model_dump(mode="json")
        ],
    }
    if extra:
        result.update(extra)
    return result


def _fail_closed(workflow_id: str, node_name: str, agent_name: str | None, message: str) -> dict[str, Any]:
    return {
        "next_action": "admin_review",
        "errors": [{"code": "agent_failure", "message": message, "recoverable": True}],
        "human_review_queue": [
            HumanApprovalRequest(
                gate="admin_review",
                reason=message,
                payload_key=node_name,
            ).model_dump(mode="json")
        ],
        "audit_events": [
            AuditEvent(
                workflow_id=workflow_id,
                node_name=node_name,
                agent_name=agent_name,
                status="failed",
                message=message,
            ).model_dump(mode="json")
        ],
    }
