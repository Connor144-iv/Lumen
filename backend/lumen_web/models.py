"""SQLAlchemy models for durable Lumen product state."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")
    therapists: Mapped[list["Therapist"]] = relationship(back_populates="tenant")


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class Patient(Base, TimestampMixin):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(160))
    date_of_birth: Mapped[str | None] = mapped_column(String(32))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(80))
    language: Mapped[str | None] = mapped_column(String(80))


class Therapist(Base, TimestampMixin):
    __tablename__ = "therapists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    specialties: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    age_groups: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    modalities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    insurers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    capacity_per_week: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    availability_blocks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="therapists")


class Referral(Base, TimestampMixin):
    __tablename__ = "referrals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_channel: Mapped[str] = mapped_column(String(40), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_file_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(80), default="new", nullable=False, index=True)
    patient_name: Mapped[str | None] = mapped_column(String(160))
    date_of_birth: Mapped[str | None] = mapped_column(String(32))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(80))
    insurer: Mapped[str | None] = mapped_column(String(160))
    referring_entity: Mapped[str | None] = mapped_column(String(160))
    language_preference: Mapped[str | None] = mapped_column(String(80))
    modality_preference: Mapped[str | None] = mapped_column(String(80))
    missing_fields: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    risk_category: Mapped[str | None] = mapped_column(String(80))
    urgency: Mapped[str | None] = mapped_column(String(80))
    risk_present: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    match_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    duplicate_candidates: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    assigned_reviewer_id: Mapped[str | None] = mapped_column(String(36))
    communication_draft_id: Mapped[str | None] = mapped_column(String(36))


class WorkflowRun(Base, TimestampMixin):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    workflow_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    input_summary: Mapped[str] = mapped_column(Text, nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    approvals: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    events: Mapped[list["WorkflowEvent"]] = relationship(
        back_populates="workflow_run", cascade="all, delete-orphan", order_by="WorkflowEvent.index"
    )


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (UniqueConstraint("workflow_run_id", "index", name="uq_workflow_events_run_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False, index=True)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    node: Mapped[str] = mapped_column(String(120), nullable=False)
    agent: Mapped[str | None] = mapped_column(String(160))
    confidence: Mapped[float | None] = mapped_column(Float)
    tools: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    payload: Mapped[dict | list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="events")


class HumanReviewTask(Base, TimestampMixin):
    __tablename__ = "human_review_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="open", nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload_key: Mapped[str] = mapped_column(String(120), nullable=False)
    source_payload: Mapped[dict | list | None] = mapped_column(JSON)
    draft_text: Mapped[str | None] = mapped_column(Text)
    final_text: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class CommunicationDraft(Base, TimestampMixin):
    __tablename__ = "communication_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft_pending_review", nullable=False, index=True)
    proposed_slots: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    requires_human_send: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    therapist_id: Mapped[str | None] = mapped_column(ForeignKey("therapists.id"), index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), default="proposed", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(80), default="manual", nullable=False)


class ConsentRecord(Base, TimestampMixin):
    __tablename__ = "consent_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="missing", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))


class IntakeTemplate(Base, TimestampMixin):
    __tablename__ = "intake_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    patient_type: Mapped[str] = mapped_column(String(80), default="standard", nullable=False)
    insurer: Mapped[str | None] = mapped_column(String(160))
    age_band: Mapped[str | None] = mapped_column(String(80))
    modality: Mapped[str | None] = mapped_column(String(80))
    source_channel: Mapped[str | None] = mapped_column(String(80))
    required_items: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    questionnaire_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class IntakeChecklistItem(Base, TimestampMixin):
    __tablename__ = "intake_checklist_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "referral_id", "item_key", name="uq_intake_items_referral_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    template_id: Mapped[str | None] = mapped_column(ForeignKey("intake_templates.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    item_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="missing", nullable=False, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    notes: Mapped[str | None] = mapped_column(Text)


class QuestionnaireResponse(Base, TimestampMixin):
    __tablename__ = "questionnaire_responses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    template_id: Mapped[str | None] = mapped_column(ForeignKey("intake_templates.id"), index=True)
    questionnaire_name: Mapped[str] = mapped_column(String(160), nullable=False)
    answers: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    score_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="completed", nullable=False, index=True)


class TherapistPrepBrief(Base, TimestampMixin):
    __tablename__ = "therapist_prep_briefs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    therapist_id: Mapped[str | None] = mapped_column(ForeignKey("therapists.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)


class SessionNote(Base, TimestampMixin):
    __tablename__ = "session_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    therapist_id: Mapped[str | None] = mapped_column(ForeignKey("therapists.id"), index=True)
    appointment_id: Mapped[str | None] = mapped_column(ForeignKey("appointments.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClinicalLibraryRecord(Base, TimestampMixin):
    __tablename__ = "clinical_library_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str | None] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False, index=True)


class ScoreRecord(Base, TimestampMixin):
    __tablename__ = "score_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    source_response_id: Mapped[str | None] = mapped_column(ForeignKey("questionnaire_responses.id"), index=True)
    instrument_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    score_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="recorded", nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("source_type", "source_id", "chunk_index", name="uq_document_chunks_source_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(160))
    vector_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ReportDraft(Base, TimestampMixin):
    __tablename__ = "report_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    therapist_id: Mapped[str | None] = mapped_column(ForeignKey("therapists.id"), index=True)
    report_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    claim_evidence_map: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    unsupported_claims: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    retrieval_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False, index=True)
    signed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_off_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ReferralImportBatch(Base, TimestampMixin):
    __tablename__ = "referral_import_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    source_channel: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    file_name: Mapped[str | None] = mapped_column(String(255))
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="processing", nullable=False, index=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    imported_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ReferralImportError(Base):
    __tablename__ = "referral_import_errors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("referral_import_batches.id"), nullable=False, index=True)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_row: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DraftFeedback(Base, TimestampMixin):
    __tablename__ = "draft_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    referral_id: Mapped[str | None] = mapped_column(ForeignKey("referrals.id"), index=True)
    report_draft_id: Mapped[str | None] = mapped_column(ForeignKey("report_drafts.id"), index=True)
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    feedback_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    original_text: Mapped[str | None] = mapped_column(Text)
    final_text: Mapped[str | None] = mapped_column(Text)
    edit_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    usable_for_practice_memory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
