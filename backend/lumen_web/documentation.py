"""Therapist-scoped documentation data services."""

from __future__ import annotations

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


def list_documentation_patients_for_therapist(session: Session, therapist_id: str) -> list[dict[str, Any]]:
    return list_patients_for_therapist(session, therapist_id)


def list_documentation_sessions_for_therapist(
    session: Session,
    *,
    therapist_id: str,
    patient_id: str | None = None,
) -> list[dict[str, Any]]:
    if patient_id:
        _assigned_patient(session, therapist_id, patient_id)
    query = (
        select(DocumentationSession)
        .where(DocumentationSession.therapist_id == therapist_id)
        .order_by(DocumentationSession.updated_at.desc(), DocumentationSession.created_at.desc())
    )
    if patient_id:
        query = query.where(DocumentationSession.patient_id == patient_id)
    return [documentation_session_to_dict(item) for item in session.scalars(query)]


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
    if source_text_id:
        source_text = session.get(DocumentationSessionText, source_text_id)
        if source_text is None:
            raise KeyError(f"Unknown documentation session text: {source_text_id}")
        if source_text.documentation_session_id != item.id:
            raise ValueError("Session text does not belong to the documentation session.")
    note = DocumentationSessionNote(
        tenant_id=item.tenant_id,
        documentation_session_id=item.id,
        source_text_id=source_text_id or None,
        note_json=json_safe(note_json),
        reviewed_json=json_safe(note_json),
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


def _active_therapist(session: Session, therapist_id: str) -> Therapist:
    therapist = session.get(Therapist, therapist_id)
    if therapist is None or not therapist.active:
        raise KeyError(f"Unknown active therapist: {therapist_id}")
    return therapist


def _assigned_patient(session: Session, therapist_id: str, patient_id: str) -> Patient:
    assigned_patient_ids = {patient["id"] for patient in list_patients_for_therapist(session, therapist_id)}
    if patient_id not in assigned_patient_ids:
        raise PermissionError("Patient is not assigned to the current therapist.")
    patient = session.get(Patient, patient_id)
    if patient is None:
        raise KeyError(f"Unknown patient: {patient_id}")
    return patient


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
    return appointment.id
