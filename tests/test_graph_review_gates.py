from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from backend.lumen_agentic.graph import build_lumen_graph
from backend.lumen_agentic.schemas import ClinicalSignals, CommunicationDraft, ReferralRecord, RiskReview, TherapistMatchRecommendation
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


class StaticAgent:
    def __init__(self, name, response):
        self.name = name
        self.response = response
        self.payloads = []

    def invoke(self, payload):
        self.payloads.append(payload)
        return self.response


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


def test_match_approval_resumes_to_communication_with_missing_fields_and_slot_options() -> None:
    matching_agent = StaticAgent(
        "therapist_matching_planner",
        TherapistMatchRecommendation(
            ranked_matches=[{"therapist_id": "therapist-1", "name": "Clara"}],
            excluded_therapists=[],
            rationale="Best available match.",
            hard_constraints_checked=["availability"],
            requires_human_approval=True,
        ),
    )
    communication_agent = StaticAgent(
        "patient_communication_drafter",
        CommunicationDraft(
            channel="email",
            subject="Appointment option",
            body="Can you attend option 1? Please also send your date of birth.",
            proposed_slots=["slot-demo-1"],
            prohibited_content_check_passed=True,
            requires_human_send=True,
        ),
    )
    runtime = Runtime(
        referral_intake=StaticAgent(
            "referral_intake_normalizer",
            ReferralRecord(
                referral_id=uuid4(),
                source_channel="email",
                patient_name="Demo Patient",
                date_of_birth=None,
                contact_email="lumenpatientdemo@gmail.com",
                contact_phone=None,
                insurer="Multicare",
                referring_entity=None,
                raw_text_ref="raw_text",
                extraction_confidence=0.8,
            ),
        ),
        clinical_signal=StaticAgent(
            "clinical_signal_extractor",
            ClinicalSignals(
                presenting_concern="Anxiety",
                language_preference="Portuguese",
                modality_preference="online",
                availability_text="Tuesday morning",
                age_band="adult",
                missing_required_fields=["date_of_birth"],
                source_spans=[],
            ),
        ),
        therapist_matching=matching_agent,
        communication_drafter=communication_agent,
    )
    graph = build_lumen_graph(runtime)
    appointment_options = [
        {
            "slot_id": "slot-demo-1",
            "option_code": "OPT1",
            "option_number": 1,
            "therapist_id": "therapist-1",
            "starts_at": "2026-05-14T10:00:00+00:00",
            "ends_at": "2026-05-14T11:00:00+00:00",
        }
    ]

    result = graph.invoke(
        {
            "workflow_id": "email-workflow",
            "workflow_type": "new_referral",
            "tenant_id": "demo-clinic",
            "raw_input": {
                "source_channel": "email",
                "raw_text": "Email referral",
                "therapist_profiles": [{"therapist_id": "therapist-1", "name": "Clara"}],
                "appointment_options": appointment_options,
            },
            "approvals": {"match_approval": True},
            "audit_events": [],
            "errors": [],
            "human_review_queue": [],
        }
    )

    assert [task["gate"] for task in result["human_review_queue"]] == ["send_approval"]
    match_payload = matching_agent.payloads[0]
    assert "appointment_options" not in match_payload
    assert match_payload["therapist_profiles"] == [{"therapist_id": "therapist-1", "name": "Clara"}]
    payload = communication_agent.payloads[0]
    assert payload["missing_required_fields"] == ["date_of_birth"]
    assert payload["appointment_options"] == appointment_options
    assert payload["clinical_signals"]["missing_required_fields"] == ["date_of_birth"]


def test_matcher_parser_error_output_fails_instead_of_opening_match_gate() -> None:
    runtime = Runtime(
        referral_intake=StaticAgent(
            "referral_intake_normalizer",
            ReferralRecord(
                referral_id=uuid4(),
                source_channel="email",
                patient_name="Demo Patient",
                date_of_birth=None,
                contact_email="lumenpatientdemo@gmail.com",
                contact_phone=None,
                insurer="Multicare",
                referring_entity=None,
                raw_text_ref="raw_text",
                extraction_confidence=0.8,
            ),
        ),
        clinical_signal=StaticAgent(
            "clinical_signal_extractor",
            ClinicalSignals(
                presenting_concern="Anxiety",
                language_preference="Portuguese",
                modality_preference="online",
                availability_text="Tuesday morning",
                age_band="adult",
                missing_required_fields=["date_of_birth"],
                source_spans=[],
            ),
        ),
        therapist_matching=StaticAgent(
            "therapist_matching_planner",
            TherapistMatchRecommendation(
                ranked_matches=[],
                excluded_therapists=[],
                rationale="Invalid JSON due to trailing comma in appointment_options array",
                hard_constraints_checked=[],
                requires_human_approval=False,
            ),
        ),
    )
    graph = build_lumen_graph(runtime)

    result = graph.invoke(
        {
            "workflow_id": "email-workflow",
            "workflow_type": "new_referral",
            "tenant_id": "demo-clinic",
            "raw_input": {
                "source_channel": "email",
                "raw_text": "Email referral",
                "therapist_profiles": [{"therapist_id": "therapist-1", "name": "Clara"}],
                "appointment_options": [{"slot_id": "slot-demo-1"}],
            },
            "approvals": {},
            "audit_events": [],
            "errors": [],
            "human_review_queue": [],
        }
    )

    assert result["errors"]
    assert [task["gate"] for task in result["human_review_queue"]] == ["admin_review"]
    assert "match_recommendation" not in result
