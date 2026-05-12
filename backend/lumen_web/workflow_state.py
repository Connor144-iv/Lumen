"""Admin referral workflow state helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import AuditLog


REFERRAL_STATUSES = (
    "new_referral",
    "normalising",
    "needs_admin_review",
    "waiting_for_missing_info",
    "needs_clinical_review",
    "clinical_escalation_review",
    "ready_for_matching",
    "match_recommended",
    "match_approved",
    "slot_options_ready",
    "awaiting_patient_contact",
    "contact_sent",
    "awaiting_patient_reply",
    "appointment_confirmed",
    "intake_packet_sent",
    "intake_incomplete",
    "intake_complete",
    "prep_brief_ready",
    "first_session_ready",
    "closed_declined",
    "closed_no_response",
    "closed_not_suitable",
)

REVIEW_TASK_TYPES = (
    "admin_missing_info_review",
    "missing_info_message_approval",
    "duplicate_resolution",
    "clinical_risk_review",
    "suitability_review",
    "match_approval",
    "slot_offer_approval",
    "send_approval",
    "appointment_confirmation_approval",
    "intake_reminder_approval",
    "intake_exception_approval",
    "therapist_note_approval",
    "post_session_risk_review",
    "report_signoff",
    "inbound_reply_review",
)

REVIEW_ACTIONS = ("approve", "reject", "request_changes", "escalate")

LEGACY_REFERRAL_STATUS_MAP = {
    "new": "new_referral",
    "normalizing": "normalising",
    "match_pending_approval": "match_recommended",
    "outreach_draft_pending": "awaiting_patient_contact",
    "ready_to_contact": "awaiting_patient_contact",
    "contacted": "appointment_confirmed",
    "closed": "closed_not_suitable",
}

STATUS_LABELS = {
    "new_referral": "New referral",
    "normalising": "Normalising",
    "needs_admin_review": "Needs admin review",
    "waiting_for_missing_info": "Waiting for missing info",
    "needs_clinical_review": "Needs clinical review",
    "clinical_escalation_review": "Clinical escalation review",
    "ready_for_matching": "Ready for matching",
    "match_recommended": "Match recommended",
    "match_approved": "Match approved",
    "slot_options_ready": "Slot options ready",
    "awaiting_patient_contact": "Awaiting patient contact",
    "contact_sent": "Contact sent",
    "awaiting_patient_reply": "Awaiting patient reply",
    "appointment_confirmed": "Appointment confirmed",
    "intake_packet_sent": "Intake packet sent",
    "intake_incomplete": "Intake incomplete",
    "intake_complete": "Intake complete",
    "prep_brief_ready": "Prep brief ready",
    "first_session_ready": "First session ready",
    "closed_declined": "Closed declined",
    "closed_no_response": "Closed no response",
    "closed_not_suitable": "Closed not suitable",
}

NEXT_ACTION_LABELS = {
    "review_referral": "Review referral details",
    "review_missing_info": "Resolve missing information",
    "clinical_review": "Complete clinical review",
    "run_matching": "Run therapist matching",
    "approve_match": "Approve therapist match",
    "approve_slots": "Approve slot options",
    "approve_contact": "Approve patient contact",
    "wait_patient_reply": "Wait for patient reply",
    "confirm_appointment": "Confirm appointment",
    "start_intake": "Start intake",
    "complete_intake": "Complete intake",
    "generate_prep_brief": "Generate prep brief",
    "ready": "Referral complete",
    "closed": "Closed",
    "retry_extraction": "Retry extraction",
    "wait_extraction": "Agent extraction running",
    "review_first_response": "Review prepared email",
    "review_prepared_email": "Review prepared email",
    "send_email": "Send email to patient",
    "sync_replies": "Sync replies",
    "resolve_reply": "Resolve patient reply",
    "resolve_match": "Resolve therapist match",
    "continue_email_workflow": "Continue from email",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_referral_status(status: str | None) -> str:
    clean = str(status or "new_referral").strip()
    return LEGACY_REFERRAL_STATUS_MAP.get(clean, clean)


def status_filter_values(status: str | None) -> list[str] | None:
    if not status:
        return None
    canonical = canonical_referral_status(status)
    values = [canonical]
    values.extend(legacy for legacy, mapped in LEGACY_REFERRAL_STATUS_MAP.items() if mapped == canonical)
    return list(dict.fromkeys(values))


def status_label(status: str | None) -> str:
    canonical = canonical_referral_status(status)
    return STATUS_LABELS.get(canonical, canonical.replace("_", " ").title())


def secondary_flags_for_referral(referral: Any) -> list[str]:
    flags: list[str] = []
    missing = set(referral.missing_fields or [])
    if "date_of_birth" in missing or "dob" in missing:
        flags.append("missing_dob")
    if (
        "contact_email" in missing
        or "contact_phone" in missing
        or "contact_phone_or_date_of_birth" in missing
        or not (referral.contact_email or referral.contact_phone)
    ):
        flags.append("missing_contact")
    if "insurer" in missing or not referral.insurer:
        flags.append("insurance_unclear")
    if referral.duplicate_candidates:
        flags.append("duplicate_candidate")
    if referral.risk_category == "unknown" or referral.urgency == "unknown":
        flags.append("risk_unknown")
    if referral.risk_present or referral.urgency in {"elevated", "urgent"}:
        flags.append("risk_elevated")
    return list(dict.fromkeys(flags))


def next_action_for_referral(referral: Any) -> str:
    status = canonical_referral_status(referral.status)
    flags = secondary_flags_for_referral(referral)
    if status in {"closed_declined", "closed_no_response", "closed_not_suitable"}:
        return "closed"
    if status == "first_session_ready":
        return "ready"
    if status in {"new_referral", "normalising"}:
        return "review_referral"
    if (
        status in {"needs_admin_review", "waiting_for_missing_info"}
        or getattr(referral, "missing_fields", None)
        or any(flag in flags for flag in {"missing_contact", "missing_dob", "insurance_unclear"})
    ):
        return "review_missing_info"
    if status in {"needs_clinical_review", "clinical_escalation_review"}:
        return "clinical_review"
    if status == "ready_for_matching":
        return "run_matching"
    if status == "match_recommended":
        return "approve_match"
    if status == "match_approved":
        return "approve_slots"
    if status == "slot_options_ready":
        return "approve_slots"
    if status == "awaiting_patient_contact":
        return "approve_contact"
    if status in {"contact_sent", "awaiting_patient_reply"}:
        return "wait_patient_reply"
    if status == "appointment_confirmed":
        return "start_intake"
    if status in {"intake_packet_sent", "intake_incomplete"}:
        return "complete_intake"
    if status == "intake_complete":
        return "generate_prep_brief"
    if status == "prep_brief_ready":
        return "ready"
    return "review_referral"


def next_action_label(next_action: str) -> str:
    return NEXT_ACTION_LABELS.get(next_action, next_action.replace("_", " ").title())


def transition_referral_status(
    session: Any,
    referral: Any,
    status: str,
    *,
    actor_user_id: str | None = None,
    reason: str | None = None,
) -> None:
    canonical = canonical_referral_status(status)
    before = {
        "id": referral.id,
        "status": canonical_referral_status(referral.status),
    }
    referral.status = canonical
    referral.updated_at = utc_now()
    after = {
        "id": referral.id,
        "status": canonical,
        "reason": reason,
    }
    session.add(
        AuditLog(
            tenant_id=referral.tenant_id,
            actor_user_id=actor_user_id,
            action="transition_status",
            entity_type="referral",
            entity_id=referral.id,
            before=before,
            after=after,
        )
    )
