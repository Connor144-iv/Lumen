from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from starlette.requests import Request

from app import (
    DocumentationReviewedNoteRequest,
    DocumentationSessionCreateRequest,
    DocumentationTextRequest,
    documentation_patients,
    documentation_session_create,
    documentation_session_get,
    documentation_session_reviewed_note_create,
    documentation_sessions,
    documentation_session_text_create,
    documentation_session_text_update,
)
from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import Appointment, DocumentationSession, DocumentationSessionNote, DocumentationSessionText, Patient
from backend.lumen_web.repositories import (
    DEMO_CLARA_PATIENT_APPOINTMENT_ID,
    DEMO_CLEAN_PATIENT_ID,
    DEMO_CLEAN_THERAPIST_ID,
    reset_clean_demo_referral,
)
from backend.lumen_web.seed import DEMO_CLARA_THERAPIST_USER_ID, DEMO_TENANT_ID, DEMO_USER_ID


def _request_for_user(user_id: str) -> Request:
    return Request({"type": "http", "headers": [(b"x-lumen-user-id", user_id.encode("utf-8"))]})


def _prepare_documentation_demo() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        session.execute(delete(DocumentationSessionNote))
        session.execute(delete(DocumentationSessionText))
        session.execute(delete(DocumentationSession))
        reset_clean_demo_referral(session)
        session.execute(
            delete(Appointment).where(
                Appointment.therapist_id == DEMO_CLEAN_THERAPIST_ID,
                Appointment.id != DEMO_CLARA_PATIENT_APPOINTMENT_ID,
            )
        )
        session.commit()
    finally:
        session.close()


def _create_clara_documentation_session() -> dict:
    return documentation_session_create(
        DocumentationSessionCreateRequest(
            patient_id=DEMO_CLEAN_PATIENT_ID,
            title="Clara demo documentation",
        ),
        _request_for_user(DEMO_CLARA_THERAPIST_USER_ID),
    )["session"]


def test_clara_can_list_documentation_patients_without_hf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    _prepare_documentation_demo()

    response = documentation_patients(_request_for_user(DEMO_CLARA_THERAPIST_USER_ID))

    patients = response["patients"]
    assert [patient["id"] for patient in patients] == [DEMO_CLEAN_PATIENT_ID]
    assert patients[0]["display_name"] == "Clean Demo Patient"


def test_admin_cannot_use_documentation_as_therapist() -> None:
    _prepare_documentation_demo()

    with pytest.raises(HTTPException) as exc_info:
        documentation_patients(_request_for_user(DEMO_USER_ID))

    assert exc_info.value.status_code == 403


def test_clara_can_create_documentation_session_for_assigned_patient() -> None:
    _prepare_documentation_demo()

    doc_session = _create_clara_documentation_session()
    sessions = documentation_sessions(_request_for_user(DEMO_CLARA_THERAPIST_USER_ID), patient_id=DEMO_CLEAN_PATIENT_ID)[
        "sessions"
    ]

    assert doc_session["patient_id"] == DEMO_CLEAN_PATIENT_ID
    assert doc_session["therapist_id"] == "demo-clean-therapist-001"
    assert doc_session["patient_label_snapshot"] == "Clean Demo Patient"
    assert doc_session["therapist_label_snapshot"] == "Dr. Clara Demo"
    assert [item["id"] for item in sessions] == [doc_session["id"]]


def test_clara_cannot_create_documentation_session_for_unassigned_patient() -> None:
    _prepare_documentation_demo()
    unassigned_patient_id = f"unassigned-{uuid4()}"
    session = SessionLocal()
    try:
        session.add(
            Patient(
                id=unassigned_patient_id,
                tenant_id=DEMO_TENANT_ID,
                display_name="Unassigned Patient",
                contact_email="unassigned@example.com",
            )
        )
        session.commit()
    finally:
        session.close()

    with pytest.raises(HTTPException) as exc_info:
        documentation_session_create(
            DocumentationSessionCreateRequest(patient_id=unassigned_patient_id),
            _request_for_user(DEMO_CLARA_THERAPIST_USER_ID),
        )

    assert exc_info.value.status_code == 403


def test_clara_can_add_update_text_save_note_and_read_detail() -> None:
    _prepare_documentation_demo()
    doc_session = _create_clara_documentation_session()
    request = _request_for_user(DEMO_CLARA_THERAPIST_USER_ID)

    created_text = documentation_session_text_create(
        doc_session["id"],
        DocumentationTextRequest(text="Patient reported sleep difficulty.", source_metadata={"source": "test"}),
        request,
    )["text"]
    updated_text = documentation_session_text_update(
        doc_session["id"],
        created_text["id"],
        DocumentationTextRequest(text="Patient reported improved sleep after grounding practice."),
        request,
    )["text"]
    note = documentation_session_reviewed_note_create(
        doc_session["id"],
        DocumentationReviewedNoteRequest(
            source_text_id=updated_text["id"],
            note_json={"summary": "Reviewed note for grounding practice."},
        ),
        request,
    )["note"]
    detail = documentation_session_get(doc_session["id"], request)

    assert updated_text["text"] == "Patient reported improved sleep after grounding practice."
    assert note["status"] == "reviewed"
    assert note["generator"] == "manual"
    assert note["reviewer_id"] == DEMO_CLARA_THERAPIST_USER_ID
    assert detail["session"]["id"] == doc_session["id"]
    assert [text["id"] for text in detail["texts"]] == [updated_text["id"]]
    assert [item["id"] for item in detail["notes"]] == [note["id"]]
