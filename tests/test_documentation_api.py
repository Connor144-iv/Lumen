from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from starlette.requests import Request

from app import (
    DocumentationReviewedNoteRequest,
    DocumentationGenerateNoteRequest,
    DocumentationReviewedNoteUpdateRequest,
    DocumentationSessionCreateRequest,
    DocumentationTextRequest,
    documentation_patients,
    documentation_note_reviewed_update,
    documentation_session_note_generate,
    documentation_session_create,
    documentation_session_get,
    documentation_session_reviewed_note_create,
    documentation_sessions,
    documentation_session_text_create,
    documentation_session_text_update,
)
from backend.lumen_web.documentation import validate_session_note_json
from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import Appointment, DocumentationSession, DocumentationSessionNote, DocumentationSessionText, Patient
from backend.lumen_web.repositories import DEMO_CLEAN_THERAPIST_ID, reset_clean_demo_referral
from backend.lumen_web.seed import DEMO_CLARA_THERAPIST_USER_ID, DEMO_TENANT_ID, DEMO_USER_ID

DOC_PATIENT_ID = "documentation-test-patient-001"
DOC_APPOINTMENT_ID = "documentation-test-appointment-001"


def _request_for_user(user_id: str) -> Request:
    return Request({"type": "http", "headers": [(b"x-lumen-user-id", user_id.encode("utf-8"))]})


def _call(coro):
    return asyncio.run(coro)


def _prepare_documentation_demo() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        session.execute(delete(DocumentationSessionNote))
        session.execute(delete(DocumentationSessionText))
        session.execute(delete(DocumentationSession))
        reset_clean_demo_referral(session)
        patient = session.get(Patient, DOC_PATIENT_ID)
        if patient is None:
            patient = Patient(id=DOC_PATIENT_ID, tenant_id=DEMO_TENANT_ID)
            session.add(patient)
        patient.display_name = "Documentation Test Patient"
        patient.contact_email = "documentation.patient@example.com"
        session.flush()
        appointment = session.get(Appointment, DOC_APPOINTMENT_ID)
        if appointment is None:
            appointment = Appointment(id=DOC_APPOINTMENT_ID, tenant_id=DEMO_TENANT_ID)
            session.add(appointment)
        appointment.patient_id = patient.id
        appointment.therapist_id = DEMO_CLEAN_THERAPIST_ID
        appointment.starts_at = patient.created_at
        appointment.ends_at = patient.created_at
        appointment.status = "confirmed"
        session.commit()
    finally:
        session.close()


def _create_clara_documentation_session() -> dict:
    return _call(
        documentation_session_create(
            DocumentationSessionCreateRequest(
                patient_id=DOC_PATIENT_ID,
                title="Clara demo documentation",
            ),
            _request_for_user(DEMO_CLARA_THERAPIST_USER_ID),
        )
    )["session"]


def test_clara_can_list_documentation_patients_without_hf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    _prepare_documentation_demo()

    response = _call(documentation_patients(_request_for_user(DEMO_CLARA_THERAPIST_USER_ID)))

    patients = response["patients"]
    assert [patient["id"] for patient in patients] == [DOC_PATIENT_ID]
    assert patients[0]["display_name"] == "Documentation Test Patient"


def test_admin_cannot_use_documentation_as_therapist() -> None:
    _prepare_documentation_demo()

    with pytest.raises(HTTPException) as exc_info:
        _call(documentation_patients(_request_for_user(DEMO_USER_ID)))

    assert exc_info.value.status_code == 403


def test_clara_can_create_documentation_session_for_assigned_patient() -> None:
    _prepare_documentation_demo()

    doc_session = _create_clara_documentation_session()
    sessions = _call(
        documentation_sessions(_request_for_user(DEMO_CLARA_THERAPIST_USER_ID), patient_id=DOC_PATIENT_ID)
    )["sessions"]

    assert doc_session["patient_id"] == DOC_PATIENT_ID
    assert doc_session["therapist_id"] == "demo-clean-therapist-001"
    assert doc_session["patient_label_snapshot"] == "Documentation Test Patient"
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
        _call(
            documentation_session_create(
                DocumentationSessionCreateRequest(patient_id=unassigned_patient_id),
                _request_for_user(DEMO_CLARA_THERAPIST_USER_ID),
            )
        )

    assert exc_info.value.status_code == 403


