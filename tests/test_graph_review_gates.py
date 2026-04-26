from __future__ import annotations

from dataclasses import dataclass

from backend.lumen_agentic.graph import build_lumen_graph
from backend.lumen_agentic.schemas import RiskReview
from backend.lumen_agentic.tools import EmptyRAGRetriever


class UnusedAgent:
    name = "unused"

    def invoke(self, payload):  # pragma: no cover - the test verifies this is not reached
        raise AssertionError("agent should not be invoked when retrieval evidence is weak")


class RiskClassifier:
    def classify(self, text: str) -> RiskReview:
        return RiskReview(
            risk_present=False,
            risk_category="none",
            urgency="standard",
            confidence=0.9,
            trigger_spans=[],
            required_handoff="continue",
        )


@dataclass
class Runtime:
    orchestrator: UnusedAgent = UnusedAgent()
    referral_intake: UnusedAgent = UnusedAgent()
    clinical_signal: UnusedAgent = UnusedAgent()
    risk_classifier: RiskClassifier = RiskClassifier()
    therapist_matching: UnusedAgent = UnusedAgent()
    communication_drafter: UnusedAgent = UnusedAgent()
    consent_collector: UnusedAgent = UnusedAgent()
    protocol_matcher: UnusedAgent = UnusedAgent()
    report_writer: UnusedAgent = UnusedAgent()
    clinical_retriever: EmptyRAGRetriever = EmptyRAGRetriever()


def test_session_workflow_stops_when_protocol_retrieval_is_weak() -> None:
    graph = build_lumen_graph(Runtime())

    result = graph.invoke(
        {
            "workflow_id": "test-workflow",
            "workflow_type": "session_completed",
            "tenant_id": "demo-clinic",
            "patient_id": "demo-patient-001",
            "raw_input": {
                "source_channel": "webform",
                "note_text": "Therapist note",
                "report_request": "Draft a report",
            },
            "approvals": {},
            "audit_events": [],
            "errors": [],
            "human_review_queue": [],
        }
    )

    assert result["next_action"] == "await_admin_review"
    assert [task["gate"] for task in result["human_review_queue"]] == ["admin_review"]
    assert "risk_review" not in result
    assert "report_draft" not in result
