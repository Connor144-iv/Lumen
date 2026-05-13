"""Therapist-scoped documentation data services."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import (
    Appointment,
    DocumentationProgressOverview,
    DocumentationSession,
    DocumentationSessionNote,
    DocumentationSessionText,
    Patient,
    Referral,
    Therapist,
)
from .repositories import iso_or_none, json_safe, list_patients_for_therapist, utc_now

NOTE_VERSION = "sessionNoteV0.2"
DEFAULT_HF_BASE_URL = "https://router.huggingface.co/v1"
DEFAULT_HF_TASK_BASE_URL = "https://router.huggingface.co/hf-inference/models"
DEFAULT_HF_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
DEFAULT_HF_ASR_MODEL = "openai/whisper-large-v3-turbo"
DEFAULT_HF_IMAGE_TO_TEXT_MODEL = "google/gemma-4-31B-it"
ALLOWED_HF_TEXT_MODELS = {
    "meta-llama/Llama-3.3-70B-Instruct",
    "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "meta-llama/Llama-3.1-70B-Instruct",
    "Qwen/Qwen2.5-7B-Instruct",
    "openai/gpt-oss-120b",
}
NOTE_TOP_LEVEL_KEYS = {
    "version",
    "noteType",
    "sourceBasis",
    "client",
    "sessionSummary",
    "presentingConcern",
    "relevantHistory",
    "currentStressors",
    "symptomsAndFunctioning",
    "cbtFormulation",
    "riskAndSafety",
    "interventionsUsedInSession",
    "clientResponseToSession",
    "clinicalImpression",
    "progressSignals",
    "planAndFollowUp",
    "uncertaintyFlags",
}
NOTE_TYPES = {"CBTAssessment", "SOAP", "DAP", "Intake", "ProgressNote", "Unknown"}
EXTRACTION_CONFIDENCE_VALUES = {"low", "medium", "high"}
RISK_ASSESSMENT_VALUES = {"notAssessed", "partiallyAssessed", "assessed"}
RISK_ITEM_VALUES = {"notAssessed", "denied", "passive", "active", "present", "unclear"}
PROGRESS_SECTION_KEYS = [
    "caseFrame",
    "longitudinalPatterns",
    "changesSinceIntake",
    "interventionResponse",
    "stuckPointsAndBarriers",
    "riskAndSafety",
    "openClinicalQuestions",
    "nextSessionPriorities",
    "evidenceGaps",
]
LEGACY_PROGRESS_SECTION_KEYS = [
    "clinicalTrends",
    "recurringThemes",
    "interventionProgress",
    "progressMilestones",
    "riskAndSafetyTrend",
    "recommendedNextClinicalFocus",
    "dataQualityIssues",
    "uncertaintyFlags",
]
PROGRESS_TRAJECTORIES = {"improving", "worsening", "stable", "fluctuating", "ongoing", "unclear", "insufficientData"}
PROGRESS_STATIC_SECTION_KEYS = {"nextSessionPriorities", "evidenceGaps"}
PROGRESS_GENERIC_PHRASES = [
    "support and guidance",
    "as needed",
    "daily routine",
    "good understanding of thoughts, feelings, and behaviors",
    "thoughts, feelings, and behaviors",
    "developing coping strategies and self-compassion",
    "significant improvements in symptoms and functioning",
    "improved mood and motivation",
    "mindfulness",
    "relaxation techniques",
    "inconsistent attendance",
    "struggling with consistent attendance",
]
PROGRESS_OVERSTATEMENT_PHRASES = [
    "significant improvement",
    "significant improvements",
    "broad symptom reduction",
    "marked symptom reduction",
    "resolved symptoms",
    "substantial symptom improvement",
]


def list_documentation_patients_for_therapist(session: Session, therapist_id: str) -> list[dict[str, Any]]:
    return list_patients_for_therapist(session, therapist_id)


def documentation_patients_overview_for_therapist(session: Session, therapist_id: str) -> dict[str, Any]:
    patients = list_documentation_patients_for_therapist(session, therapist_id)
    overview = []
    for patient in patients:
        sessions = _documentation_sessions_for_patient(session, therapist_id, patient["id"])
        session_dates = [item.created_at for item in sessions if item.created_at is not None]
        first_session_at = min(session_dates) if session_dates else None
        last_session_at = max(session_dates) if session_dates else None
        overview.append(
            {
                **patient,
                "patient_key": patient["id"],
                "patient_label": patient.get("display_name") or patient["id"],
                "first_session_at": iso_or_none(first_session_at),
                "last_session_at": iso_or_none(last_session_at),
                "session_count": len(sessions),
            }
        )
    return {"patients": overview}


def list_documentation_sessions_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    patient_id: str | None = None,
) -> list[dict[str, Any]]:
    assigned_patient_ids = _assigned_patient_ids(session, therapist_id)
    if patient_id:
        _assigned_patient(session, therapist_id, patient_id)
    if not assigned_patient_ids:
        return []
    query = (
        select(DocumentationSession)
        .where(
            DocumentationSession.therapist_id == therapist_id,
            DocumentationSession.patient_id.in_(assigned_patient_ids),
        )
        .order_by(DocumentationSession.updated_at.desc(), DocumentationSession.created_at.desc())
    )
    if patient_id:
        query = query.where(DocumentationSession.patient_id == patient_id)
    return [documentation_patient_session_to_dict(session, item) for item in session.scalars(query)]


def documentation_patient_dashboard_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    patient_key: str,
) -> dict[str, Any]:
    patient = _resolve_assigned_patient_key(session, therapist_id, patient_key)
    sessions = _documentation_sessions_for_patient(session, therapist_id, patient.id)
    session_items = []
    for item in sessions:
        detail = documentation_session_detail_for_therapist(
            session,
            therapist_id=therapist_id,
            documentation_session_id=item.id,
        )
        latest_text = detail["texts"][0] if detail["texts"] else None
        latest_note = detail["notes"][0] if detail["notes"] else None
        note_json = (latest_note or {}).get("reviewed_json") or (latest_note or {}).get("note_json") or {}
        session_items.append(
            {
                **detail["session"],
                "session_date": detail["session"].get("created_at"),
                "transcript": latest_text,
                "transcript_text": latest_text.get("text") if latest_text else "",
                "transcript_snippet": _snippet(latest_text.get("text") if latest_text else ""),
                "latest_note": latest_note,
                "generated_note_summary": _note_summary(note_json) if isinstance(note_json, dict) else "",
                "notes": detail["notes"],
                "texts": detail["texts"],
            }
        )
    progress_overview = _latest_progress_overview_for_patient(
        session,
        therapist_id=therapist_id,
        patient_id=patient.id,
        fallback_items=session_items,
    )
    return {
        "patient": {
            **_patient_to_dashboard_dict(patient),
            "patient_key": patient.id,
            "patient_label": patient.display_name or patient.id,
        },
        "sessions": session_items,
        "progress_overview": progress_overview,
    }


def create_documentation_session_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    patient_id: str,
    title: str = "",
    appointment_id: str | None = None,
    referral_id: str | None = None,
) -> dict[str, Any]:
    patient = _assigned_patient(session, therapist_id, patient_id)
    therapist = _active_therapist(session, therapist_id)
    clean_referral_id = _validated_referral_id(session, therapist.tenant_id, patient.id, referral_id)
    clean_appointment_id = _validated_appointment_id(
        session,
        therapist_id=therapist.id,
        patient_id=patient.id,
        appointment_id=appointment_id,
    )
    item = DocumentationSession(
        tenant_id=therapist.tenant_id,
        patient_id=patient.id,
        therapist_id=therapist.id,
        referral_id=clean_referral_id,
        appointment_id=clean_appointment_id,
        title=title.strip() or "Documentation session",
        patient_label_snapshot=patient.display_name,
        therapist_label_snapshot=therapist.name,
        status="active",
    )
    session.add(item)
    session.flush()
    return documentation_session_to_dict(item)


def documentation_session_detail_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    documentation_session_id: str,
) -> dict[str, Any]:
    item = _documentation_session_for_therapist(session, therapist_id, documentation_session_id)
    texts = list(
        session.scalars(
            select(DocumentationSessionText)
            .where(DocumentationSessionText.documentation_session_id == item.id)
            .order_by(DocumentationSessionText.created_at.desc())
        )
    )
    notes = list(
        session.scalars(
            select(DocumentationSessionNote)
            .where(DocumentationSessionNote.documentation_session_id == item.id)
            .order_by(DocumentationSessionNote.created_at.desc())
        )
    )
    return {
        "session": documentation_session_to_dict(item),
        "texts": [documentation_text_to_dict(text) for text in texts],
        "notes": [documentation_note_to_dict(note) for note in notes],
    }


def delete_documentation_session_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    documentation_session_id: str,
) -> dict[str, Any]:
    item = _documentation_session_for_therapist(session, therapist_id, documentation_session_id)
    note_count = session.scalar(
        select(func.count())
        .select_from(DocumentationSessionNote)
        .where(DocumentationSessionNote.documentation_session_id == item.id)
    ) or 0
    text_count = session.scalar(
        select(func.count())
        .select_from(DocumentationSessionText)
        .where(DocumentationSessionText.documentation_session_id == item.id)
    ) or 0
    deleted = {
        "id": item.id,
        "patient_id": item.patient_id,
        "text_count": int(text_count),
        "note_count": int(note_count),
    }
    session.execute(
        delete(DocumentationProgressOverview).where(
            DocumentationProgressOverview.therapist_id == therapist_id,
            DocumentationProgressOverview.patient_id == item.patient_id,
        )
    )
    session.execute(delete(DocumentationSessionNote).where(DocumentationSessionNote.documentation_session_id == item.id))
    session.execute(delete(DocumentationSessionText).where(DocumentationSessionText.documentation_session_id == item.id))
    session.execute(delete(DocumentationSession).where(DocumentationSession.id == item.id))
    session.flush()
    return deleted


def _invalidate_progress_overviews(session: Session, *, therapist_id: str, patient_id: str) -> None:
    session.execute(
        delete(DocumentationProgressOverview).where(
            DocumentationProgressOverview.therapist_id == therapist_id,
            DocumentationProgressOverview.patient_id == patient_id,
        )
    )


def add_documentation_session_text_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    documentation_session_id: str,
    text: str,
    input_type: str = "manual_text",
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = _documentation_session_for_therapist(session, therapist_id, documentation_session_id)
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Session text is required.")
    source_text = DocumentationSessionText(
        tenant_id=item.tenant_id,
        documentation_session_id=item.id,
        text=clean_text,
        input_type=input_type.strip() or "manual_text",
        source_metadata=json_safe(source_metadata or {"source": "manual_text"}),
        raw_source_stored=False,
    )
    item.updated_at = utc_now()
    session.add(source_text)
    session.flush()
    return documentation_text_to_dict(source_text)


def update_documentation_session_text_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    documentation_session_id: str,
    text_id: str,
    text: str,
    input_type: str = "manual_text",
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = _documentation_session_for_therapist(session, therapist_id, documentation_session_id)
    source_text = session.get(DocumentationSessionText, text_id)
    if source_text is None:
        raise KeyError(f"Unknown documentation session text: {text_id}")
    if source_text.documentation_session_id != item.id:
        raise ValueError("Session text does not belong to the documentation session.")
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Session text is required.")
    source_text.text = clean_text
    source_text.input_type = input_type.strip() or "manual_text"
    if source_metadata is not None:
        source_text.source_metadata = json_safe(source_metadata)
    source_text.raw_source_stored = False
    source_text.updated_at = utc_now()
    item.updated_at = utc_now()
    session.flush()
    return documentation_text_to_dict(source_text)


def save_reviewed_documentation_note_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    documentation_session_id: str,
    note_json: dict[str, Any],
    source_text_id: str | None = None,
    reviewer_id: str | None = None,
) -> dict[str, Any]:
    item = _documentation_session_for_therapist(session, therapist_id, documentation_session_id)
    if not isinstance(note_json, dict) or not note_json:
        raise ValueError("Reviewed note JSON is required.")
    source_text = None
    if source_text_id:
        source_text = session.get(DocumentationSessionText, source_text_id)
        if source_text is None:
            raise KeyError(f"Unknown documentation session text: {source_text_id}")
        if source_text.documentation_session_id != item.id:
            raise ValueError("Session text does not belong to the documentation session.")
    reviewed_json = normalize_reviewed_note_json(note_json, source_text.text if source_text else "")
    note = DocumentationSessionNote(
        tenant_id=item.tenant_id,
        documentation_session_id=item.id,
        source_text_id=source_text_id or None,
        note_json=json_safe(reviewed_json),
        reviewed_json=json_safe(reviewed_json),
        status="reviewed",
        generator="manual",
        model=None,
        generated_at=None,
        reviewed_at=utc_now(),
        reviewer_id=reviewer_id,
    )
    item.updated_at = utc_now()
    session.add(note)
    _invalidate_progress_overviews(session, therapist_id=therapist_id, patient_id=item.patient_id)
    session.flush()
    return documentation_note_to_dict(note)


def generate_documentation_note_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    documentation_session_id: str,
    source_text_id: str | None = None,
) -> dict[str, Any]:
    item = _documentation_session_for_therapist(session, therapist_id, documentation_session_id)
    source_text = _select_source_text(session, item.id, source_text_id)
    generator = os.getenv("NOTE_GENERATOR", "fake").strip().lower() or "fake"
    if generator not in {"fake", "huggingface"}:
        raise ValueError("NOTE_GENERATOR must be 'fake' or 'huggingface'.")
    if generator == "huggingface":
        try:
            note_json, model = _generate_with_hugging_face(source_text.text)
        except ValueError as exc:
            note_json = _generate_fake_note(source_text.text)
            note_json["uncertaintyFlags"].append(
                {
                    "item": "AI draft generation",
                    "reason": f"Hugging Face generation failed, so this fallback draft was generated locally and requires review: {str(exc)[:500]}",
                }
            )
            model = "local-fallback-after-huggingface-error"
    else:
        note_json, model = _generate_fake_note(source_text.text), "fake"
    validated = validate_session_note_json(note_json, source_text.text)
    note = DocumentationSessionNote(
        tenant_id=item.tenant_id,
        documentation_session_id=item.id,
        source_text_id=source_text.id,
        note_json=validated,
        reviewed_json=None,
        status="draft",
        generator=generator,
        model=model,
        generated_at=utc_now(),
    )
    item.updated_at = utc_now()
    session.add(note)
    session.flush()
    return documentation_note_to_dict(note)


def save_reviewed_documentation_note_update_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    note_id: str,
    reviewed_json: dict[str, Any],
    reviewer_id: str | None = None,
) -> dict[str, Any]:
    note = session.get(DocumentationSessionNote, note_id)
    if note is None:
        raise KeyError(f"Unknown documentation note: {note_id}")
    item = _documentation_session_for_therapist(session, therapist_id, note.documentation_session_id)
    source_text = session.get(DocumentationSessionText, note.source_text_id) if note.source_text_id else None
    if source_text is not None and source_text.documentation_session_id != item.id:
        raise ValueError("Session text does not belong to the documentation note.")
    validated = normalize_reviewed_note_json(reviewed_json, source_text.text if source_text else "")
    versioned_note = DocumentationSessionNote(
        tenant_id=note.tenant_id,
        documentation_session_id=note.documentation_session_id,
        source_text_id=note.source_text_id,
        note_json=json_safe(note.note_json),
        reviewed_json=validated,
        status="reviewed",
        generator=note.generator or "manual",
        model=note.model,
        generated_at=note.generated_at,
        reviewed_at=utc_now(),
        reviewer_id=reviewer_id,
    )
    item.updated_at = utc_now()
    session.add(versioned_note)
    _invalidate_progress_overviews(session, therapist_id=therapist_id, patient_id=item.patient_id)
    session.flush()
    return documentation_note_to_dict(versioned_note)


def transcribe_documentation_session_audio_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    documentation_session_id: str,
    audio_bytes: bytes,
    filename: str = "session-audio",
) -> dict[str, Any]:
    item = _documentation_session_for_therapist(session, therapist_id, documentation_session_id)
    if not audio_bytes:
        raise ValueError("Audio file is required.")
    transcript = _transcribe_audio_with_hugging_face(audio_bytes, filename=filename)
    source_text = DocumentationSessionText(
        tenant_id=item.tenant_id,
        documentation_session_id=item.id,
        text=transcript,
        input_type="audio_transcription",
        source_metadata={
            "source": "audio_upload",
            "provider": "huggingface",
            "model": os.getenv("HF_ASR_MODEL", DEFAULT_HF_ASR_MODEL).strip() or DEFAULT_HF_ASR_MODEL,
            "original_filename": filename,
            "raw_source_stored": False,
        },
        raw_source_stored=False,
    )
    item.updated_at = utc_now()
    session.add(source_text)
    session.flush()
    return documentation_text_to_dict(source_text)


def extract_documentation_session_upload_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    documentation_session_id: str,
    file_bytes: bytes,
    filename: str = "session-upload",
    content_type: str | None = None,
) -> dict[str, Any]:
    item = _documentation_session_for_therapist(session, therapist_id, documentation_session_id)
    if not file_bytes:
        raise ValueError("Upload file is required.")
    file_kind, media_type = _documentation_upload_kind(filename, content_type)
    if file_kind == "audio":
        extracted_text = _transcribe_audio_with_hugging_face(file_bytes, filename=filename, content_type=media_type)
        input_type = "audio_transcription"
        source = "audio_upload"
        model = os.getenv("HF_ASR_MODEL", DEFAULT_HF_ASR_MODEL).strip() or DEFAULT_HF_ASR_MODEL
    elif file_kind in {"image", "pdf"}:
        extracted_text = _extract_visual_text_with_hugging_face(
            file_bytes,
            filename=filename,
            content_type=media_type,
            file_kind=file_kind,
        )
        input_type = "document_extraction" if file_kind == "pdf" else "image_extraction"
        source = "pdf_upload" if file_kind == "pdf" else "image_upload"
        model = os.getenv("HF_IMAGE_TO_TEXT_MODEL", DEFAULT_HF_IMAGE_TO_TEXT_MODEL).strip() or DEFAULT_HF_IMAGE_TO_TEXT_MODEL
    else:
        raise ValueError("Upload must be an audio file, image file, or PDF.")
    source_text = DocumentationSessionText(
        tenant_id=item.tenant_id,
        documentation_session_id=item.id,
        text=extracted_text,
        input_type=input_type,
        source_metadata={
            "source": source,
            "provider": "huggingface",
            "task": "automatic-speech-recognition" if file_kind == "audio" else "image-to-text",
            "model": model,
            "original_filename": filename,
            "content_type": media_type,
            "raw_source_stored": False,
        },
        raw_source_stored=False,
    )
    item.updated_at = utc_now()
    session.add(source_text)
    session.flush()
    return documentation_text_to_dict(source_text)


def generate_progress_overview_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    patient_key: str,
) -> dict[str, Any]:
    dashboard = documentation_patient_dashboard_for_therapist(
        session,
        therapist_id=therapist_id,
        patient_key=patient_key,
    )
    patient_id = dashboard["patient"]["id"]
    progress_sources = _progress_note_sources(dashboard["sessions"])
    if os.getenv("NOTE_GENERATOR", "fake").strip().lower() == "huggingface":
        try:
            overview = _generate_progress_overview_with_hugging_face(progress_sources)
        except ValueError as exc:
            overview = _source_based_progress_overview(progress_sources, error=str(exc))
    else:
        overview = _source_based_progress_overview(progress_sources)
    overview["source_session_count"] = len(dashboard["sessions"])
    overview["reviewed_note_count"] = len(progress_sources)
    overview["generated_at"] = iso_or_none(utc_now())
    record = DocumentationProgressOverview(
        tenant_id=dashboard["patient"]["tenant_id"],
        patient_id=patient_id,
        therapist_id=therapist_id,
        overview_json=json_safe(overview),
        source_metadata={
            "source": "latestReviewedSessionNotes",
            "reviewed_note_count": len(progress_sources),
            "session_count": len(dashboard["sessions"]),
            "session_ids": [source["sessionId"] for source in progress_sources],
            "note_ids": [source["noteId"] for source in progress_sources],
        },
        generated_at=utc_now(),
    )
    session.add(record)
    session.flush()
    return {"patient": dashboard["patient"], "progress_overview": overview}


def normalize_reviewed_note_json(value: dict[str, Any], source_text: str = "") -> dict[str, Any]:
    if set(value) == NOTE_TOP_LEVEL_KEYS:
        return validate_session_note_json(value, source_text)
    summary = str(
        value.get("summary")
        or (value.get("sessionSummary") or {}).get("briefSummary")
        or (value.get("clinicalImpression") or {}).get("summary")
        or ""
    ).strip()
    if not summary:
        raise ValueError("Reviewed note summary is required.")
    note = empty_session_note_json()
    note["sessionSummary"]["briefSummary"] = summary
    note["clinicalImpression"]["summary"] = summary
    note["sourceBasis"]["extractionConfidence"] = "low"
    legacy_lists = {
        "key_points_discussed": ("sessionSummary", "mainClinicalThemes"),
        "presenting_topics": ("presentingConcern", "clientDescription"),
        "interventions": ("interventionsUsedInSession", None),
        "plan": ("planAndFollowUp", "therapyDirection"),
        "uncertainty_flags": ("uncertaintyFlags", None),
    }
    for legacy_key, target in legacy_lists.items():
        items = [str(item).strip() for item in value.get(legacy_key, []) if str(item).strip()] if isinstance(value.get(legacy_key), list) else []
        if not items:
            continue
        parent_key, child_key = target
        if parent_key == "interventionsUsedInSession":
            note[parent_key] = [{"intervention": item, "description": item, "evidence": []} for item in items]
        elif parent_key == "uncertaintyFlags":
            note[parent_key] = [{"item": item, "reason": "Carried forward from reviewed note fields."} for item in items]
        else:
            note[parent_key][child_key] = items
    risk = value.get("risk_or_safety") or value.get("riskAndSafety")
    if isinstance(risk, dict):
        note["riskAndSafety"]["status"] = _camel_risk_status(str(risk.get("status") or "notAssessed"))
        details = str(risk.get("details") or risk.get("recommendedFollowUp") or "").strip()
        note["riskAndSafety"]["recommendedFollowUp"] = details or None
    return validate_session_note_json(note, source_text)


def empty_session_note_json() -> dict[str, Any]:
    return {
        "version": NOTE_VERSION,
        "noteType": "Unknown",
        "sourceBasis": {
            "rawSourceStored": False,
            "inputUsed": "sessionTranscript",
            "extractionConfidence": "low",
        },
        "client": {
            "nameUsedInSession": None,
            "roleOrContext": None,
            "demographicsMentioned": [],
        },
        "sessionSummary": {
            "briefSummary": "",
            "mainClinicalThemes": [],
        },
        "presentingConcern": {
            "primaryConcern": None,
            "onsetContext": None,
            "durationMentioned": None,
            "clientDescription": [],
        },
        "relevantHistory": {
            "mentalHealthHistory": {
                "previousEpisodesMentioned": None,
                "details": None,
            },
            "educationOrWork": {
                "currentStatus": None,
                "context": None,
                "impact": None,
            },
            "relationships": {
                "relevantEvents": [],
                "impact": None,
            },
            "familyContext": {
                "livingSituation": None,
                "familyInvolvement": None,
            },
        },
        "currentStressors": [],
        "symptomsAndFunctioning": {
            "emotionalSymptoms": [],
            "cognitiveSymptoms": [],
            "physicalSymptoms": [],
            "behaviouralPatterns": [],
            "educationImpact": None,
            "workImpact": None,
            "socialImpact": None,
            "dailyRoutineImpact": None,
        },
        "cbtFormulation": {
            "situationExamples": [],
            "maintainingFactors": [],
            "protectiveFactors": [],
            "possibleCognitiveDistortions": [],
            "coreBeliefsOrSchemasToExplore": [],
        },
        "riskAndSafety": {
            "status": "notAssessed",
            "suicidalIdeation": "notAssessed",
            "selfHarm": "notAssessed",
            "homicidalIdeation": "notAssessed",
            "riskIndicatorsMentioned": [],
            "protectiveIndicatorsMentioned": [],
            "recommendedFollowUp": None,
        },
        "interventionsUsedInSession": [],
        "clientResponseToSession": {
            "engagement": None,
            "motivation": None,
            "insight": None,
            "notableResponses": [],
        },
        "clinicalImpression": {
            "summary": "",
            "diagnosisStatus": "notDiagnosedFromTranscript",
            "areasForFurtherAssessment": [],
        },
        "progressSignals": {
            "progressSinceLastSession": [],
            "setbacksOrBarriers": [],
            "betweenSessionPractice": {
                "assigned": [],
                "attempted": [],
                "helped": [],
                "barriers": [],
            },
            "observedProgress": [],
            "clientReportedProgress": [],
            "openClinicalQuestions": [],
            "nextSessionDecisionPoints": [],
        },
        "planAndFollowUp": {
            "therapyDirection": [],
            "possibleHomework": [],
            "nextSessionFocus": [],
        },
        "uncertaintyFlags": [],
    }


def validate_session_note_json(value: Any, source_text: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Session note must be a JSON object.")
    _validate_no_underscore_keys(value)
    if "progressSignals" not in value:
        value = {**value, "progressSignals": empty_session_note_json()["progressSignals"]}
    missing = NOTE_TOP_LEVEL_KEYS - set(value)
    extra = set(value) - NOTE_TOP_LEVEL_KEYS
    if missing:
        raise ValueError(f"Session note JSON is missing required keys: {', '.join(sorted(missing))}.")
    if extra:
        raise ValueError(f"Session note JSON has unsupported keys: {', '.join(sorted(extra))}.")
    if value.get("version") != NOTE_VERSION:
        raise ValueError(f"Session note version must be {NOTE_VERSION}.")
    value = _schema_compliant_session_note(value, source_text or "")
    if value.get("noteType") not in NOTE_TYPES:
        raise ValueError("noteType is not supported.")
    source_basis = value.get("sourceBasis")
    if not isinstance(source_basis, dict):
        raise ValueError("sourceBasis must be an object.")
    if source_basis.get("rawSourceStored") is not False:
        raise ValueError("sourceBasis.rawSourceStored must be false.")
    if source_basis.get("inputUsed") != "sessionTranscript":
        raise ValueError("sourceBasis.inputUsed must be sessionTranscript.")
    if source_basis.get("extractionConfidence") not in EXTRACTION_CONFIDENCE_VALUES:
        raise ValueError("sourceBasis.extractionConfidence must be low, medium, or high.")
    risk = value.get("riskAndSafety")
    if not isinstance(risk, dict):
        raise ValueError("riskAndSafety must be an object.")
    if risk.get("status") not in RISK_ASSESSMENT_VALUES:
        raise ValueError("riskAndSafety.status is not supported.")
    for key in ["suicidalIdeation", "selfHarm", "homicidalIdeation"]:
        if risk.get(key) not in RISK_ITEM_VALUES:
            raise ValueError(f"riskAndSafety.{key} is not supported.")
    _validate_clinical_safety(value, source_text or "")
    return json_safe(value)


def _normalize_generated_session_note(value: Any, source_text: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Generated session note must be a JSON object.")
    clean = _camelize_keys(value)
    if set(clean) == NOTE_TOP_LEVEL_KEYS:
        return clean
    if any(key in clean for key in {"summary", "sourceBasis", "riskAndSafety"}) and not any(
        key in clean for key in {"sessionSummary", "clinicalImpression"}
    ):
        return normalize_reviewed_note_json(clean, source_text)
    note = empty_session_note_json()
    _deep_update_known_keys(note, clean)
    summary = str(
        clean.get("summary")
        or (clean.get("sessionSummary") or {}).get("briefSummary")
        or (clean.get("clinicalImpression") or {}).get("summary")
        or ""
    ).strip()
    if summary:
        note["sessionSummary"]["briefSummary"] = summary
        note["clinicalImpression"]["summary"] = summary
    note["version"] = NOTE_VERSION
    note["sourceBasis"]["rawSourceStored"] = False
    note["sourceBasis"]["inputUsed"] = "sessionTranscript"
    return note


def _camelize_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {_snake_to_camel(str(key)): _camelize_keys(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_camelize_keys(item) for item in value]
    return value


def _snake_to_camel(value: str) -> str:
    if "_" not in value:
        return value
    parts = [part for part in value.split("_") if part]
    if not parts:
        return value
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _deep_update_known_keys(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key not in target:
            continue
        if isinstance(target[key], dict) and isinstance(value, dict):
            _deep_update_known_keys(target[key], value)
        else:
            target[key] = value


def documentation_session_to_dict(item: DocumentationSession) -> dict[str, Any]:
    return {
        "id": item.id,
        "tenant_id": item.tenant_id,
        "patient_id": item.patient_id,
        "therapist_id": item.therapist_id,
        "referral_id": item.referral_id,
        "appointment_id": item.appointment_id,
        "title": item.title,
        "patient_label_snapshot": item.patient_label_snapshot,
        "therapist_label_snapshot": item.therapist_label_snapshot,
        "status": item.status,
        "created_at": iso_or_none(item.created_at),
        "updated_at": iso_or_none(item.updated_at),
    }


def documentation_patient_session_to_dict(session: Session, item: DocumentationSession) -> dict[str, Any]:
    has_transcript = (
        session.scalar(
            select(DocumentationSessionText.id)
            .where(DocumentationSessionText.documentation_session_id == item.id)
            .limit(1)
        )
        is not None
    )
    latest_note = session.scalar(
        select(DocumentationSessionNote)
        .where(DocumentationSessionNote.documentation_session_id == item.id)
        .order_by(DocumentationSessionNote.created_at.desc())
        .limit(1)
    )
    note_status = latest_note.status if latest_note is not None else "no_draft"
    return {
        **documentation_session_to_dict(item),
        "session_date": iso_or_none(item.created_at),
        "transcript_status": "transcript stored" if has_transcript else "no transcript",
        "has_transcript": has_transcript,
        "note_status": note_status,
        "latest_note_id": latest_note.id if latest_note is not None else None,
        "reviewed_at": iso_or_none(latest_note.reviewed_at) if latest_note is not None else None,
        "generated_at": iso_or_none(latest_note.generated_at) if latest_note is not None else None,
    }


def documentation_text_to_dict(text: DocumentationSessionText) -> dict[str, Any]:
    return {
        "id": text.id,
        "tenant_id": text.tenant_id,
        "documentation_session_id": text.documentation_session_id,
        "text": text.text,
        "input_type": text.input_type,
        "source_metadata": json_safe(text.source_metadata),
        "raw_source_stored": text.raw_source_stored,
        "created_at": iso_or_none(text.created_at),
        "updated_at": iso_or_none(text.updated_at),
    }


def documentation_note_to_dict(note: DocumentationSessionNote) -> dict[str, Any]:
    return {
        "id": note.id,
        "tenant_id": note.tenant_id,
        "documentation_session_id": note.documentation_session_id,
        "source_text_id": note.source_text_id,
        "note_json": json_safe(note.note_json),
        "reviewed_json": json_safe(note.reviewed_json),
        "status": note.status,
        "generator": note.generator,
        "model": note.model,
        "generated_at": iso_or_none(note.generated_at),
        "reviewed_at": iso_or_none(note.reviewed_at),
        "reviewer_id": note.reviewer_id,
        "created_at": iso_or_none(note.created_at),
        "updated_at": iso_or_none(note.updated_at),
    }


def _schema_compliant_session_note(note: dict[str, Any], source_text: str) -> dict[str, Any]:
    clean = json_safe(note)
    clean["noteType"] = clean.get("noteType") if clean.get("noteType") in NOTE_TYPES else "Unknown"
    if not isinstance(clean.get("sourceBasis"), dict):
        clean["sourceBasis"] = {}
    clean["sourceBasis"]["rawSourceStored"] = False
    clean["sourceBasis"]["inputUsed"] = "sessionTranscript"
    if clean["sourceBasis"].get("extractionConfidence") not in EXTRACTION_CONFIDENCE_VALUES:
        clean["sourceBasis"]["extractionConfidence"] = "medium" if source_text.strip() else "low"
    clean["riskAndSafety"] = _normalized_risk_and_safety(clean.get("riskAndSafety"), source_text)
    if not isinstance(clean.get("uncertaintyFlags"), list):
        clean["uncertaintyFlags"] = []
    if not clean["uncertaintyFlags"]:
        clean["uncertaintyFlags"] = _minimal_uncertainty_flags(source_text)
    clinical = clean.get("clinicalImpression")
    if isinstance(clinical, dict) and not clinical.get("diagnosisStatus"):
        clinical["diagnosisStatus"] = "notDiagnosedFromTranscript"
    return clean


def _normalized_risk_and_safety(value: Any, source_text: str) -> dict[str, Any]:
    source_lower = source_text.lower()
    risk = value if isinstance(value, dict) else {}
    status = _camel_risk_status(str(risk.get("status") or "notAssessed"))
    if "not directly assessed" in source_lower or "not assessed" in source_lower:
        status = "notAssessed"
    elif any(term in source_lower for term in ["denied risk", "denied safety concerns", "no safety concerns", "no acute risk"]):
        status = "assessed"
    elif any(term in source_lower for term in ["suicide", "suicidal", "self-harm", "self harm", "not wanting to be alive", "harm myself", "harm themselves"]):
        status = "partiallyAssessed" if status == "notAssessed" else status
    return {
        "status": status if status in RISK_ASSESSMENT_VALUES else "notAssessed",
        "suicidalIdeation": _risk_item_value(risk.get("suicidalIdeation"), allow_passive_active=True),
        "selfHarm": _risk_item_value(risk.get("selfHarm")),
        "homicidalIdeation": _risk_item_value(risk.get("homicidalIdeation")),
        "riskIndicatorsMentioned": _string_list(risk.get("riskIndicatorsMentioned")),
        "protectiveIndicatorsMentioned": _string_list(risk.get("protectiveIndicatorsMentioned")),
        "recommendedFollowUp": risk.get("recommendedFollowUp") if isinstance(risk.get("recommendedFollowUp"), str) else None,
    }


def _minimal_uncertainty_flags(source_text: str) -> list[dict[str, str]]:
    flags = []
    lower = source_text.lower()
    if "risk" not in lower and "safety" not in lower:
        flags.append({"item": "Risk and safety", "reason": "Risk and safety assessment was not documented in the transcript."})
    if "plan" not in lower:
        flags.append({"item": "Plan and follow-up", "reason": "Plan details were limited or absent in the transcript."})
    return flags or [{"item": "Transcript limits", "reason": "No additional uncertainty was identified beyond the provided transcript."}]


def _validate_no_underscore_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if "_" in str(key):
                raise ValueError(f"Session note key contains underscore: {path}.{key}")
            _validate_no_underscore_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_no_underscore_keys(child, f"{path}[{index}]")


def _camel_risk_status(value: str) -> str:
    normalized = value.strip()
    return {
        "not_assessed": "notAssessed",
        "mentioned": "partiallyAssessed",
        "assessed_denied": "assessed",
        "notAssessed": "notAssessed",
        "partiallyAssessed": "partiallyAssessed",
        "assessed": "assessed",
    }.get(normalized, "notAssessed")


def _risk_item_value(value: Any, *, allow_passive_active: bool = False) -> str:
    normalized = str(value or "notAssessed").strip()
    allowed = {"notAssessed", "denied", "present", "unclear"}
    if allow_passive_active:
        allowed |= {"passive", "active"}
    return normalized if normalized in allowed else "notAssessed"


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _unique(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _select_source_text(
    session: Session,
    documentation_session_id: str,
    source_text_id: str | None,
) -> DocumentationSessionText:
    if source_text_id:
        source_text = session.get(DocumentationSessionText, source_text_id)
        if source_text is None:
            raise KeyError(f"Unknown documentation session text: {source_text_id}")
        if source_text.documentation_session_id != documentation_session_id:
            raise ValueError("Session text does not belong to the documentation session.")
        return source_text
    source_text = session.scalar(
        select(DocumentationSessionText)
        .where(DocumentationSessionText.documentation_session_id == documentation_session_id)
        .order_by(DocumentationSessionText.created_at.desc())
        .limit(1)
    )
    if source_text is None:
        raise ValueError("Add transcript text before generating a note.")
    return source_text


def _generate_fake_note(source_text: str) -> dict[str, Any]:
    sentences = _sentences(source_text)
    lower = source_text.lower()
    risk_not_assessed = "not directly assessed" in lower or "not assessed" in lower
    denied_terms = ["denied risk", "denied safety concerns", "no safety concerns", "no risk"]
    risk_denied = any(phrase in lower for phrase in denied_terms)
    risk_phrases = [
        "not wanting to be alive",
        "doesn't want to be alive",
        "does not want to be alive",
        "suicide",
        "suicidal",
        "self-harm",
        "self harm",
        "harm myself",
        "harm themselves",
        "harm others",
        "harming others",
    ]
    risk_mentions = [sentence for sentence in sentences if any(phrase in sentence.lower() for phrase in risk_phrases)]
    risk_status = "mentioned" if risk_mentions else "not_assessed" if risk_not_assessed or not risk_denied else "assessed_denied"
    risk_details = "Risk and safety concerns were not directly assessed during this session." if risk_not_assessed else ""
    if risk_mentions:
        risk_details = " ".join(risk_mentions)
    if risk_denied and not risk_not_assessed and not risk_mentions:
        risk_details = "Source text states that risk or safety concerns were assessed and denied."
    interventions = [sentence for sentence in sentences if "therapist" in sentence.lower()]
    subjective = [
        sentence
        for sentence in sentences
        if any(term in sentence.lower() for term in ["patient", "client", "reported", "described", "shared"])
    ]
    topics = []
    for phrase in ["sleep difficulty", "sleep difficulties", "unfinished work tasks", "tension with their partner", "grounding exercise"]:
        if phrase in lower:
            topics.append(phrase)
    flags = []
    if risk_not_assessed or not risk_denied and not risk_mentions:
        flags.append("Risk and safety were not directly assessed in the provided text.")
    if not interventions:
        flags.append("Therapist interventions are missing or unclear in the provided text.")
    if not any("plan" in sentence.lower() for sentence in sentences):
        flags.append("Plan was not directly documented in the provided text.")
    note = empty_session_note_json()
    note["noteType"] = "ProgressNote"
    note["sourceBasis"]["extractionConfidence"] = "medium" if sentences else "low"
    note["sessionSummary"]["briefSummary"] = "Draft generated from the stored transcript. Review against the source text before saving."
    note["sessionSummary"]["mainClinicalThemes"] = topics
    note["presentingConcern"]["clientDescription"] = subjective
    note["symptomsAndFunctioning"]["behaviouralPatterns"] = [
        sentence for sentence in sentences if "pattern" in sentence.lower() or "repeated" in sentence.lower()
    ]
    note["riskAndSafety"] = {
        "status": "partiallyAssessed" if risk_status == "mentioned" else "notAssessed" if risk_status == "not_assessed" else "assessed",
        "suicidalIdeation": "unclear" if risk_mentions else "notAssessed",
        "selfHarm": "unclear" if any("self" in item.lower() for item in risk_mentions) else "notAssessed",
        "homicidalIdeation": "notAssessed",
        "riskIndicatorsMentioned": [risk_details] if risk_details and risk_mentions else [],
        "protectiveIndicatorsMentioned": [],
        "recommendedFollowUp": risk_details or None,
    }
    note["interventionsUsedInSession"] = [
        {"intervention": sentence, "description": sentence, "evidence": [sentence]} for sentence in interventions
    ]
    note["planAndFollowUp"]["possibleHomework"] = [sentence for sentence in sentences if "practice" in sentence.lower()]
    note["planAndFollowUp"]["nextSessionFocus"] = [sentence for sentence in sentences if "plan" in sentence.lower()]
    note["clinicalImpression"]["summary"] = note["sessionSummary"]["briefSummary"]
    note["uncertaintyFlags"] = [{"item": "Generated draft", "reason": item} for item in flags]
    return note


def _generate_with_hugging_face(source_text: str) -> tuple[dict[str, Any], str]:
    token = _hf_token()
    if not token:
        raise ValueError("HF_TOKEN or HUGGINGFACE_API_KEY is required when NOTE_GENERATOR=huggingface.")
    errors = []
    for model in _hf_text_model_candidates():
        try:
            content = _hf_chat_completion(
                token,
                model,
                [
                    {"role": "system", "content": _system_prompt()},
                    {"role": "user", "content": f"Session transcript:\n{source_text}"},
                ],
                max_tokens=4200,
                error_label=f"Hugging Face note generation with {model}",
            )
            try:
                return _normalize_generated_session_note(json.loads(_extract_json_text(content)), source_text), model
            except json.JSONDecodeError:
                repaired = _repair_json_with_hugging_face(token, model, content)
                return _normalize_generated_session_note(json.loads(_extract_json_text(repaired)), source_text), model
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{model}: {exc}")
    raise ValueError("Hugging Face note generation failed for all configured text models. " + " | ".join(errors))


def _repair_json_with_hugging_face(token: str, model: str, content: str) -> str:
    return _hf_chat_completion(
        token,
        model,
        [
            {
                "role": "system",
                "content": "Repair the following text into valid JSON only. Do not change the content. Do not add explanations.",
            },
            {"role": "user", "content": content},
        ],
        max_tokens=4200,
        error_label="Hugging Face JSON repair",
    )


def _hf_chat_completion(
    token: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    error_label: str,
    use_response_format: bool = True,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if use_response_format:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        f"{_hf_chat_base_url()}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_hf_json_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if use_response_format and exc.code in {400, 422}:
            return _hf_chat_completion(
                token,
                model,
                messages,
                max_tokens=max_tokens,
                error_label=error_label,
                use_response_format=False,
            )
        raise ValueError(f"{error_label} failed: HTTP {exc.code} {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"{error_label} failed: {exc.reason}") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"{error_label} response did not include a chat completion message.") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"{error_label} returned an empty response.")
    return content


def _transcribe_audio_with_hugging_face(audio_bytes: bytes, *, filename: str, content_type: str | None = None) -> str:
    token = _hf_token()
    if not token:
        raise ValueError("HF_TOKEN or HUGGINGFACE_API_KEY is required for Hugging Face audio transcription.")
    model = os.getenv("HF_ASR_MODEL", DEFAULT_HF_ASR_MODEL).strip() or DEFAULT_HF_ASR_MODEL
    model_path = urllib.parse.quote(model, safe="/")
    base_url = os.getenv("HF_TASK_BASE_URL", DEFAULT_HF_TASK_BASE_URL).rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/{model_path}",
        data=audio_bytes,
        headers=_hf_binary_headers(token, content_type or _audio_content_type(filename)),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Hugging Face ASR failed: HTTP {exc.code} {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Hugging Face ASR failed: {exc.reason}") from exc
    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Hugging Face ASR returned an empty transcript.")
    return text.strip()


def _extract_visual_text_with_hugging_face(
    file_bytes: bytes,
    *,
    filename: str,
    content_type: str,
    file_kind: str,
) -> str:
    token = _hf_token()
    if not token:
        raise ValueError("HF_TOKEN or HUGGINGFACE_API_KEY is required for Hugging Face image/PDF text extraction.")
    model = os.getenv("HF_IMAGE_TO_TEXT_MODEL", DEFAULT_HF_IMAGE_TO_TEXT_MODEL).strip() or DEFAULT_HF_IMAGE_TO_TEXT_MODEL
    encoded = base64.b64encode(file_bytes).decode("ascii")
    prompt = (
        "Extract the readable text from this clinical documentation upload. "
        "Return only the extracted transcript-style text. Preserve speaker labels, line breaks, and clinically relevant wording. "
        "If text is unclear, mark it as [unclear]. Do not summarize and do not add facts."
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{content_type};base64,{encoded}",
                        },
                    },
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 2400 if file_kind == "pdf" else 1400,
    }
    request = urllib.request.Request(
        f"{_hf_chat_base_url()}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_hf_json_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Hugging Face image/PDF extraction failed: HTTP {exc.code} {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Hugging Face image/PDF extraction failed: {exc.reason}") from exc
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Hugging Face image/PDF extraction did not return text.") from exc
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Hugging Face image/PDF extraction returned empty text.")
    return text.strip()


def _generate_progress_overview_with_hugging_face(progress_sources: list[dict[str, Any]]) -> dict[str, Any]:
    token = _hf_token()
    if not token:
        raise ValueError("HF_TOKEN or HUGGINGFACE_API_KEY is required when NOTE_GENERATOR=huggingface.")
    if not progress_sources:
        return _source_based_progress_overview(progress_sources)
    errors = []
    for model in _hf_text_model_candidates():
        try:
            content = _hf_chat_completion(
                token,
                model,
                [
                    {
                        "role": "system",
                        "content": (
                            "Return valid JSON only. Use camelCase keys only. Do not use markdown or code fences. "
                            "Create a case-specific longitudinal therapy progress overview using ONLY the provided therapist-reviewed session notes. "
                            "Do not use raw transcripts as evidence. Do not invent attendance problems, mindfulness work, relaxation techniques, mood improvement, medication changes, or safety conclusions unless directly documented in the reviewed notes. "
                            "Do not repeat the same sentence across sections. Each section must answer a distinct clinical question. "
                            "Do not use generic phrases such as support and guidance as needed, daily routine, good understanding of thoughts feelings and behaviors, or developing coping strategies unless those exact clinical details are important and evidenced. "
                            "Calibrate progress language: prefer early progress, emerging insight, some successful experiments, continued impairment, or not yet enough evidence for broad symptom reduction. Do not claim significant improvement unless multiple reviewed notes directly document it. "
                            "First infer the case formulation from the notes, then synthesize change over time. Avoid generic therapy-category filler. "
                            "Every substantive claim must include concrete evidence with sessionNumber or date and a short reviewed-note excerpt or paraphrase that is relevant to that specific section. "
                            "Risk and Safety must cite only direct risk/safety assessment, denial, concern, safety monitoring, protective factor, or safety follow-up evidence. If direct risk/safety evidence is absent, say it is not clearly documented. "
                            "Intervention Response must cite actual interventions and the client's actual response; do not infer response from generic engagement. "
                            "Evidence Gaps must identify genuinely unresolved or undocumented items, not topics that are covered elsewhere in the notes. "
                            "If narrative evidence exists but a structured field is missing, do not call it insufficient data; describe the narrative evidence and flag the structuring gap only in evidenceGaps. "
                            "Use uncertainty only for clinically meaningful unknowns such as unclear diagnosis, unclear risk assessment, pending formal evaluation, or ambiguous treatment response. "
                            "Use trajectory only where conceptually meaningful. For nextSessionPriorities and evidenceGaps set trajectory to null. "
                            "Before returning JSON, self-check: each claim has relevant evidence; no duplicated section summary; no invented attendance, mindfulness, broad mood improvement, or missing data; next-session priorities are specific and actionable. "
                            "Return exactly these top-level keys: overviewSummary, caseFrame, longitudinalPatterns, changesSinceIntake, interventionResponse, stuckPointsAndBarriers, riskAndSafety, openClinicalQuestions, nextSessionPriorities, evidenceGaps. "
                            "Each top-level section must be an object with keys: title, summary, trajectory, supportingDetails, evidence, recommendedFollowUp. "
                            "evidence must be a list of objects with keys sessionNumber, date, detail. trajectory must be null or one of: improving, worsening, stable, fluctuating, ongoing, unclear, insufficientData."
                        ),
                    },
                    {"role": "user", "content": json.dumps(_progress_source_payload(progress_sources), ensure_ascii=False)},
                ],
                max_tokens=2400,
                error_label=f"Hugging Face progress overview with {model}",
            )
            try:
                overview = _normalize_progress_overview(json.loads(_extract_json_text(content)))
            except json.JSONDecodeError:
                repaired = _repair_json_with_hugging_face(token, model, content)
                overview = _normalize_progress_overview(json.loads(_extract_json_text(repaired)))
            overview = _quality_control_progress_overview(overview, progress_sources)
            overview["source_session_count"] = len(progress_sources)
            overview["reviewed_note_count"] = len(progress_sources)
            return overview
        except (ValueError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            errors.append(f"{model}: {exc}")
    raise ValueError("Hugging Face progress overview failed for all configured text models. " + " | ".join(errors))


def _hf_chat_base_url() -> str:
    base_url = os.getenv("HF_BASE_URL", DEFAULT_HF_BASE_URL).rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return base_url


def _hf_text_model() -> str:
    model = os.getenv("HF_LLM_MODEL", DEFAULT_HF_MODEL).strip() or DEFAULT_HF_MODEL
    if model not in ALLOWED_HF_TEXT_MODELS:
        allowed = ", ".join(sorted(ALLOWED_HF_TEXT_MODELS))
        raise ValueError(f"HF_LLM_MODEL must be one of the configured text models: {allowed}.")
    return model


def _hf_text_model_candidates() -> list[str]:
    preferred = _hf_text_model()
    fallback_order = [
        DEFAULT_HF_MODEL,
        "Qwen/Qwen3-235B-A22B-Instruct-2507",
        "meta-llama/Llama-3.1-70B-Instruct",
        "Qwen/Qwen2.5-7B-Instruct",
        "openai/gpt-oss-120b",
    ]
    result = []
    for model in [preferred, *fallback_order]:
        if model in ALLOWED_HF_TEXT_MODELS and model not in result:
            result.append(model)
    return result


def _hf_token() -> str | None:
    for key in ["HF_TOKEN", "HUGGINGFACE_API_KEY", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACEHUB_API_TOKEN"]:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return None


def _hf_json_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "lumen-clinical-workflow/0.1",
    }


def _hf_binary_headers(token: str, content_type: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
        "Accept": "application/json",
        "User-Agent": "lumen-clinical-workflow/0.1",
    }


def _resolve_assigned_patient_key(session: Session, therapist_id: str, patient_key: str) -> Patient:
    clean_key = urllib.parse.unquote(patient_key).strip()
    if not clean_key:
        raise ValueError("Patient key is required.")
    assigned = list_documentation_patients_for_therapist(session, therapist_id)
    normalized_key = _normalize_key(clean_key)
    for item in assigned:
        candidates = [
            item.get("id"),
            item.get("display_name"),
            item.get("patient_label"),
            item.get("patient_name"),
        ]
        if any(_normalize_key(candidate) == normalized_key for candidate in candidates if candidate):
            patient = session.get(Patient, item["id"])
            if patient is None:
                raise KeyError(f"Unknown patient: {item['id']}")
            return patient
    session_matches = session.scalars(
        select(DocumentationSession)
        .where(DocumentationSession.therapist_id == therapist_id)
        .where(DocumentationSession.patient_label_snapshot.is_not(None))
        .order_by(DocumentationSession.updated_at.desc())
    )
    for session_match in session_matches:
        if _normalize_key(session_match.patient_label_snapshot) == normalized_key:
            return _assigned_patient(session, therapist_id, session_match.patient_id)
    raise PermissionError("Patient is not assigned to the current therapist.")


def _documentation_sessions_for_patient(session: Session, therapist_id: str, patient_id: str) -> list[DocumentationSession]:
    _assigned_patient(session, therapist_id, patient_id)
    return list(
        session.scalars(
            select(DocumentationSession)
            .where(DocumentationSession.therapist_id == therapist_id, DocumentationSession.patient_id == patient_id)
            .order_by(DocumentationSession.created_at.desc(), DocumentationSession.updated_at.desc())
        )
    )


def _patient_to_dashboard_dict(patient: Patient) -> dict[str, Any]:
    return {
        "id": patient.id,
        "tenant_id": patient.tenant_id,
        "display_name": patient.display_name,
        "date_of_birth": patient.date_of_birth,
        "contact_email": patient.contact_email,
        "contact_phone": patient.contact_phone,
        "language": patient.language,
        "created_at": iso_or_none(patient.created_at),
        "updated_at": iso_or_none(patient.updated_at),
    }


def _current_progress_overview(session_items: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed_notes = _progress_note_sources(session_items)
    return _empty_progress_overview(
        source_session_count=len(session_items),
        reviewed_note_count=len(reviewed_notes),
        generated_at=None,
    )


def _latest_progress_overview_for_patient(
    session: Session,
    *,
    therapist_id: str,
    patient_id: str,
    fallback_items: list[dict[str, Any]],
) -> dict[str, Any]:
    record = session.scalar(
        select(DocumentationProgressOverview)
        .where(
            DocumentationProgressOverview.therapist_id == therapist_id,
            DocumentationProgressOverview.patient_id == patient_id,
        )
        .order_by(DocumentationProgressOverview.generated_at.desc(), DocumentationProgressOverview.created_at.desc())
        .limit(1)
    )
    if record is None:
        return _current_progress_overview(fallback_items)
    overview = json_safe(record.overview_json)
    if isinstance(overview, dict):
        try:
            normalized = _normalize_progress_overview(overview)
            normalized = _quality_control_progress_overview(normalized, _progress_note_sources(fallback_items))
            normalized["source_session_count"] = normalized.get("source_session_count") or len(fallback_items)
            normalized["reviewed_note_count"] = normalized.get("reviewed_note_count") or len(_progress_note_sources(fallback_items))
            normalized["generated_at"] = overview.get("generated_at") or iso_or_none(record.generated_at)
            return normalized
        except (ValueError, TypeError):
            return _current_progress_overview(fallback_items)
    return _current_progress_overview(fallback_items)


def _progress_note_sources(session_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources = []
    ordered_items = sorted(session_items, key=lambda item: str(item.get("session_date") or item.get("created_at") or ""))
    for index, item in enumerate(ordered_items, start=1):
        reviewed_note = _latest_reviewed_note_from_item(item)
        if reviewed_note is None:
            continue
        note_json = reviewed_note.get("reviewed_json") or reviewed_note.get("note_json") or {}
        if not isinstance(note_json, dict):
            continue
        sources.append(
            {
                "sessionId": item.get("id"),
                "sessionNumber": index,
                "noteId": reviewed_note.get("id"),
                "date": item.get("session_date") or item.get("created_at"),
                "title": item.get("title"),
                "summary": _note_summary(note_json),
                "note": note_json,
                "riskAndSafety": note_json.get("riskAndSafety") or note_json.get("risk_or_safety") or {},
                "interventions": note_json.get("interventionsUsedInSession") or note_json.get("interventions") or [],
                "progressSignals": note_json.get("progressSignals") or {},
                "planAndFollowUp": note_json.get("planAndFollowUp") or {},
                "uncertaintyFlags": note_json.get("uncertaintyFlags") or note_json.get("uncertainty_flags") or [],
            }
        )
    return sources


def _latest_reviewed_note_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    notes = item.get("notes")
    if isinstance(notes, list):
        for note in notes:
            if isinstance(note, dict) and note.get("status") == "reviewed" and isinstance(note.get("reviewed_json"), dict):
                return note
    latest = item.get("latest_note")
    if isinstance(latest, dict) and latest.get("status") == "reviewed" and isinstance(latest.get("reviewed_json"), dict):
        return latest
    return None


def _fake_progress_overview(session_items: list[dict[str, Any]]) -> dict[str, Any]:
    return _source_based_progress_overview(_progress_note_sources(session_items))


def _source_based_progress_overview(sources: list[dict[str, Any]], *, error: str | None = None) -> dict[str, Any]:
    overview = _empty_progress_overview(
        source_session_count=len(sources),
        reviewed_note_count=len(sources),
        generated_at=iso_or_none(utc_now()),
    )
    if not sources:
        overview["overviewSummary"] = _progress_section(
            title="Overview Summary",
            summary="No therapist-reviewed session notes are available yet for longitudinal progress synthesis.",
            trajectory="insufficientData",
            supportingDetails=[],
            evidence=[],
            recommendedFollowUp="Save reviewed session notes before generating a patient progress overview.",
        )
        overview["evidenceGaps"] = _progress_section(
            title="Evidence Gaps",
            summary="Progress synthesis is waiting for reviewed notes; transcripts are not used as the primary clinical source.",
            trajectory="insufficientData",
            supportingDetails=["No reviewed notes available."],
            evidence=[],
            recommendedFollowUp="Review and save at least one session note.",
        )
        return overview
    summaries = _readable_unique([source.get("summary", "") for source in sources], limit=8)
    theme_items = _source_items_with_evidence(sources, _case_theme_values)
    impairment_items = _source_items_with_evidence(sources, _functioning_values)
    intervention_items = _source_items_with_evidence(sources, lambda note: flatten_clinical_items(note.get("interventionsUsedInSession")))
    response_items = _source_items_with_evidence(sources, _client_response_values)
    progress_signal_items = _source_items_with_evidence(sources, _progress_signal_values)
    barrier_items = _source_items_with_evidence(sources, _barrier_values)
    follow_up_items = _source_items_with_evidence(sources, _follow_up_values)
    uncertainty_items = _source_items_with_evidence(sources, _open_question_values)
    risk_items = [
        {"text": item, "evidence": _source_evidence(source, item)}
        for source in sources
        for item in [_risk_summary(source.get("riskAndSafety"))]
        if item
    ]
    all_case_items = _unique_evidence_items([*theme_items, *impairment_items, *barrier_items], limit=8)
    all_change_items = _unique_evidence_items([*progress_signal_items, *response_items], limit=8)
    overview["overviewSummary"] = _progress_section(
        title="Overview Summary",
        summary=_case_specific_summary(sources, all_case_items),
        trajectory="ongoing" if len(sources) > 1 else "unclear",
        supportingDetails=summaries,
        evidence=[_source_evidence(source, source.get("summary", "")) for source in sources if source.get("summary")][:6],
        recommendedFollowUp=None,
    )
    overview["caseFrame"] = _progress_section(
        title="Case Frame",
        summary=_first_text(all_case_items) or "Reviewed notes identify the current clinical focus, but case formulation details remain lightly structured.",
        trajectory="ongoing",
        supportingDetails=[item["text"] for item in all_case_items],
        evidence=[item["evidence"] for item in all_case_items],
        recommendedFollowUp="Keep future reviewed notes anchored to the client's target problems, functional impacts, and maintaining patterns.",
    )
    overview["longitudinalPatterns"] = _progress_section(
        title="Patterns Across Sessions",
        summary=_first_text(_unique_evidence_items([*theme_items, *barrier_items], limit=6)) or "Reviewed notes do not yet show a repeated pattern across multiple sessions.",
        trajectory="ongoing" if len(sources) > 1 and (theme_items or barrier_items) else "unclear",
        supportingDetails=[item["text"] for item in _unique_evidence_items([*theme_items, *barrier_items], limit=6)],
        evidence=[item["evidence"] for item in _unique_evidence_items([*theme_items, *barrier_items], limit=6)],
        recommendedFollowUp=None,
    )
    overview["changesSinceIntake"] = _progress_section(
        title="Change Since Intake",
        summary=_first_text(all_change_items) or "Reviewed notes identify treatment targets, but do not yet make a concrete before-and-after progress claim.",
        trajectory="fluctuating" if len(sources) > 1 and all_change_items else "unclear",
        supportingDetails=[item["text"] for item in all_change_items],
        evidence=[item["evidence"] for item in all_change_items],
        recommendedFollowUp="Document concrete before/after changes, setbacks, and between-session experiments in reviewed notes.",
    )
    overview["interventionResponse"] = _progress_section(
        title="Interventions And Response",
        summary=_first_text(_unique_evidence_items([*intervention_items, *response_items], limit=6)) or "Reviewed notes do not clearly link interventions to client response.",
        trajectory="ongoing" if intervention_items or response_items else "unclear",
        supportingDetails=[item["text"] for item in _unique_evidence_items([*intervention_items, *response_items], limit=6)],
        evidence=[item["evidence"] for item in _unique_evidence_items([*intervention_items, *response_items], limit=6)],
        recommendedFollowUp="When documenting interventions, include what the client tried and what changed afterward.",
    )
    overview["stuckPointsAndBarriers"] = _progress_section(
        title="Stuck Points And Barriers",
        summary=_first_text(barrier_items) or "Specific stuck points are not clearly separated from general themes in the reviewed notes.",
        trajectory="ongoing" if barrier_items else "unclear",
        supportingDetails=[item["text"] for item in _unique_evidence_items(barrier_items, limit=6)],
        evidence=[item["evidence"] for item in _unique_evidence_items(barrier_items, limit=6)],
        recommendedFollowUp=None,
    )
    overview["riskAndSafety"] = _progress_section(
        title="Risk And Safety",
        summary=_first_text(risk_items) or "Risk and safety status is not clearly documented in the reviewed notes.",
        trajectory="unclear" if not risk_items or any("not assessed" in item["text"].lower() for item in risk_items) else "ongoing",
        supportingDetails=[item["text"] for item in _unique_evidence_items(risk_items, limit=6)],
        evidence=[item["evidence"] for item in _unique_evidence_items(risk_items, limit=6)],
        recommendedFollowUp="Assess and document risk/safety directly when clinically appropriate.",
    )
    overview["openClinicalQuestions"] = _progress_section(
        title="Open Clinical Questions",
        summary=_first_text(uncertainty_items) or "No specific open clinical questions were clearly documented as separate items.",
        trajectory="ongoing" if uncertainty_items else "unclear",
        supportingDetails=[item["text"] for item in _unique_evidence_items(uncertainty_items, limit=6)],
        evidence=[item["evidence"] for item in _unique_evidence_items(uncertainty_items, limit=6)],
        recommendedFollowUp=None,
    )
    overview["nextSessionPriorities"] = _progress_section(
        title="Next Session Priorities",
        summary=_first_text(follow_up_items) or "Next-session priorities are not clearly documented in the reviewed notes.",
        trajectory=None,
        supportingDetails=[item["text"] for item in _unique_evidence_items(follow_up_items, limit=6)],
        evidence=[item["evidence"] for item in _unique_evidence_items(follow_up_items, limit=6)],
        recommendedFollowUp=_first_text(follow_up_items),
    )
    gap_details = []
    if error:
        gap_details.append(f"Model generation fallback used: {error[:240]}")
    for label, values in [
        ("intervention response", intervention_items or response_items),
        ("concrete change since intake", all_change_items),
        ("risk/safety status", risk_items),
        ("open clinical questions", uncertainty_items),
    ]:
        if not values:
            gap_details.append(f"Reviewed notes do not clearly structure {label}.")
    overview["evidenceGaps"] = _progress_section(
        title="Evidence Gaps",
        summary=gap_details[0] if gap_details else "Reviewed notes contain enough structured material for a grounded high-level overview.",
        trajectory=None,
        supportingDetails=gap_details,
        evidence=[],
        recommendedFollowUp="Improve future notes by documenting progress signals, barriers, intervention response, and open clinical questions.",
    )
    return _quality_control_progress_overview(overview, sources, fallback=overview)


def _case_specific_summary(sources: list[dict[str, Any]], items: list[dict[str, Any]]) -> str:
    if items:
        return f"Across {len(sources)} reviewed note{'s' if len(sources) != 1 else ''}, the clearest clinical material is: {items[0]['text']}"
    summaries = _readable_unique([source.get("summary", "") for source in sources], limit=2)
    if summaries:
        return f"Across {len(sources)} reviewed note{'s' if len(sources) != 1 else ''}, the overview is grounded in therapist-reviewed summaries."
    return f"Across {len(sources)} reviewed note{'s' if len(sources) != 1 else ''}, reviewed material is available but not yet richly structured."


def _quality_control_progress_overview(
    overview: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = fallback or _source_based_progress_overview(sources)
    source_blob = _progress_source_text_blob(sources)
    result = dict(overview)
    for key in ["overviewSummary", *PROGRESS_SECTION_KEYS]:
        section = result.get(key)
        if not isinstance(section, dict):
            result[key] = fallback.get(key) if isinstance(fallback.get(key), dict) else _empty_progress_section(_humanize_identifier(key))
            continue
        if key in PROGRESS_STATIC_SECTION_KEYS:
            section["trajectory"] = None
        elif section.get("trajectory") not in PROGRESS_TRAJECTORIES:
            section["trajectory"] = fallback.get(key, {}).get("trajectory") if isinstance(fallback.get(key), dict) else "unclear"
        if _section_needs_fallback(key, section, source_blob, sources):
            result[key] = fallback.get(key, section)
    _dedupe_progress_summaries(result, fallback)
    _tighten_evidence_gaps(result, sources)
    return result


def _section_needs_fallback(key: str, section: dict[str, Any], source_blob: str, sources: list[dict[str, Any]]) -> bool:
    text = _progress_section_text(section)
    if not str(section.get("summary") or "").strip():
        return True
    lowered = text.lower()
    if any(phrase in lowered and phrase not in source_blob for phrase in PROGRESS_GENERIC_PHRASES):
        return True
    if any(phrase in lowered and phrase not in source_blob for phrase in PROGRESS_OVERSTATEMENT_PHRASES):
        return True
    if key == "riskAndSafety":
        return not _section_has_relevant_evidence(section, _risk_evidence_terms()) and bool(_direct_risk_items(sources))
    if key == "interventionResponse":
        return not _section_has_relevant_evidence(section, _intervention_evidence_terms()) and bool(
            _source_items_with_evidence(sources, lambda note: flatten_clinical_items(note.get("interventionsUsedInSession")))
        )
    if key in {"caseFrame", "longitudinalPatterns", "changesSinceIntake", "stuckPointsAndBarriers"}:
        evidence = section.get("evidence")
        if isinstance(evidence, list) and not evidence and any(term in lowered for term in ["improvement", "progress", "reduction", "worsening"]):
            return True
    return False


def _dedupe_progress_summaries(result: dict[str, Any], fallback: dict[str, Any]) -> None:
    seen: dict[str, str] = {}
    for key in PROGRESS_SECTION_KEYS:
        section = result.get(key)
        if not isinstance(section, dict):
            continue
        normalized = _normalize_sentence_key(section.get("summary"))
        if not normalized:
            continue
        prior_key = seen.get(normalized)
        if prior_key:
            fallback_section = fallback.get(key)
            if isinstance(fallback_section, dict) and _normalize_sentence_key(fallback_section.get("summary")) != normalized:
                result[key] = fallback_section
            else:
                section["summary"] = _section_distinct_fallback_summary(key)
                section["trajectory"] = None if key in PROGRESS_STATIC_SECTION_KEYS else section.get("trajectory")
        else:
            seen[normalized] = key


def _tighten_evidence_gaps(result: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    section = result.get("evidenceGaps")
    if not isinstance(section, dict):
        return
    details = section.get("supportingDetails") if isinstance(section.get("supportingDetails"), list) else []
    if not details:
        section["trajectory"] = None
        return
    has_risk = bool(_direct_risk_items(sources))
    has_intervention = bool(_source_items_with_evidence(sources, lambda note: flatten_clinical_items(note.get("interventionsUsedInSession"))))
    has_open_questions = bool(_source_items_with_evidence(sources, _open_question_values))
    has_change = bool(_source_items_with_evidence(sources, _progress_signal_values))
    filtered = []
    for detail in details:
        text = str(detail).lower()
        if "risk" in text and has_risk:
            continue
        if "intervention" in text and has_intervention:
            continue
        if "open clinical questions" in text and has_open_questions:
            continue
        if "change since intake" in text and has_change:
            continue
        filtered.append(detail)
    section["supportingDetails"] = _readable_unique([str(item) for item in filtered], limit=6)
    section["summary"] = (
        section["supportingDetails"][0]
        if section["supportingDetails"]
        else "No major evidence gaps are apparent from the reviewed notes used for this overview."
    )
    section["trajectory"] = None


def _progress_section_text(section: dict[str, Any]) -> str:
    parts = [str(section.get("summary") or ""), str(section.get("recommendedFollowUp") or "")]
    details = section.get("supportingDetails")
    if isinstance(details, list):
        parts.extend(str(item) for item in details)
    evidence = section.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict):
                parts.append(str(item.get("detail") or ""))
            else:
                parts.append(str(item))
    return " ".join(parts)


def _progress_source_text_blob(sources: list[dict[str, Any]]) -> str:
    return " ".join(json.dumps(source.get("note") or {}, ensure_ascii=False).lower() for source in sources)


def _section_has_relevant_evidence(section: dict[str, Any], terms: set[str]) -> bool:
    evidence = section.get("evidence")
    if not isinstance(evidence, list):
        return False
    for item in evidence:
        detail = str(item.get("detail") if isinstance(item, dict) else item).lower()
        if any(term in detail for term in terms):
            return True
    return False


def _risk_evidence_terms() -> set[str]:
    return {"risk", "safety", "suicid", "self-harm", "self harm", "harm", "protective", "denied", "monitoring"}


def _intervention_evidence_terms() -> set[str]:
    return {"therapist", "intervention", "practice", "experiment", "homework", "system", "skills", "repair", "response", "tried"}


def _direct_risk_items(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"text": item, "evidence": _source_evidence(source, item)}
        for source in sources
        for item in [_risk_summary(source.get("riskAndSafety"))]
        if item
    ]


def _normalize_sentence_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _section_distinct_fallback_summary(key: str) -> str:
    return {
        "caseFrame": "This section should identify the client's central presenting pattern and maintaining context.",
        "longitudinalPatterns": "This section should focus on patterns recurring across reviewed notes.",
        "changesSinceIntake": "This section should separate early progress from ongoing impairment and avoid broad improvement claims.",
        "interventionResponse": "This section should link specific interventions to documented client responses.",
        "stuckPointsAndBarriers": "This section should identify barriers that remain active in the reviewed notes.",
        "riskAndSafety": "This section should reflect only directly documented risk or safety material.",
        "openClinicalQuestions": "This section should list unresolved clinical questions that affect treatment planning.",
        "nextSessionPriorities": "This section should list specific next clinical actions from the reviewed notes.",
        "evidenceGaps": "This section should identify documentation gaps that remain after reviewing the available notes.",
    }.get(key, "This section needs case-specific evidence from reviewed notes.")


def _source_items_with_evidence(
    sources: list[dict[str, Any]],
    extractor: Callable[[dict[str, Any]], list[str]],
) -> list[dict[str, Any]]:
    items = []
    for source in sources:
        note = source.get("note")
        if not isinstance(note, dict):
            continue
        for text in extractor(note):
            phrase = _readable_phrase(text)
            if phrase:
                items.append({"text": phrase, "evidence": _source_evidence(source, phrase)})
    return _unique_evidence_items(items, limit=24)


def _case_theme_values(note: dict[str, Any]) -> list[str]:
    presenting = note.get("presentingConcern") or {}
    history = note.get("relevantHistory") or {}
    relationships = history.get("relationships") or {}
    family = history.get("familyContext") or {}
    clinical = note.get("clinicalImpression") or {}
    return _readable_unique(
        [
            _note_summary(note),
            *((note.get("sessionSummary") or {}).get("mainClinicalThemes") or []),
            presenting.get("primaryConcern"),
            presenting.get("onsetContext"),
            presenting.get("durationMentioned"),
            *flatten_clinical_items(presenting.get("clientDescription")),
            *flatten_clinical_items(note.get("currentStressors")),
            *flatten_clinical_items(relationships.get("relevantEvents")),
            relationships.get("impact"),
            family.get("livingSituation"),
            family.get("familyInvolvement"),
            clinical.get("summary"),
        ],
        limit=12,
    )


def _functioning_values(note: dict[str, Any]) -> list[str]:
    functioning = note.get("symptomsAndFunctioning") or {}
    return _readable_unique(
        [
            *flatten_clinical_items(functioning.get("emotionalSymptoms")),
            *flatten_clinical_items(functioning.get("cognitiveSymptoms")),
            *flatten_clinical_items(functioning.get("physicalSymptoms")),
            *flatten_clinical_items(functioning.get("behaviouralPatterns")),
            functioning.get("educationImpact"),
            functioning.get("workImpact"),
            functioning.get("socialImpact"),
            functioning.get("dailyRoutineImpact"),
        ],
        limit=12,
    )


def _client_response_values(note: dict[str, Any]) -> list[str]:
    response = note.get("clientResponseToSession") or {}
    return _readable_unique(
        [
            response.get("engagement"),
            response.get("motivation"),
            response.get("insight"),
            *flatten_clinical_items(response.get("notableResponses")),
        ],
        limit=10,
    )


def _progress_signal_values(note: dict[str, Any]) -> list[str]:
    signals = note.get("progressSignals") or {}
    practice = signals.get("betweenSessionPractice") if isinstance(signals, dict) else {}
    if not isinstance(practice, dict):
        practice = {}
    if not isinstance(signals, dict):
        signals = {}
    return _readable_unique(
        [
            *flatten_clinical_items(signals.get("progressSinceLastSession")),
            *flatten_clinical_items(signals.get("setbacksOrBarriers")),
            *flatten_clinical_items(signals.get("observedProgress")),
            *flatten_clinical_items(signals.get("clientReportedProgress")),
            *flatten_clinical_items(practice.get("attempted")),
            *flatten_clinical_items(practice.get("helped")),
            *flatten_clinical_items(practice.get("barriers")),
        ],
        limit=12,
    )


def _follow_up_values(note: dict[str, Any]) -> list[str]:
    plan = note.get("planAndFollowUp") or {}
    signals = note.get("progressSignals") or {}
    values = [
        *flatten_clinical_items(plan.get("therapyDirection")),
        *flatten_clinical_items(plan.get("possibleHomework")),
        *flatten_clinical_items(plan.get("nextSessionFocus")),
    ]
    if isinstance(signals, dict):
        values.extend(flatten_clinical_items(signals.get("nextSessionDecisionPoints")))
    return _readable_unique(values, limit=12)


def _barrier_values(note: dict[str, Any]) -> list[str]:
    formulation = note.get("cbtFormulation") or {}
    signals = note.get("progressSignals") or {}
    return _readable_unique(
        [
            *flatten_clinical_items(note.get("currentStressors")),
            *flatten_clinical_items(formulation.get("maintainingFactors")),
            *flatten_clinical_items(formulation.get("coreBeliefsOrSchemasToExplore")),
            *flatten_clinical_items(signals.get("setbacksOrBarriers") if isinstance(signals, dict) else []),
        ],
        limit=12,
    )


def _open_question_values(note: dict[str, Any]) -> list[str]:
    clinical = note.get("clinicalImpression") or {}
    signals = note.get("progressSignals") or {}
    values = [
        *flatten_clinical_items(note.get("uncertaintyFlags")),
        *flatten_clinical_items(clinical.get("areasForFurtherAssessment")),
    ]
    if isinstance(signals, dict):
        values.extend(flatten_clinical_items(signals.get("openClinicalQuestions")))
        values.extend(flatten_clinical_items(signals.get("nextSessionDecisionPoints")))
    diagnosis_status = clinical.get("diagnosisStatus")
    if diagnosis_status and diagnosis_status != "notDiagnosedFromTranscript":
        values.append(f"Diagnosis status documented as {diagnosis_status}.")
    elif diagnosis_status == "notDiagnosedFromTranscript":
        values.append("Diagnosis was not established from the transcript.")
    return _readable_unique(values, limit=12)


def _source_evidence(source: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "sessionNumber": source.get("sessionNumber"),
        "date": source.get("date"),
        "detail": _snippet(text, 260),
    }


def _unique_evidence_items(items: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        text = _readable_phrase(item.get("text") if isinstance(item, dict) else "")
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        evidence = item.get("evidence") if isinstance(item, dict) else None
        result.append({"text": text, "evidence": evidence or {"sessionNumber": None, "date": None, "detail": text}})
        seen.add(key)
        if len(result) >= limit:
            break
    return result


def _first_text(items: list[dict[str, Any]]) -> str:
    for item in items:
        text = _readable_phrase(item.get("text") if isinstance(item, dict) else item)
        if text:
            return text
    return ""


def flatten_clinical_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            result.append(
                str(
                    item.get("description")
                    or item.get("intervention")
                    or item.get("item")
                    or item.get("reason")
                    or item.get("stressor")
                    or item.get("clientMeaning")
                    or item.get("summary")
                    or ""
                )
            )
    return [item.strip() for item in result if item.strip()]


def _risk_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    status = _readable_phrase(value.get("status") or "")
    details = _readable_unique(
        [
            *_string_list(value.get("riskIndicatorsMentioned")),
            *_string_list(value.get("protectiveIndicatorsMentioned")),
            str(value.get("recommendedFollowUp") or ""),
        ],
        limit=3,
    )
    return " - ".join([item for item in [f"Status: {status}" if status else "", "; ".join(details)] if item])


def _normalize_progress_overview(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Progress overview must be a JSON object.")
    value = _camelize_keys(value)
    _validate_no_underscore_keys(value)
    overview = _empty_progress_overview()
    overview["overviewSummary"] = _normalize_progress_section(
        value.get("overviewSummary"),
        fallback_title="Overview Summary",
        fallback_summary=str(value.get("summary") or value.get("overview") or "").strip(),
    )
    for key in PROGRESS_SECTION_KEYS:
        overview[key] = _normalize_progress_section(value.get(key), fallback_title=_humanize_identifier(key))
    overview["source_session_count"] = int(value.get("sourceSessionCount") or value.get("source_session_count") or 0)
    overview["reviewed_note_count"] = int(value.get("reviewedNoteCount") or value.get("reviewed_note_count") or 0)
    overview["generated_at"] = iso_or_none(utc_now())
    return overview


def _empty_progress_overview(
    *,
    source_session_count: int = 0,
    reviewed_note_count: int = 0,
    generated_at: str | None = None,
) -> dict[str, Any]:
    overview = {
        "overviewSummary": _progress_section(
            title="Overview Summary",
            summary="Generate a progress overview after transcripts and reviewed notes are ready.",
            trajectory="insufficientData",
            supportingDetails=[],
            evidence=[],
            recommendedFollowUp="Generate or refresh the overview when session notes are available.",
        ),
        "source_session_count": source_session_count,
        "reviewed_note_count": reviewed_note_count,
        "generated_at": generated_at,
    }
    for key in PROGRESS_SECTION_KEYS:
        overview[key] = _empty_progress_section(_humanize_identifier(key))
    overview["riskAndSafety"] = _progress_section(
        title="Risk And Safety",
        summary="Risk and safety trend cannot be determined unless risk has been assessed and documented in reviewed notes.",
        trajectory="insufficientData",
        supportingDetails=[],
        evidence=[],
        recommendedFollowUp="Assess and document risk/safety status directly when clinically appropriate.",
    )
    return overview


def _empty_progress_section(title: str) -> dict[str, Any]:
    return _progress_section(
        title=title,
        summary="No therapist-reviewed evidence has been synthesized for this section yet.",
        trajectory="insufficientData",
        supportingDetails=[],
        evidence=[],
        recommendedFollowUp=None,
    )


def _progress_section(
    *,
    title: str,
    summary: str,
    trajectory: str | None,
    supportingDetails: list[str],
    evidence: list[Any] | None = None,
    recommendedFollowUp: str | None = None,
) -> dict[str, Any]:
    return {
        "title": _humanize_identifier(title),
        "summary": _readable_phrase(summary),
        "trajectory": trajectory if trajectory in PROGRESS_TRAJECTORIES else None,
        "supportingDetails": _readable_unique(supportingDetails, limit=6),
        "evidence": _normalize_progress_evidence(evidence or []),
        "recommendedFollowUp": _readable_phrase(recommendedFollowUp) if recommendedFollowUp else None,
    }


def _normalize_progress_section(value: Any, *, fallback_title: str, fallback_summary: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        details = value.get("supportingDetails") or value.get("details") or value.get("items") or []
        if isinstance(details, str):
            details = [details]
        evidence = value.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [{"detail": evidence}]
        return _progress_section(
            title=str(value.get("title") or fallback_title),
            summary=str(value.get("summary") or fallback_summary or ""),
            trajectory=value.get("trajectory") if value.get("trajectory") is not None else value.get("status"),
            supportingDetails=[str(item) for item in details] if isinstance(details, list) else [],
            evidence=evidence if isinstance(evidence, list) else [],
            recommendedFollowUp=value.get("recommendedFollowUp"),
        )
    if isinstance(value, list):
        return _progress_section(
            title=fallback_title,
            summary=fallback_summary or (_readable_phrase(value[0]) if value else ""),
            trajectory="ongoing" if value else "insufficientData",
            supportingDetails=[str(item) for item in value],
            evidence=[],
            recommendedFollowUp=None,
        )
    return _progress_section(
        title=fallback_title,
        summary=fallback_summary or "",
        trajectory="insufficientData",
        supportingDetails=[],
        evidence=[],
        recommendedFollowUp=None,
    )


def _normalize_progress_evidence(value: list[Any]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in value:
        if isinstance(item, dict):
            detail = _readable_phrase(item.get("detail") or item.get("excerpt") or item.get("summary") or "")
            session_number = item.get("sessionNumber")
            date = item.get("date")
        else:
            detail = _readable_phrase(item)
            session_number = None
            date = None
        if not detail:
            continue
        key = (str(session_number or ""), str(date or ""), detail.lower())
        if key in seen:
            continue
        result.append({"sessionNumber": session_number, "date": date, "detail": detail})
        seen.add(key)
        if len(result) >= 6:
            break
    return result


def _readable_unique(values: list[str], *, limit: int = 8) -> list[str]:
    result = []
    seen = set()
    for value in values:
        phrase = _readable_phrase(value)
        key = phrase.lower()
        if phrase and key not in seen:
            result.append(phrase)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def _readable_phrase(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("_", " ")
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if text[0].islower():
        text = f"{text[0].upper()}{text[1:]}"
    return text


def _humanize_identifier(value: Any) -> str:
    text = _readable_phrase(value)
    return text or "Clinical Section"


def _progress_source_payload(progress_sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = []
    for item in progress_sources:
        payload.append(
            {
                "sessionId": item.get("sessionId"),
                "sessionNumber": item.get("sessionNumber"),
                "noteId": item.get("noteId"),
                "date": item.get("date"),
                "title": item.get("title"),
                "reviewedNote": item.get("note"),
                "summary": item.get("summary"),
                "riskAndSafety": item.get("riskAndSafety"),
                "interventions": item.get("interventions"),
                "progressSignals": item.get("progressSignals"),
                "planAndFollowUp": item.get("planAndFollowUp"),
                "uncertaintyFlags": item.get("uncertaintyFlags"),
            }
        )
    return payload


def _normalize_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def _snippet(value: str, limit: int = 240) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return f"{clean[:limit].rstrip()}..."


def _note_summary(note_json: dict[str, Any]) -> str:
    return str(
        note_json.get("summary")
        or (note_json.get("sessionSummary") or {}).get("briefSummary")
        or (note_json.get("clinicalImpression") or {}).get("summary")
        or ""
    )


def _audio_content_type(filename: str) -> str:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return {
        "mp3": "audio/mpeg",
        "mpeg": "audio/mpeg",
        "wav": "audio/wav",
        "m4a": "audio/mp4",
        "mp4": "audio/mp4",
        "webm": "audio/webm",
        "ogg": "audio/ogg",
    }.get(suffix, "application/octet-stream")


def _documentation_upload_kind(filename: str, content_type: str | None) -> tuple[str, str]:
    guessed_type = mimetypes.guess_type(filename or "")[0]
    media_type = (content_type or guessed_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if media_type.startswith("audio/") or suffix in {"mp3", "mpeg", "wav", "m4a", "mp4", "webm", "ogg"}:
        return "audio", media_type if media_type != "application/octet-stream" else _audio_content_type(filename)
    if media_type.startswith("image/") or suffix in {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"}:
        return "image", media_type if media_type.startswith("image/") else guessed_type or "image/jpeg"
    if media_type == "application/pdf" or suffix == "pdf":
        return "pdf", "application/pdf"
    return "unsupported", media_type


def _system_prompt() -> str:
    return """
