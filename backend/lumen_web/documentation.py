"""Therapist-scoped documentation data services."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Appointment,
    DocumentationSession,
    DocumentationSessionNote,
    DocumentationSessionText,
    Patient,
    Referral,
    Therapist,
)
from .repositories import iso_or_none, json_safe, list_patients_for_therapist, utc_now

NOTE_VERSION = "session_note_v0.1"
DEFAULT_HF_BASE_URL = "https://router.huggingface.co/v1"
DEFAULT_HF_MODEL = "meta-llama/Llama-3.1-70B-Instruct"
DEFAULT_HF_ASR_MODEL = "openai/whisper-large-v3-turbo"
NOTE_KEYS = {
    "version",
    "summary",
    "source_basis",
    "presenting_topics",
    "subjective",
    "objective_observations",
    "interventions",
    "patient_response",
    "risk_or_safety",
    "plan",
    "uncertainty_flags",
    "key_points_discussed",
    "observed_behavior_patterns",
    "recommendations",
    "follow_up_items",
}
LIST_KEYS = [
    "key_points_discussed",
    "presenting_topics",
    "subjective",
    "objective_observations",
    "observed_behavior_patterns",
    "interventions",
    "patient_response",
    "recommendations",
    "follow_up_items",
    "plan",
    "uncertainty_flags",
]
CONTROLLED_LIST_KEYS = {
    "key_points_discussed",
    "presenting_topics",
    "objective_observations",
    "observed_behavior_patterns",
    "interventions",
    "recommendations",
}
CONTROLLED_TERM_MAP = {
    "anxiety": "emotional_distress",
    "anxious": "emotional_distress",
    "stress": "stress_response",
    "stressed": "stress_response",
    "work": "work_related_stressors",
    "workload": "work_related_stressors",
    "sleep": "sleep_disturbance",
    "grounding": "grounding_exercise",
    "breathing": "breathing_exercise",
    "boundary": "boundary_setting",
    "boundaries": "boundary_setting",
    "partner": "relationship_stressors",
    "rumination": "rumination",
    "worry": "rumination",
    "overwhelm": "emotional_distress",
    "overwhelmed": "emotional_distress",
    "activation": "stress_response",
    "coping": "coping_skills_practice",
    "practice": "coping_skills_practice",
    "plan": "treatment_planning",
    "safety": "risk_safety_assessment",
    "risk": "risk_safety_assessment",
}


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
                "generated_note_summary": note_json.get("summary") if isinstance(note_json, dict) else "",
                "notes": detail["notes"],
                "texts": detail["texts"],
            }
        )
    return {
        "patient": {
            **_patient_to_dashboard_dict(patient),
            "patient_key": patient.id,
            "patient_label": patient.display_name or patient.id,
        },
        "sessions": session_items,
        "progress_overview": _current_progress_overview(session_items),
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
        note_json, model = _generate_with_hugging_face(source_text.text)
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
    if os.getenv("NOTE_GENERATOR", "fake").strip().lower() == "huggingface":
        overview = _generate_progress_overview_with_hugging_face(dashboard["sessions"])
    else:
        overview = _fake_progress_overview(dashboard["sessions"])
    return {"patient": dashboard["patient"], "progress_overview": overview}


def normalize_reviewed_note_json(value: dict[str, Any], source_text: str = "") -> dict[str, Any]:
    if set(value) == NOTE_KEYS:
        return validate_session_note_json(value, source_text)
    summary = str(value.get("summary") or "").strip()
    if not summary:
        raise ValueError("Reviewed note summary is required.")
    note = empty_session_note_json()
    note["summary"] = summary
    for key in LIST_KEYS:
        if isinstance(value.get(key), list):
            note[key] = [str(item) for item in value[key] if str(item).strip()]
    risk = value.get("risk_or_safety")
    if isinstance(risk, dict):
        note["risk_or_safety"] = {
            "status": str(risk.get("status") or "not_assessed"),
            "details": str(risk.get("details") or ""),
        }
    return validate_session_note_json(note, source_text)


def empty_session_note_json() -> dict[str, Any]:
    return {
        "version": NOTE_VERSION,
        "summary": "",
        "source_basis": {
            "raw_source_stored": False,
            "input_used": "extracted_session_text",
        },
        "key_points_discussed": [],
        "presenting_topics": [],
        "subjective": [],
        "objective_observations": [],
        "observed_behavior_patterns": [],
        "interventions": [],
        "patient_response": [],
        "recommendations": [],
        "follow_up_items": [],
        "risk_or_safety": {
            "status": "not_assessed",
            "details": "",
        },
        "plan": [],
        "uncertainty_flags": [],
    }


def validate_session_note_json(value: Any, source_text: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Session note must be a JSON object.")
    missing = NOTE_KEYS - set(value)
    extra = set(value) - NOTE_KEYS
    if missing:
        raise ValueError(f"Session note JSON is missing required keys: {', '.join(sorted(missing))}.")
    if extra:
        raise ValueError(f"Session note JSON has unsupported keys: {', '.join(sorted(extra))}.")
    if value.get("version") != NOTE_VERSION:
        raise ValueError(f"Session note version must be {NOTE_VERSION}.")
    if not isinstance(value.get("summary"), str):
        raise ValueError("summary must be a string.")
    source_basis = value.get("source_basis")
    if not isinstance(source_basis, dict):
        raise ValueError("source_basis must be an object.")
    if source_basis.get("raw_source_stored") is not False:
        raise ValueError("source_basis.raw_source_stored must be false.")
    if source_basis.get("input_used") != "extracted_session_text":
        raise ValueError("source_basis.input_used must be extracted_session_text.")
    for key in LIST_KEYS:
        if not isinstance(value.get(key), list) or not all(isinstance(item, str) for item in value[key]):
            raise ValueError(f"{key} must be a list of strings.")
    value = _schema_compliant_session_note(value, source_text or "")
    risk = value.get("risk_or_safety")
    if not isinstance(risk, dict):
        raise ValueError("risk_or_safety must be an object.")
    if not isinstance(risk.get("status"), str) or not isinstance(risk.get("details"), str):
        raise ValueError("risk_or_safety.status and risk_or_safety.details must be strings.")
    _validate_clinical_safety(value, source_text or "")
    return json_safe(value)


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
    source_terms = _source_controlled_terms(source_text)
    for key in CONTROLLED_LIST_KEYS:
        clean[key] = _controlled_terms(clean.get(key, []), source_text)
    clean["key_points_discussed"] = _unique(clean["key_points_discussed"] + source_terms)
    clean["presenting_topics"] = _unique(clean["presenting_topics"] + source_terms)
    if not clean["objective_observations"]:
        clean["objective_observations"] = _minimal_objective_observations(source_text)
    if not clean["observed_behavior_patterns"]:
        clean["observed_behavior_patterns"] = _minimal_behavior_patterns(source_text)
    clean["risk_or_safety"] = _normalized_risk_or_safety(clean.get("risk_or_safety"), source_text)
    if not clean["plan"]:
        clean["plan"] = _minimal_action_plan(source_text)
    if not clean["uncertainty_flags"]:
        clean["uncertainty_flags"] = _minimal_uncertainty_flags(source_text)
    return clean


def _controlled_terms(values: list[str], source_text: str) -> list[str]:
    terms: list[str] = []
    source_lower = source_text.lower()
    for raw in values:
        item = str(raw).strip()
        if not item:
            continue
        mapped = _controlled_term(item, source_lower)
        if mapped:
            terms.append(mapped)
    return _unique(terms)


def _source_controlled_terms(source_text: str) -> list[str]:
    source_lower = source_text.lower()
    terms = []
    for token, mapped in CONTROLLED_TERM_MAP.items():
        if token in source_lower:
            terms.append(mapped)
    return _unique(terms)


def _controlled_term(value: str, source_lower: str) -> str:
    clean = value.strip().lower()
    if "generalized anxiety disorder" in clean or clean in {"gad", "generalized_anxiety_disorder"}:
        return "generalized_anxiety_disorder" if "generalized anxiety disorder" in source_lower or "gad" in source_lower else "emotional_distress"
    if re.fullmatch(r"[a-z][a-z0-9_]*", clean):
        if clean in {"anxiety", "anxious"}:
            return "emotional_distress"
        return clean
    for token, mapped in CONTROLLED_TERM_MAP.items():
        if token in clean:
            return mapped
    return re.sub(r"[^a-z0-9]+", "_", clean).strip("_") or "clinical_topic_documented"


def _minimal_objective_observations(source_text: str) -> list[str]:
    lower = source_text.lower()
    if "engaged" in lower or "participated" in lower:
        return ["engaged_in_session"]
    if "therapist" in lower and "patient" in lower:
        return ["session_participation_documented"]
    return ["limited_objective_data_available"]


def _minimal_behavior_patterns(source_text: str) -> list[str]:
    lower = source_text.lower()
    if "repeated" in lower or "recurring" in lower or "pattern" in lower:
        return ["recurring_pattern_documented"]
    if "stress" in lower or "overwhelm" in lower or "activation" in lower:
        return ["stress_response"]
    return ["no_observed_behavior_pattern_documented"]


def _normalized_risk_or_safety(value: Any, source_text: str) -> dict[str, str]:
    source_lower = source_text.lower()
    risk = value if isinstance(value, dict) else {}
    status = str(risk.get("status") or "").strip() or "not_assessed"
    details = str(risk.get("details") or "").strip()
    if "not directly assessed" in source_lower or "not assessed" in source_lower:
        status = "not_assessed"
        details = details or "Risk and safety were not directly assessed in the provided session text."
    elif any(term in source_lower for term in ["denied risk", "denied safety concerns", "no safety concerns", "no acute risk"]):
        status = "assessed_denied"
        details = details or "No acute risk identified based on the provided session text."
    elif any(term in source_lower for term in ["suicide", "suicidal", "self-harm", "self harm", "not wanting to be alive", "harm myself", "harm themselves"]):
        status = "mentioned"
        details = details or "Risk or safety-related content was mentioned in the provided session text."
    else:
        status = status if status in {"not_assessed", "mentioned", "assessed_denied"} else "not_assessed"
        details = details or "Risk and safety were not documented in the provided session text."
    return {"status": status, "details": details}


def _minimal_action_plan(source_text: str) -> list[str]:
    lower = source_text.lower()
    if "grounding" in lower:
        return [
            "Patient will practice the grounding strategy before the next session.",
            "Therapist will review grounding practice and symptom response at the next session.",
        ]
    if "sleep" in lower:
        return [
            "Patient will track sleep-related stressors before the next session.",
            "Therapist will review sleep pattern changes and coping strategy use at the next session.",
        ]
    return [
        "Patient will track relevant stressors and coping responses before the next session.",
        "Therapist will review progress, barriers, and risk/safety status at the next session.",
    ]


def _minimal_uncertainty_flags(source_text: str) -> list[str]:
    flags = []
    lower = source_text.lower()
    if "risk" not in lower and "safety" not in lower:
        flags.append("Risk and safety assessment was not documented in the provided text.")
    if "plan" not in lower:
        flags.append("Plan details were limited in the provided text.")
    return flags or ["No additional uncertainty documented beyond the provided session text."]


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
    return {
        **empty_session_note_json(),
        "summary": "Draft generated from the stored transcript. Review against the source text before saving.",
        "presenting_topics": topics,
        "key_points_discussed": topics,
        "subjective": subjective,
        "observed_behavior_patterns": [sentence for sentence in sentences if "pattern" in sentence.lower() or "repeated" in sentence.lower()],
        "interventions": interventions,
        "recommendations": [sentence for sentence in sentences if "practice" in sentence.lower()],
        "follow_up_items": [sentence for sentence in sentences if "plan" in sentence.lower()],
        "risk_or_safety": {
            "status": risk_status,
            "details": risk_details,
        },
        "plan": [sentence for sentence in sentences if "plan" in sentence.lower()],
        "uncertainty_flags": flags,
    }


def _generate_with_hugging_face(source_text: str) -> tuple[dict[str, Any], str]:
    token = os.getenv("HF_TOKEN")
    if not token:
        raise ValueError("HF_TOKEN is required when NOTE_GENERATOR=huggingface.")
    base_url = _hf_chat_base_url()
    model = os.getenv("HF_LLM_MODEL", DEFAULT_HF_MODEL).strip() or DEFAULT_HF_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": f"Extracted session text:\n{source_text}"},
        ],
        "temperature": 0,
        "max_tokens": 1400,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_hf_json_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Hugging Face note generation failed: HTTP {exc.code} {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Hugging Face note generation failed: {exc.reason}") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Hugging Face response did not include a chat completion message.") from exc
    try:
        return json.loads(_strip_json_fence(content)), model
    except json.JSONDecodeError as exc:
        raise ValueError("Hugging Face returned invalid JSON. No draft was saved.") from exc


def _transcribe_audio_with_hugging_face(audio_bytes: bytes, *, filename: str) -> str:
    token = os.getenv("HF_TOKEN")
    if not token:
        raise ValueError("HF_TOKEN is required for Hugging Face audio transcription.")
    model = os.getenv("HF_ASR_MODEL", DEFAULT_HF_ASR_MODEL).strip() or DEFAULT_HF_ASR_MODEL
    model_path = urllib.parse.quote(model, safe="")
    request = urllib.request.Request(
        f"https://api-inference.huggingface.co/models/{model_path}",
        data=audio_bytes,
        headers=_hf_audio_headers(token, filename),
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


def _generate_progress_overview_with_hugging_face(session_items: list[dict[str, Any]]) -> dict[str, Any]:
    token = os.getenv("HF_TOKEN")
    if not token:
        raise ValueError("HF_TOKEN is required when NOTE_GENERATOR=huggingface.")
    model = os.getenv("HF_LLM_MODEL", DEFAULT_HF_MODEL).strip() or DEFAULT_HF_MODEL
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return valid JSON only with keys: summary, improvements_or_trends, "
                    "persistent_issues, recommendations_for_therapist, recommendations_for_patient, follow_up_items. "
                    "Each key except summary must be a list of strings. Use only the provided transcripts and notes."
                ),
            },
            {"role": "user", "content": json.dumps(_progress_source_payload(session_items), ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 1400,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        f"{_hf_chat_base_url()}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_hf_json_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return _normalize_progress_overview(json.loads(_strip_json_fence(content)))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Hugging Face progress overview failed: HTTP {exc.code} {detail[:300]}") from exc
    except (urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Hugging Face progress overview failed: {exc}") from exc


def _hf_chat_base_url() -> str:
    base_url = os.getenv("HF_BASE_URL", DEFAULT_HF_BASE_URL).rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return base_url


def _hf_json_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "lumen-clinical-workflow/0.1",
    }


def _hf_audio_headers(token: str, filename: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": _audio_content_type(filename),
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
    reviewed_notes = [
        item.get("latest_note")
        for item in session_items
        if isinstance(item.get("latest_note"), dict) and item["latest_note"].get("status") == "reviewed"
    ]
    return {
        "summary": "Generate a progress overview after transcripts and reviewed notes are ready.",
        "improvements_or_trends": [],
        "persistent_issues": [],
        "recommendations_for_therapist": [],
        "recommendations_for_patient": [],
        "follow_up_items": [],
        "source_session_count": len(session_items),
        "reviewed_note_count": len(reviewed_notes),
        "generated_at": None,
    }


def _fake_progress_overview(session_items: list[dict[str, Any]]) -> dict[str, Any]:
    transcript_text = " ".join(item.get("transcript_text") or "" for item in session_items).lower()
    improvements = []
    if "improved sleep" in transcript_text or "improvement in sleep" in transcript_text:
        improvements.append("Sleep appears to improve when grounding and shutdown routines are used.")
    if "boundary" in transcript_text or "notification" in transcript_text:
        improvements.append("Work-boundary practice appears repeatedly across sessions.")
    persistent = []
    if "deadline" in transcript_text or "late-night work" in transcript_text:
        persistent.append("Deadline weeks and late work contact remain recurring stressors.")
    return {
        "summary": f"Progress overview based on {len(session_items)} documented sessions. Review before clinical use.",
        "improvements_or_trends": improvements,
        "persistent_issues": persistent,
        "recommendations_for_therapist": ["Review which routines are reliable during high-demand weeks."],
        "recommendations_for_patient": ["Continue tracking sleep, boundaries, and stress cues between sessions."],
        "follow_up_items": ["Revisit goals after the next reviewed session note is saved."],
        "source_session_count": len(session_items),
        "reviewed_note_count": len(
            [
                item
                for item in session_items
                if isinstance(item.get("latest_note"), dict) and item["latest_note"].get("status") == "reviewed"
            ]
        ),
        "generated_at": iso_or_none(utc_now()),
    }


def _normalize_progress_overview(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Progress overview must be a JSON object.")
    keys = [
        "improvements_or_trends",
        "persistent_issues",
        "recommendations_for_therapist",
        "recommendations_for_patient",
        "follow_up_items",
    ]
    overview = {"summary": str(value.get("summary") or "").strip()}
    for key in keys:
        raw_items = value.get(key, [])
        overview[key] = [str(item) for item in raw_items] if isinstance(raw_items, list) else []
    overview["generated_at"] = iso_or_none(utc_now())
    return overview


def _progress_source_payload(session_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = []
    for item in session_items:
        note = item.get("latest_note") or {}
        payload.append(
            {
                "date": item.get("session_date"),
                "title": item.get("title"),
                "transcript": item.get("transcript_text"),
                "reviewed_note": note.get("reviewed_json"),
                "draft_note": note.get("note_json"),
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


def _system_prompt() -> str:
    return """
