from __future__ import annotations

import asyncio
import json
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
    documentation_session_delete,
    documentation_session_get,
    documentation_session_reviewed_note_create,
    documentation_sessions,
    documentation_session_text_create,
    documentation_session_text_update,
)
from backend.lumen_web.documentation import (
    extract_documentation_session_upload_for_therapist,
    generate_progress_overview_for_therapist,
    validate_session_note_json,
    _hf_token,
    _hf_text_model,
    _normalize_generated_session_note,
)
from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import Appointment, DocumentationProgressOverview, DocumentationSession, DocumentationSessionNote, DocumentationSessionText, Patient
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
        session.execute(delete(DocumentationProgressOverview))
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


def test_therapist_documentation_text_generation_defaults_to_llama_33_70b(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_LLM_MODEL", raising=False)

    assert _hf_text_model() == "meta-llama/Llama-3.3-70B-Instruct"


def test_therapist_documentation_text_generation_allows_existing_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_LLM_MODEL", "meta-llama/Llama-3.1-70B-Instruct")

    assert _hf_text_model() == "meta-llama/Llama-3.1-70B-Instruct"


def test_hugging_face_token_accepts_existing_repo_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf_test")

    assert _hf_token() == "hf_test"


def test_generated_session_note_normalizer_accepts_partial_camel_or_snake_output() -> None:
    note = _normalize_generated_session_note(
        {
            "summary": "Client discussed work stress and difficulty sleeping.",
            "source_basis": {"extraction_confidence": "medium"},
            "risk_and_safety": {"status": "not_assessed"},
        },
        "Client discussed work stress and difficulty sleeping.",
    )

    validated = validate_session_note_json(note, "Client discussed work stress and difficulty sleeping.")

    assert validated["version"] == "sessionNoteV0.2"
    assert validated["sessionSummary"]["briefSummary"] == "Client discussed work stress and difficulty sleeping."
    assert validated["sourceBasis"]["inputUsed"] == "sessionTranscript"
    assert validated["riskAndSafety"]["status"] == "notAssessed"


def test_generated_session_note_validation_repairs_model_enum_hints() -> None:
    note = _normalize_generated_session_note(
        {
            "version": "sessionNoteV0.2",
            "noteType": "CBTAssessment | SOAP | DAP | Intake | ProgressNote | Unknown",
            "sourceBasis": {
                "rawSourceStored": False,
                "inputUsed": "sessionTranscript",
                "extractionConfidence": "low | medium | high",
            },
            "client": {"nameUsedInSession": None, "roleOrContext": None, "demographicsMentioned": []},
            "sessionSummary": {"briefSummary": "Client discussed work stress.", "mainClinicalThemes": []},
            "presentingConcern": {
                "primaryConcern": None,
                "onsetContext": None,
                "durationMentioned": None,
                "clientDescription": [],
            },
            "relevantHistory": {
                "mentalHealthHistory": {"previousEpisodesMentioned": None, "details": None},
                "educationOrWork": {"currentStatus": None, "context": None, "impact": None},
                "relationships": {"relevantEvents": [], "impact": None},
                "familyContext": {"livingSituation": None, "familyInvolvement": None},
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
                "status": "notAssessed | partiallyAssessed | assessed",
                "suicidalIdeation": "notAssessed | denied | passive | active | unclear",
                "selfHarm": "notAssessed | denied | present | unclear",
                "homicidalIdeation": "notAssessed | denied | present | unclear",
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
                "summary": "Client discussed work stress.",
                "diagnosisStatus": "notDiagnosedFromTranscript",
                "areasForFurtherAssessment": [],
            },
            "planAndFollowUp": {"therapyDirection": [], "possibleHomework": [], "nextSessionFocus": []},
            "uncertaintyFlags": [],
        },
        "Client discussed work stress.",
    )

    validated = validate_session_note_json(note, "Client discussed work stress.")

    assert validated["noteType"] == "Unknown"
    assert validated["sourceBasis"]["extractionConfidence"] == "medium"
    assert validated["riskAndSafety"]["status"] == "notAssessed"


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


def test_progress_overview_uses_reviewed_notes_not_transcripts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTE_GENERATOR", "fake")
    _prepare_documentation_demo()
    doc_session = _create_clara_documentation_session()
    request = _request_for_user(DEMO_CLARA_THERAPIST_USER_ID)
    text = _call(
        documentation_session_text_create(
            doc_session["id"],
            DocumentationTextRequest(text="Transcript-only wording about crisis escalation should not drive progress."),
            request,
        )
    )["text"]
    _call(
        documentation_session_reviewed_note_create(
            doc_session["id"],
            DocumentationReviewedNoteRequest(
                source_text_id=text["id"],
                note_json={"summary": "Reviewed note describes steady sleep routine practice."},
            ),
            request,
        )
    )

    session = SessionLocal()
    try:
        overview = generate_progress_overview_for_therapist(
            session,
            therapist_id=DEMO_CLEAN_THERAPIST_ID,
            patient_key=DOC_PATIENT_ID,
        )["progress_overview"]
        session.commit()
    finally:
        session.close()

    details = " ".join(overview["overviewSummary"]["supportingDetails"])
    assert overview["reviewed_note_count"] == 1
    assert "steady sleep routine practice" in details
    assert "crisis escalation" not in details


def test_progress_overview_fallback_is_case_specific_for_adhd_material(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTE_GENERATOR", "fake")
    _prepare_documentation_demo()
    request = _request_for_user(DEMO_CLARA_THERAPIST_USER_ID)
    source_material = [
        (
            "Client discussed suspected adult ADHD, shame spirals, executive dysfunction, and difficulty initiating tasks.",
            {
                "summary": "Reviewed note describes suspected adult ADHD, executive dysfunction, task initiation problems, and shame spirals.",
                "key_points_discussed": [
                    "Client described suspected adult ADHD and executive dysfunction.",
                    "Client linked task initiation problems with shame spirals.",
                ],
                "presenting_topics": ["Adult ADHD evaluation preparation", "Difficulty initiating tasks"],
                "plan": ["Prepare examples for formal ADHD evaluation."],
                "uncertainty_flags": ["Diagnosis remains unconfirmed pending formal evaluation."],
                "risk_or_safety": {"status": "mentioned", "details": "Risk/safety will continue to be monitored."},
            },
        ),
        (
            "Client described relationship strain and family-of-origin criticism; therapist supported practical systems.",
            {
                "summary": "Reviewed note describes relationship strain, family-of-origin criticism, practical systems for executive dysfunction, and safety monitoring.",
                "key_points_discussed": [
                    "Relationship strain intensified shame after missed tasks.",
                    "Family-of-origin criticism appears connected to self-blame.",
                ],
                "interventions": [
                    "Therapist supported practical task-initiation systems and preparation for ADHD evaluation.",
                ],
                "plan": ["Review practical systems and safety monitoring next session."],
                "risk_or_safety": {"status": "mentioned", "details": "Safety monitoring remained part of the plan."},
            },
        ),
    ]
    for transcript, note_json in source_material:
        doc_session = _create_clara_documentation_session()
        text = _call(documentation_session_text_create(doc_session["id"], DocumentationTextRequest(text=transcript), request))["text"]
        _call(
            documentation_session_reviewed_note_create(
                doc_session["id"],
                DocumentationReviewedNoteRequest(source_text_id=text["id"], note_json=note_json),
                request,
            )
        )

    session = SessionLocal()
    try:
        overview = generate_progress_overview_for_therapist(
            session,
            therapist_id=DEMO_CLEAN_THERAPIST_ID,
            patient_key=DOC_PATIENT_ID,
        )["progress_overview"]
        session.commit()
    finally:
        session.close()

    overview_text = json.dumps(overview).lower()
    for expected in ["adult adhd", "executive dysfunction", "shame", "relationship strain", "formal adhd evaluation", "safety monitoring"]:
        assert expected in overview_text
    for unsupported in ["inconsistent attendance", "mindfulness", "relaxation technique", "improved mood", "insufficient data"]:
        assert unsupported not in overview_text
    assert "evidence" in overview["caseFrame"]
    assert overview["reviewed_note_count"] == 2


def test_progress_overview_model_output_is_quality_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTE_GENERATOR", "huggingface")
    monkeypatch.setenv("HF_TOKEN", "test-token")
    _prepare_documentation_demo()
    request = _request_for_user(DEMO_CLARA_THERAPIST_USER_ID)
    doc_session = _create_clara_documentation_session()
    text = _call(
        documentation_session_text_create(
            doc_session["id"],
            DocumentationTextRequest(text="Client discussed ADHD evaluation preparation and safety monitoring."),
            request,
        )
    )["text"]
    _call(
        documentation_session_reviewed_note_create(
            doc_session["id"],
            DocumentationReviewedNoteRequest(
                source_text_id=text["id"],
                note_json={
                    "summary": "Reviewed note describes suspected adult ADHD, shame spirals, task-initiation experiments, and safety monitoring.",
                    "key_points_discussed": ["Client linked executive dysfunction with shame after missed tasks."],
                    "interventions": ["Therapist helped build a task-initiation experiment."],
                    "plan": ["Prepare concrete examples for formal ADHD evaluation."],
                    "risk_or_safety": {"status": "mentioned", "details": "Safety monitoring remained part of the plan."},
                },
            ),
            request,
        )
    )
    repeated = "The client has been working on developing coping strategies and self-compassion, and has reported feeling more confident and motivated."
    bad_model_overview = {
        "overviewSummary": {"title": "Overview Summary", "summary": repeated, "trajectory": "improving", "supportingDetails": [repeated], "evidence": [{"sessionNumber": 1, "date": None, "detail": repeated}], "recommendedFollowUp": None},
        "caseFrame": {"title": "Case Frame", "summary": repeated, "trajectory": "improving", "supportingDetails": [repeated], "evidence": [{"sessionNumber": 1, "date": None, "detail": repeated}], "recommendedFollowUp": None},
        "longitudinalPatterns": {"title": "Patterns", "summary": "Significant improvements in symptoms and functioning.", "trajectory": "improving", "supportingDetails": [], "evidence": [], "recommendedFollowUp": None},
        "changesSinceIntake": {"title": "Changes", "summary": "Significant improvements in symptoms and functioning.", "trajectory": "improving", "supportingDetails": [], "evidence": [], "recommendedFollowUp": None},
        "interventionResponse": {"title": "Intervention", "summary": repeated, "trajectory": "improving", "supportingDetails": [], "evidence": [{"sessionNumber": 1, "date": None, "detail": "Client discussed ADHD."}], "recommendedFollowUp": "Offer support and guidance as needed."},
        "stuckPointsAndBarriers": {"title": "Barriers", "summary": repeated, "trajectory": "stable", "supportingDetails": [], "evidence": [], "recommendedFollowUp": None},
        "riskAndSafety": {"title": "Risk", "summary": "No safety concerns.", "trajectory": "stable", "supportingDetails": [], "evidence": [{"sessionNumber": 1, "date": None, "detail": "Client discussed ADHD and procrastination."}], "recommendedFollowUp": None},
        "openClinicalQuestions": {"title": "Questions", "summary": repeated, "trajectory": "stable", "supportingDetails": [], "evidence": [], "recommendedFollowUp": None},
        "nextSessionPriorities": {"title": "Next", "summary": "Support and guidance as needed.", "trajectory": "improving", "supportingDetails": [], "evidence": [], "recommendedFollowUp": "Support and guidance as needed."},
        "evidenceGaps": {"title": "Gaps", "summary": "Risk/safety status is missing.", "trajectory": "stable", "supportingDetails": ["Reviewed notes do not clearly structure risk/safety status."], "evidence": [], "recommendedFollowUp": None},
    }
    monkeypatch.setattr(
        "backend.lumen_web.documentation._hf_chat_completion",
        lambda *args, **kwargs: json.dumps(bad_model_overview),
    )

    session = SessionLocal()
    try:
        overview = generate_progress_overview_for_therapist(
            session,
            therapist_id=DEMO_CLEAN_THERAPIST_ID,
            patient_key=DOC_PATIENT_ID,
        )["progress_overview"]
        session.commit()
    finally:
        session.close()

    overview_text = json.dumps(overview).lower()
    assert "developing coping strategies and self-compassion" not in overview_text
    assert "significant improvements in symptoms and functioning" not in overview_text
    assert "support and guidance as needed" not in overview_text
    assert "adult adhd" in overview_text
    assert "task-initiation experiment" in overview_text
    assert "safety monitoring" in overview_text
    assert overview["nextSessionPriorities"]["trajectory"] is None
    assert overview["evidenceGaps"]["trajectory"] is None
    risk_evidence = json.dumps(overview["riskAndSafety"]["evidence"]).lower()
    assert "safety monitoring" in risk_evidence
    assert "procrastination" not in risk_evidence


def test_clara_can_delete_documentation_session_with_texts_and_notes() -> None:
    _prepare_documentation_demo()
    doc_session = _create_clara_documentation_session()
    request = _request_for_user(DEMO_CLARA_THERAPIST_USER_ID)
    text = _call(
        documentation_session_text_create(
            doc_session["id"],
            DocumentationTextRequest(text="Patient reported sleep difficulty."),
            request,
        )
    )["text"]
    note = _call(
        documentation_session_reviewed_note_create(
            doc_session["id"],
            DocumentationReviewedNoteRequest(source_text_id=text["id"], note_json={"summary": "Reviewed note."}),
            request,
        )
    )["note"]

    deleted = _call(documentation_session_delete(doc_session["id"], request))["deleted"]

    assert deleted["id"] == doc_session["id"]
    assert deleted["text_count"] == 1
    assert deleted["note_count"] == 1
    session = SessionLocal()
    try:
        assert session.get(DocumentationSession, doc_session["id"]) is None
        assert session.get(DocumentationSessionText, text["id"]) is None
        assert session.get(DocumentationSessionNote, note["id"]) is None
    finally:
        session.close()


def test_admin_cannot_delete_therapist_documentation_session() -> None:
    _prepare_documentation_demo()
    doc_session = _create_clara_documentation_session()

    with pytest.raises(HTTPException) as exc_info:
        _call(documentation_session_delete(doc_session["id"], _request_for_user(DEMO_USER_ID)))

    assert exc_info.value.status_code == 403


def test_upload_extraction_routes_audio_into_existing_text_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_documentation_demo()
    doc_session = _create_clara_documentation_session()
    monkeypatch.setattr(
        "backend.lumen_web.documentation._transcribe_audio_with_hugging_face",
        lambda file_bytes, *, filename, content_type=None: "Audio transcript from upload.",
    )
    session = SessionLocal()
    try:
        text = extract_documentation_session_upload_for_therapist(
            session,
            therapist_id=DEMO_CLEAN_THERAPIST_ID,
            documentation_session_id=doc_session["id"],
            file_bytes=b"audio-bytes",
            filename="session.wav",
            content_type="audio/wav",
        )
        session.commit()
    finally:
        session.close()

    assert text["text"] == "Audio transcript from upload."
    assert text["input_type"] == "audio_transcription"
    assert text["source_metadata"]["task"] == "automatic-speech-recognition"
    assert text["source_metadata"]["model"] == "openai/whisper-large-v3-turbo"


def test_upload_extraction_routes_image_and_pdf_into_existing_text_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_documentation_demo()
    doc_session = _create_clara_documentation_session()

    def fake_visual_extract(file_bytes, *, filename, content_type, file_kind):
        return f"{file_kind} text from upload."

    monkeypatch.setattr("backend.lumen_web.documentation._extract_visual_text_with_hugging_face", fake_visual_extract)
    session = SessionLocal()
    try:
        image_text = extract_documentation_session_upload_for_therapist(
            session,
            therapist_id=DEMO_CLEAN_THERAPIST_ID,
            documentation_session_id=doc_session["id"],
            file_bytes=b"image-bytes",
            filename="session-note.png",
            content_type="image/png",
        )
        pdf_text = extract_documentation_session_upload_for_therapist(
            session,
            therapist_id=DEMO_CLEAN_THERAPIST_ID,
            documentation_session_id=doc_session["id"],
            file_bytes=b"pdf-bytes",
            filename="session-note.pdf",
            content_type="application/pdf",
        )
        session.commit()
    finally:
        session.close()

    assert image_text["text"] == "image text from upload."
    assert image_text["input_type"] == "image_extraction"
    assert image_text["source_metadata"]["task"] == "image-to-text"
    assert image_text["source_metadata"]["model"] == "google/gemma-4-31B-it"
    assert pdf_text["text"] == "pdf text from upload."
    assert pdf_text["input_type"] == "document_extraction"
    assert pdf_text["source_metadata"]["source"] == "pdf_upload"


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
    assert note["note_json"]["version"] == "sessionNoteV0.2"
    assert note["note_json"]["sourceBasis"]["rawSourceStored"] is False
    assert note["note_json"]["sourceBasis"]["inputUsed"] == "sessionTranscript"
    assert note["note_json"]["riskAndSafety"]["status"] == "notAssessed"

    reviewed_json = dict(note["note_json"])
    reviewed_json["sessionSummary"]["briefSummary"] = "Reviewed sleep and grounding note."
    reviewed_json["clinicalImpression"]["summary"] = "Reviewed sleep and grounding note."
    reviewed = _call(
        documentation_note_reviewed_update(
            note["id"],
            DocumentationReviewedNoteUpdateRequest(reviewed_json=reviewed_json),
            request,
        )
    )["note"]

    assert reviewed["status"] == "reviewed"
    assert reviewed["reviewed_json"]["sessionSummary"]["briefSummary"] == "Reviewed sleep and grounding note."
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

    assert note["note_json"]["riskAndSafety"]["status"] == "partiallyAssessed"
    assert "not wanting to be alive" in " ".join(note["note_json"]["riskAndSafety"]["riskIndicatorsMentioned"])


def test_generated_note_uses_readable_clinical_values_and_required_minimums(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert note["version"] == "sessionNoteV0.2"
    assert note["sessionSummary"]["briefSummary"]
    assert note["riskAndSafety"]["status"] == "notAssessed"
    assert note["uncertaintyFlags"]
    assert "_" not in json.dumps(note)


def test_validation_allows_source_supported_anxiety_only_in_free_text() -> None:
    source_text = "Patient reported feeling anxious and guilty after forgetting a client email."
    note_json = {
        "version": "sessionNoteV0.2",
        "noteType": "CBTAssessment",
        "sourceBasis": {
            "rawSourceStored": False,
            "inputUsed": "sessionTranscript",
            "extractionConfidence": "high",
        },
        "client": {"nameUsedInSession": None, "roleOrContext": None, "demographicsMentioned": []},
        "sessionSummary": {
            "briefSummary": "Patient discussed anxiety-like distress and guilt after forgetting a client email.",
            "mainClinicalThemes": ["Client described anxiety-like distress and guilt after forgetting a client email."],
        },
        "presentingConcern": {
            "primaryConcern": "Client described feeling anxious and guilty after forgetting a client email.",
            "onsetContext": "Forgetting a client email.",
            "durationMentioned": None,
            "clientDescription": ["Patient reported feeling anxious and guilty."],
        },
        "relevantHistory": {
            "mentalHealthHistory": {"previousEpisodesMentioned": None, "details": None},
            "educationOrWork": {"currentStatus": None, "context": "Client email stress was discussed.", "impact": None},
            "relationships": {"relevantEvents": [], "impact": None},
            "familyContext": {"livingSituation": None, "familyInvolvement": None},
        },
        "currentStressors": [{"stressor": "Forgotten client email.", "clientMeaning": "Client felt anxious and guilty.", "evidence": ["feeling anxious and guilty"]}],
        "symptomsAndFunctioning": {
            "emotionalSymptoms": ["Client described anxiety-like distress and guilt."],
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
        "interventionsUsedInSession": [{"intervention": "Self-compassion exercise.", "description": "Therapist used a self-compassion exercise.", "evidence": ["self-compassion"]}],
        "clientResponseToSession": {"engagement": None, "motivation": None, "insight": None, "notableResponses": ["Patient said the self-compassion statement felt lighter."]},
        "clinicalImpression": {
            "summary": "Client described anxiety-like distress and guilt after forgetting a client email.",
            "diagnosisStatus": "notDiagnosedFromTranscript",
            "areasForFurtherAssessment": [],
        },
        "planAndFollowUp": {
            "therapyDirection": [],
            "possibleHomework": ["Practice self-compassion."],
            "nextSessionFocus": ["Review use of self-compassion practice next session."],
        },
        "uncertaintyFlags": [{"item": "Risk and safety", "reason": "Risk and safety assessment was not documented."}],
    }

    validated = validate_session_note_json(note_json, source_text)

    assert "anxiety-like distress" in validated["sessionSummary"]["briefSummary"]
    assert validated["riskAndSafety"]["status"] == "notAssessed"
    assert validated["planAndFollowUp"]["possibleHomework"]