You are a careful clinical documentation extraction engine for therapist session transcripts.
Return valid JSON only. Do not return markdown, code fences, comments, explanations, or surrounding text.

Task:
- Extract clinically useful structure from the transcript.
- Treat the transcript as the only source of truth.
- Preserve clinically meaningful wording from the client and therapist.
- Prefer complete short sentences in values, not tags or machine labels.
- Separate directly stated facts from clinical interpretations.
- Do not invent information that is missing, unclear, or only partially assessed.
- Use null or "notAssessed" where appropriate instead of guessing.
- Do not diagnose unless the transcript explicitly contains a diagnosis.
- When no diagnosis is explicitly documented, set diagnosisStatus to "notDiagnosedFromTranscript".
- Capture brief transcript evidence snippets for important claims.
- Do not include empty filler objects in lists. If a list has no evidence, return [].
- Use camelCase keys only. Never use snake_case keys.
- JSON must parse with JSON.parse.
- For enum fields, choose one listed value only. Do not copy the pipe-separated option list.

Use exactly this JSON shape and these top-level keys:
{
  "version": "sessionNoteV0.2",
  "noteType": "CBTAssessment | SOAP | DAP | Intake | ProgressNote | Unknown",
  "sourceBasis": {
    "rawSourceStored": false,
    "inputUsed": "sessionTranscript",
    "extractionConfidence": "low | medium | high"
  },
  "client": {
    "nameUsedInSession": null,
    "roleOrContext": null,
    "demographicsMentioned": []
  },
  "sessionSummary": {
    "briefSummary": "",
    "mainClinicalThemes": []
  },
  "presentingConcern": {
    "primaryConcern": null,
    "onsetContext": null,
    "durationMentioned": null,
    "clientDescription": []
  },
  "relevantHistory": {
    "mentalHealthHistory": {
      "previousEpisodesMentioned": null,
      "details": null
    },
    "educationOrWork": {
      "currentStatus": null,
      "context": null,
      "impact": null
    },
    "relationships": {
      "relevantEvents": [],
      "impact": null
    },
    "familyContext": {
      "livingSituation": null,
      "familyInvolvement": null
    }
  },
  "currentStressors": [
    {
      "stressor": "",
      "clientMeaning": "",
      "evidence": []
    }
  ],
  "symptomsAndFunctioning": {
    "emotionalSymptoms": [],
    "cognitiveSymptoms": [],
    "physicalSymptoms": [],
    "behaviouralPatterns": [],
    "educationImpact": null,
    "workImpact": null,
    "socialImpact": null,
    "dailyRoutineImpact": null
  },
  "cbtFormulation": {
    "situationExamples": [
      {
        "situation": "",
        "automaticThoughts": [],
        "beliefRatings": [
          {
            "thought": "",
            "beliefPercent": null
          }
        ],
        "emotions": [],
        "bodySensations": [],
        "behaviours": [],
        "evidence": []
      }
    ],
    "maintainingFactors": [],
    "protectiveFactors": [],
    "possibleCognitiveDistortions": [
      {
        "pattern": "",
        "example": ""
      }
    ],
    "coreBeliefsOrSchemasToExplore": []
  },
  "riskAndSafety": {
    "status": "notAssessed | partiallyAssessed | assessed",
    "suicidalIdeation": "notAssessed | denied | passive | active | unclear",
    "selfHarm": "notAssessed | denied | present | unclear",
    "homicidalIdeation": "notAssessed | denied | present | unclear",
    "riskIndicatorsMentioned": [],
    "protectiveIndicatorsMentioned": [],
    "recommendedFollowUp": null
  },
  "interventionsUsedInSession": [
    {
      "intervention": "",
      "description": "",
      "evidence": []
    }
  ],
  "clientResponseToSession": {
    "engagement": null,
    "motivation": null,
    "insight": null,
    "notableResponses": []
  },
  "clinicalImpression": {
    "summary": "",
    "diagnosisStatus": "notDiagnosedFromTranscript",
    "areasForFurtherAssessment": []
  },
  "progressSignals": {
    "progressSinceLastSession": [],
    "setbacksOrBarriers": [],
    "betweenSessionPractice": {
      "assigned": [],
      "attempted": [],
      "helped": [],
      "barriers": []
    },
    "observedProgress": [],
    "clientReportedProgress": [],
    "openClinicalQuestions": [],
    "nextSessionDecisionPoints": []
  },
  "planAndFollowUp": {
    "therapyDirection": [],
    "possibleHomework": [],
    "nextSessionFocus": []
  },
  "uncertaintyFlags": [
    {
      "item": "",
      "reason": ""
    }
  ]
}