You generate structured psychotherapy session notes. Return valid JSON only.
Use exactly this JSON shape:
{
  "version": "session_note_v0.1",
  "summary": "",
  "source_basis": {
    "raw_source_stored": false,
    "input_used": "extracted_session_text"
  },
  "key_points_discussed": [],
  "presenting_topics": [],
  "subjective": [],
  "objective_observations": [],
  "observed_behavior_patterns": [],
  "interventions": [],
  "patient_response": [],
  "recommendations": [],
  "follow_up_items": [],
  "risk_or_safety": {
    "status": "not_assessed",
    "details": ""
  },
  "plan": [],
  "uncertainty_flags": []
}
Clinical safety rules:
- Use only the provided extracted session text.
- Do not invent facts.
- Do not infer diagnoses, symptoms, mental states, or clinical labels unless explicitly stated in the source text.
- Prefer the client's and therapist's actual wording.
- Do not convert stress, rumination, overwhelm, activation, or physical signs of stress into anxiety unless the source explicitly uses anxiety.
- Do not create diagnoses unless explicitly stated.
- If risk/safety was not discussed, use status "not_assessed".
- If risk/safety wording is present, preserve the source wording in risk_or_safety.details.
- Do not write "no risk", "denied risk", or "no safety concerns" unless the source explicitly says risk was assessed and denied.
- Add uncertainty flags for missing, unclear, or not directly assessed areas.
- Include key_points_discussed, observed_behavior_patterns, recommendations, and follow_up_items when supported by the source.
""".strip()


def _validate_clinical_safety(note: dict[str, Any], source_text: str) -> None:
    source_lower = source_text.lower()
    note_text = json.dumps(note, ensure_ascii=False).lower()
    restricted_text = json.dumps(
        {key: note.get(key, []) for key in CONTROLLED_LIST_KEYS},
        ensure_ascii=False,
    ).lower()
    if "anxiety" in restricted_text:
        raise ValueError("Generated note contains unsupported 'anxiety' wording in a restricted coded field.")
    source_mentions_anxiety = "anxiety" in source_lower or "anxious" in source_lower
    free_text_note = json.dumps(
        {
            "summary": note.get("summary", ""),
            "subjective": note.get("subjective", []),
            "patient_response": note.get("patient_response", []),
            "follow_up_items": note.get("follow_up_items", []),
            "plan": note.get("plan", []),
            "risk_or_safety": note.get("risk_or_safety", {}),
            "uncertainty_flags": note.get("uncertainty_flags", []),
        },
        ensure_ascii=False,
    ).lower()
    if "anxiety" in free_text_note and not source_mentions_anxiety:
        raise ValueError("Generated note contains unsupported 'anxiety' wording.")
    diagnoses = ["diagnosis", "diagnosed", "major depressive", "ptsd", "generalized anxiety disorder"]
    if any(term in note_text for term in diagnoses) and not any(term in source_lower for term in diagnoses):
        raise ValueError("Generated note appears to contain an unsupported diagnosis.")
    denied_terms = ["no risk", "denied risk", "no safety concerns"]
    assessed_denied = any(term in source_lower for term in denied_terms)
    if any(term in note_text for term in denied_terms) and not assessed_denied:
        raise ValueError("Generated note contains unsupported risk-denial wording.")
    if "not directly assessed" in source_lower and note.get("risk_or_safety", {}).get("status") != "not_assessed":
        raise ValueError("risk_or_safety.status must be not_assessed when risk was not directly assessed.")


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", text.strip()) if item.strip()]


def _strip_json_fence(text: str) -> str:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    return clean.strip()


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
