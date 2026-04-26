"""Typed handoff schemas for the Lumen LangGraph workflow."""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    """Forbid unknown keys so agent handoffs fail closed."""

    model_config = ConfigDict(extra="forbid")


class EvidenceRef(StrictModel):
    source_type: Literal[
        "referral",
        "session_note",
        "protocol",
        "template",
        "score",
        "calendar",
        "insurer_rule",
    ]
    source_id: UUID | str
    span_start: Optional[int] = None
    span_end: Optional[int] = None
    quote_hash: Optional[str] = None
    relevance_score: Optional[float] = Field(default=None, ge=0, le=1)


class AgentError(StrictModel):
    code: str
    message: str
    recoverable: bool
    retry_count: int = 0


class AgentEnvelope(StrictModel):
    workflow_id: UUID
    tenant_id: UUID
    patient_id: Optional[UUID] = None
    agent_name: str
    schema_version: str = "2026-04-24"
    status: Literal["ok", "needs_human_review", "blocked", "failed"]
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    errors: list[AgentError] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class OrchestratorDecision(StrictModel):
    route: Literal[
        "referral_intake",
        "protocol_matcher",
        "human_clinical_review",
        "admin_review",
        "stop",
    ]
    reason: str
    required_gate: Optional[
        Literal["clinical_review", "match_approval", "send_approval", "therapist_signoff", "admin_review"]
    ] = None


class ReferralRecord(StrictModel):
    referral_id: UUID
    source_channel: Literal["email", "voicemail", "whatsapp", "doctoralia", "excel", "webform"]
    patient_name: Optional[str]
    date_of_birth: Optional[str]
    contact_email: Optional[str]
    contact_phone: Optional[str]
    insurer: Optional[str]
    referring_entity: Optional[str]
    raw_text_ref: str
    dedupe_candidates: list[UUID] = Field(default_factory=list)
    extraction_confidence: float = Field(ge=0, le=1)


class ClinicalSignals(StrictModel):
    presenting_concern: Optional[str]
    language_preference: Optional[str]
    modality_preference: Optional[Literal["in_person", "online", "hybrid", "unknown"]]
    availability_text: Optional[str]
    age_band: Optional[Literal["child", "adolescent", "adult", "older_adult", "unknown"]]
    missing_required_fields: list[str] = Field(default_factory=list)
    source_spans: list[EvidenceRef] = Field(default_factory=list)


class RiskReview(StrictModel):
    risk_present: bool
    risk_category: Literal["none", "self_harm", "suicidality", "acute_crisis", "safeguarding", "unknown"]
    urgency: Literal["standard", "elevated", "urgent", "unknown"]
    confidence: float = Field(ge=0, le=1)
    trigger_spans: list[EvidenceRef] = Field(default_factory=list)
    required_handoff: Literal["continue", "clinician_review", "director_review"]


class TherapistMatchRecommendation(StrictModel):
    ranked_matches: list[dict[str, Any]] = Field(default_factory=list)
    excluded_therapists: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str
    hard_constraints_checked: list[str] = Field(default_factory=list)
    requires_human_approval: bool = True


class CommunicationDraft(StrictModel):
    channel: Literal["email", "sms", "whatsapp"]
    subject: Optional[str]
    body: str
    proposed_slots: list[str] = Field(default_factory=list)
    prohibited_content_check_passed: bool
    requires_human_send: bool = True


class ConsentIntakeSummary(StrictModel):
    required_items: list[str] = Field(default_factory=list)
    completed_items: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    expired_items: list[str] = Field(default_factory=list)
    consent_scope: Optional[str] = None
    requires_admin_followup: bool = False


class ProtocolCoverageMap(StrictModel):
    protocol_id: UUID
    session_note_id: UUID
    covered_steps: list[dict[str, Any]] = Field(default_factory=list)
    partial_steps: list[dict[str, Any]] = Field(default_factory=list)
    missing_steps: list[dict[str, Any]] = Field(default_factory=list)
    extracted_scores: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_inferences: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ReportDraft(StrictModel):
    report_type: Literal[
        "session_summary",
        "assessment_report",
        "treatment_review",
        "evidence_pack",
        "discharge_summary",
    ]
    markdown_body: str
    claim_evidence_map: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    requires_therapist_signoff: bool = True


class HumanApprovalRequest(StrictModel):
    gate: Literal[
        "clinical_review",
        "match_approval",
        "send_approval",
        "therapist_signoff",
        "admin_review",
    ]
    reason: str
    payload_key: str
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(StrictModel):
    workflow_id: UUID | str
    node_name: str
    agent_name: Optional[str] = None
    status: Literal["ok", "needs_human_review", "blocked", "failed"]
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    message: str
    created_at: datetime = Field(default_factory=utc_now)


class LumenGraphState(TypedDict, total=False):
    """Shared LangGraph state.

    Appended fields use reducers so each node can return only its new audit
    events/errors without manually copying the entire history.
    """

    workflow_id: str
    workflow_type: Literal["new_referral", "session_completed"]
    tenant_id: str
    patient_id: Optional[str]
    raw_input: dict[str, Any]
    approvals: dict[str, bool]
    next_action: str

    referral: dict[str, Any]
    clinical_signals: dict[str, Any]
    risk_review: dict[str, Any]
    match_recommendation: dict[str, Any]
    communication_draft: dict[str, Any]
    consent_summary: dict[str, Any]
    protocol_coverage: dict[str, Any]
    report_draft: dict[str, Any]

    audit_events: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]
    human_review_queue: Annotated[list[dict[str, Any]], operator.add]