def test_clara_cannot_list_or_access_stale_session_for_unassigned_patient() -> None:
    _prepare_documentation_demo()
    unassigned_patient_id = f"unassigned-{uuid4()}"
    stale_session_id = f"stale-session-{uuid4()}"
    stale_text_id = f"stale-text-{uuid4()}"
    session = SessionLocal()
    try:
        patient = Patient(
            id=unassigned_patient_id,
            tenant_id=DEMO_TENANT_ID,
            display_name="Stale Documentation Patient",
            contact_email="stale.documentation@example.com",
        )
        session.add(patient)
        session.flush()
        stale_session = DocumentationSession(
            id=stale_session_id,
            tenant_id=DEMO_TENANT_ID,
            patient_id=unassigned_patient_id,
            therapist_id=DEMO_CLEAN_THERAPIST_ID,
            title="Stale documentation session",
            status="active",
        )
        stale_text = DocumentationSessionText(
            id=stale_text_id,
            tenant_id=DEMO_TENANT_ID,
            documentation_session_id=stale_session.id,
            text="Stale transcript.",
            input_type="manual_text",
            source_metadata={"source": "test"},
            raw_source_stored=False,
        )
        session.add_all([stale_session, stale_text])
        session.commit()
    finally:
        session.close()

    request = _request_for_user(DEMO_CLARA_THERAPIST_USER_ID)
    sessions = _call(documentation_sessions(request))["sessions"]
    filtered_sessions = _call(documentation_sessions(request, patient_id=DOC_PATIENT_ID))["sessions"]

    assert stale_session_id not in {item["id"] for item in sessions}
    assert stale_session_id not in {item["id"] for item in filtered_sessions}

    protected_calls = [
        lambda: _call(documentation_sessions(request, patient_id=unassigned_patient_id)),
        lambda: _call(documentation_session_get(stale_session_id, request)),
        lambda: _call(
            documentation_session_text_create(
                stale_session_id,
                DocumentationTextRequest(text="New text should not save."),
                request,
            )
        ),
        lambda: _call(
            documentation_session_text_update(
                stale_session_id,
                stale_text_id,
                DocumentationTextRequest(text="Updated text should not save."),
                request,
            )
        ),
        lambda: _call(
            documentation_session_reviewed_note_create(
                stale_session_id,
                DocumentationReviewedNoteRequest(note_json={"summary": "Should not save."}),
                request,
            )
        ),
    ]
    for call in protected_calls:
        with pytest.raises(HTTPException) as exc_info:
            call()
        assert exc_info.value.status_code == 403


def test_clara_can_add_update_text_save_note_and_read_detail() -> None:
    _prepare_documentation_demo()
    doc_session = _create_clara_documentation_session()
    request = _request_for_user(DEMO_CLARA_THERAPIST_USER_ID)

    created_text = _call(
        documentation_session_text_create(
            doc_session["id"],
            DocumentationTextRequest(text="Patient reported sleep difficulty.", source_metadata={"source": "test"}),
            request,
        )
    )["text"]
    updated_text = _call(
        documentation_session_text_update(
            doc_session["id"],
            created_text["id"],
            DocumentationTextRequest(text="Patient reported improved sleep after grounding practice."),
            request,
        )
    )["text"]
    note = _call(
        documentation_session_reviewed_note_create(
            doc_session["id"],
            DocumentationReviewedNoteRequest(
                source_text_id=updated_text["id"],
                note_json={"summary": "Reviewed note for grounding practice."},
            ),
            request,
        )
    )["note"]
    detail = _call(documentation_session_get(doc_session["id"], request))

    assert updated_text["text"] == "Patient reported improved sleep after grounding practice."
    assert note["status"] == "reviewed"
    assert note["generator"] == "manual"
    assert note["reviewer_id"] == DEMO_CLARA_THERAPIST_USER_ID
    assert detail["session"]["id"] == doc_session["id"]
    assert [text["id"] for text in detail["texts"]] == [updated_text["id"]]
    assert [item["id"] for item in detail["notes"]] == [note["id"]]


def test_clara_can_generate_structured_draft_and_save_reviewed_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTE_GENERATOR", "fake")
    _prepare_documentation_demo()
    doc_session = _create_clara_documentation_session()
    request = _request_for_user(DEMO_CLARA_THERAPIST_USER_ID)
    source_text = _call(
        documentation_session_text_create(
            doc_session["id"],
            DocumentationTextRequest(
                text=(
                    "Patient reported sleep difficulty. "
                    "Therapist practiced a grounding exercise. "
                    "Risk and safety were not directly assessed. "
                    "Plan is to practice grounding before bed."
                )
            ),
            request,
        )
    )["text"]

    note = _call(
        documentation_session_note_generate(
            doc_session["id"],
            DocumentationGenerateNoteRequest(source_text_id=source_text["id"]),
            request,
        )
    )["note"]

    assert note["status"] == "draft"
    assert note["generator"] == "fake"
    assert note["note_json"]["version"] == "session_note_v0.1"
    assert note["note_json"]["source_basis"]["raw_source_stored"] is False
    assert note["note_json"]["source_basis"]["input_used"] == "extracted_session_text"
    assert note["note_json"]["risk_or_safety"]["status"] == "not_assessed"

    reviewed_json = dict(note["note_json"])
    reviewed_json["summary"] = "Reviewed sleep and grounding note."
    reviewed = _call(
        documentation_note_reviewed_update(
            note["id"],
            DocumentationReviewedNoteUpdateRequest(reviewed_json=reviewed_json),
            request,
        )
    )["note"]

    assert reviewed["status"] == "reviewed"
    assert reviewed["reviewed_json"]["summary"] == "Reviewed sleep and grounding note."
    assert reviewed["reviewer_id"] == DEMO_CLARA_THERAPIST_USER_ID