Extraction guidance:
- For evidence arrays, include short verbatim snippets from the transcript when useful.
- For currentStressors, include the stressor, the client's meaning or appraisal, and evidence.
- For cbtFormulation, include concrete situation examples, automatic thoughts, emotions, body sensations, behaviours, maintaining factors, protective factors, and possible cognitive distortions only when supported by the transcript.
- For riskAndSafety, use notAssessed if risk was not discussed. Use partiallyAssessed when risk language appears but full assessment is incomplete.
- For progressSignals, capture only session-specific change, setbacks, between-session practice, client-reported progress, observed progress, and next clinical decision points that are directly supported by the transcript.
- For uncertaintyFlags, include missing risk assessment, unclear timeline, unclear diagnosis, unclear plan, or partially assessed information.
- Do not over-compress clinically meaningful material into keywords such as "low_mood" or "negative_thoughts"; write readable clinical sentences instead.
""".strip()


def _validate_clinical_safety(note: dict[str, Any], source_text: str) -> None:
    source_lower = source_text.lower()
    note_text = json.dumps(note, ensure_ascii=False).lower()
    flags = note.setdefault("uncertaintyFlags", [])
    if not isinstance(flags, list):
        flags = []
        note["uncertaintyFlags"] = flags
    source_mentions_anxiety = "anxiety" in source_lower or "anxious" in source_lower
    if "anxiety" in note_text and not source_mentions_anxiety:
        flags.append(
            {
                "item": "Anxiety wording",
                "reason": "Generated note used anxiety-related wording that should be reviewed against the transcript.",
            }
        )
    diagnoses = ["diagnosis", "diagnosed", "major depressive", "ptsd", "generalized anxiety disorder"]
    clinical = note.setdefault("clinicalImpression", {})
    diagnosis_status = str(clinical.get("diagnosisStatus") or "")
    if diagnosis_status != "notDiagnosedFromTranscript" and not any(term in source_lower for term in diagnoses):
        clinical["diagnosisStatus"] = "notDiagnosedFromTranscript"
        areas = clinical.setdefault("areasForFurtherAssessment", [])
        if isinstance(areas, list):
            areas.append("Review diagnostic wording; transcript did not explicitly document a diagnosis.")
        flags.append(
            {
                "item": "Diagnosis wording",
                "reason": "Diagnostic status was reset because the transcript did not explicitly document a diagnosis.",
            }
        )
    denied_terms = ["no risk", "denied risk", "no safety concerns"]
    assessed_denied = any(term in source_lower for term in denied_terms)
    if any(term in note_text for term in denied_terms) and not assessed_denied:
        risk = note.setdefault("riskAndSafety", {})
        risk["status"] = "notAssessed"
        risk["suicidalIdeation"] = "notAssessed"
        risk["selfHarm"] = "notAssessed"
        risk["homicidalIdeation"] = "notAssessed"
        risk["recommendedFollowUp"] = "Risk/safety denial wording should be reviewed; the transcript did not explicitly document risk being assessed and denied."
        flags.append(
            {
                "item": "Risk denial wording",
                "reason": "Risk denial wording was not explicitly supported by the transcript.",
            }
        )
    if "not directly assessed" in source_lower and note.get("riskAndSafety", {}).get("status") == "assessed":
        note["riskAndSafety"]["status"] = "notAssessed"
        flags.append(
            {
                "item": "Risk assessment status",
                "reason": "Risk was marked not assessed because the transcript states it was not directly assessed.",
            }
        )


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", text.strip()) if item.strip()]


def _strip_json_fence(text: str) -> str:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    return clean.strip()


def _json_text_or_reject_markdown(text: str) -> str:
    clean = text.strip()
    if "```" in clean:
        raise json.JSONDecodeError("Markdown code fences are not allowed in JSON output.", clean, 0)
    return clean


def _extract_json_text(text: str) -> str:
    clean = _strip_json_fence(text)
    if clean.startswith("{") and clean.endswith("}"):
        return clean
    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        return clean[start : end + 1]
    return _json_text_or_reject_markdown(clean)


def _active_therapist(session: Session, therapist_id: str) -> Therapist:
    therapist = session.get(Therapist, therapist_id)
    if therapist is None or not therapist.active:
        raise KeyError(f"Unknown active therapist: {therapist_id}")
    return therapist


def _assigned_patient(session: Session, therapist_id: str, patient_id: str) -> Patient:
    therapist = _active_therapist(session, therapist_id)
    assigned_patient_ids = _assigned_patient_ids(session, therapist_id)
    if patient_id not in assigned_patient_ids:
        raise PermissionError("Patient is not assigned to the current therapist.")
    patient = session.get(Patient, patient_id)
    if patient is None:
        raise KeyError(f"Unknown patient: {patient_id}")
    if patient.tenant_id != therapist.tenant_id:
        raise PermissionError("Patient is not assigned to the current therapist.")
    return patient


def _assigned_patient_ids(session: Session, therapist_id: str) -> set[str]:
    _active_therapist(session, therapist_id)
    return {patient["id"] for patient in list_patients_for_therapist(session, therapist_id)}


def _documentation_session_for_therapist(
    session: Session,
    therapist_id: str,
    documentation_session_id: str,
) -> DocumentationSession:
    item = session.get(DocumentationSession, documentation_session_id)
    if item is None:
        raise KeyError(f"Unknown documentation session: {documentation_session_id}")
    if item.therapist_id != therapist_id:
        raise PermissionError("Documentation session is not assigned to the current therapist.")
    therapist = _active_therapist(session, therapist_id)
    if item.tenant_id != therapist.tenant_id:
        raise PermissionError("Documentation session is not assigned to the current therapist.")
    _assigned_patient(session, therapist_id, item.patient_id)
    return item


def _validated_referral_id(
    session: Session,
    tenant_id: str,
    patient_id: str,
    referral_id: str | None,
) -> str | None:
    if not referral_id:
        return None
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise KeyError(f"Unknown referral: {referral_id}")
    if referral.tenant_id != tenant_id or referral.patient_id != patient_id:
        raise ValueError("Referral does not belong to the selected patient.")
    return referral.id


def _validated_appointment_id(
    session: Session,
    *,
    therapist_id: str,
    patient_id: str,
    appointment_id: str | None,
) -> str | None:
    if not appointment_id:
        return None
    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        raise KeyError(f"Unknown appointment: {appointment_id}")
    if appointment.therapist_id != therapist_id or appointment.patient_id != patient_id:
        raise ValueError("Appointment does not belong to the selected patient and therapist.")
    if appointment.status == "cancelled":
        raise ValueError("Appointment is cancelled.")
    return appointment.id