def test_fake_generator_preserves_risk_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTE_GENERATOR", "fake")
    _prepare_documentation_demo()
    doc_session = _create_clara_documentation_session()
    request = _request_for_user(DEMO_CLARA_THERAPIST_USER_ID)
    source_text = _call(
        documentation_session_text_create(
            doc_session["id"],
            DocumentationTextRequest(
                text=(
                    "Patient said they have moments of not wanting to be alive. "
                    "Therapist paused to assess immediate safety."
                )
            ),
            request,
        )
    )["text"]

    note = _call(
        documentation_session_note_generate(
            doc_session["id"],
            DocumentationGenerateNoteRequest(source_text_id=source_text["id"]),
            request,
        )
    )["note"]

    assert note["note_json"]["risk_or_safety"]["status"] == "mentioned"
    assert "not wanting to be alive" in note["note_json"]["risk_or_safety"]["details"]


def test_generated_note_uses_controlled_terms_and_required_minimums(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTE_GENERATOR", "fake")
    _prepare_documentation_demo()
    doc_session = _create_clara_documentation_session()
    request = _request_for_user(DEMO_CLARA_THERAPIST_USER_ID)
    source_text = _call(
        documentation_session_text_create(
            doc_session["id"],
            DocumentationTextRequest(
                text=(
                    "Patient reported anxiety and work stress. "
                    "Therapist reviewed a grounding exercise. "
                    "Patient participated in the exercise."
                )
            ),
            request,
        )
    )["text"]

    note = _call(
        documentation_session_note_generate(
            doc_session["id"],
            DocumentationGenerateNoteRequest(source_text_id=source_text["id"]),
            request,
        )
    )["note"]["note_json"]

    restricted_values = (
        note["key_points_discussed"]
        + note["presenting_topics"]
        + note["objective_observations"]
        + note["observed_behavior_patterns"]
        + note["interventions"]
        + note["recommendations"]
    )
    assert "anxiety" not in restricted_values
    assert "emotional_distress" in restricted_values
    assert note["objective_observations"]
    assert note["observed_behavior_patterns"]
    assert note["risk_or_safety"]["status"] == "not_assessed"
    assert note["risk_or_safety"]["details"]
    assert note["plan"]


def test_validation_allows_source_supported_anxiety_only_in_free_text() -> None:
    source_text = "Patient reported feeling anxious and guilty after forgetting a client email."
    note_json = {
        "version": "session_note_v0.1",
        "summary": "Patient discussed anxiety-like distress and guilt after forgetting a client email.",
        "source_basis": {
            "raw_source_stored": False,
            "input_used": "extracted_session_text",
        },
        "key_points_discussed": ["anxiety", "client email stress"],
        "presenting_topics": ["anxious guilt"],
        "subjective": ["Patient reported feeling anxious and guilty."],
        "objective_observations": [],
        "observed_behavior_patterns": [],
        "interventions": ["self-compassion exercise"],
        "patient_response": ["Patient said the self-compassion statement felt lighter."],
        "recommendations": ["practice self-compassion"],
        "follow_up_items": ["Review use of self-compassion practice next session."],
        "risk_or_safety": {
            "status": "not_assessed",
            "details": "",
        },
        "plan": [],
        "uncertainty_flags": [],
    }

    validated = validate_session_note_json(note_json, source_text)

    restricted_values = (
        validated["key_points_discussed"]
        + validated["presenting_topics"]
        + validated["objective_observations"]
        + validated["observed_behavior_patterns"]
        + validated["interventions"]
        + validated["recommendations"]
    )
    assert "anxiety" not in restricted_values
    assert "emotional_distress" in restricted_values
    assert "anxiety-like distress" in validated["summary"]
    assert validated["risk_or_safety"]["details"]
    assert validated["plan"]
