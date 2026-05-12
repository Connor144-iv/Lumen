from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import delete

from app import app
from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import (
    Appointment,
    DocumentationSession,
    DocumentationSessionNote,
    DocumentationSessionText,
    Patient,
    Therapist,
    User,
)
from backend.lumen_web.repositories import (
    DEMO_CLEAN_THERAPIST_ID,
    DEMO_OUTBOUND_PATIENT_EMAIL,
    reset_clean_demo_referral,
    seed_clara_demo_documentation_transcripts,
)
from backend.lumen_web.seed import DEMO_CLARA_THERAPIST_USER_ID, DEMO_TENANT_ID, DEMO_USER_ID

DOC_PATIENT_ID = "documentation-http-patient-001"
DOC_APPOINTMENT_ID = "documentation-http-appointment-001"


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
        patient.display_name = "Documentation HTTP Patient"
        patient.contact_email = DEMO_OUTBOUND_PATIENT_EMAIL
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


def _http_request(
    method: str,
    path: str,
    *,
    user_id: str | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    return asyncio.run(_asgi_request(method, path, user_id=user_id, json_body=json_body))


async def _asgi_request(
    method: str,
    path: str,
    *,
    user_id: str | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    raw_path, _, raw_query = path.partition("?")
    body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
    headers = [(b"content-length", str(len(body)).encode("ascii"))]
    if json_body is not None:
        headers.append((b"content-type", b"application/json"))
    if user_id:
        headers.append((b"x-lumen-user-id", user_id.encode("utf-8")))

    messages: list[dict[str, Any]] = []
    received = False

    async def receive() -> dict[str, Any]:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": raw_path,
        "root_path": "",
        "raw_path": raw_path.encode("utf-8"),
        "query_string": raw_query.encode("utf-8"),
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "state": {},
        "extensions": {},
    }
    await app(scope, receive, send)

    start = next(message for message in messages if message["type"] == "http.response.start")
    body_bytes = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1") for key, value in start.get("headers", [])
    }
    return start["status"], response_headers, json.loads(body_bytes.decode("utf-8") or "{}")


def test_clara_can_use_documentation_api_over_http() -> None:
    _prepare_documentation_demo()

    status, _, body = _http_request("GET", "/api/documentation/patients", user_id=DEMO_CLARA_THERAPIST_USER_ID)
    assert status == 200
    assert [patient["id"] for patient in body["patients"]] == [DOC_PATIENT_ID]

    status, _, body = _http_request(
        "POST",
        "/api/documentation/sessions",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
        json_body={"patient_id": DOC_PATIENT_ID, "title": "HTTP documentation session"},
    )
    assert status == 201
    doc_session = body["session"]
    assert doc_session["therapist_id"] == DEMO_CLEAN_THERAPIST_ID
    assert doc_session["patient_id"] == DOC_PATIENT_ID

    status, _, body = _http_request(
        "GET",
        f"/api/documentation/sessions?patient_id={DOC_PATIENT_ID}",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
    )
    assert status == 200
    assert [item["id"] for item in body["sessions"]] == [doc_session["id"]]

    status, _, body = _http_request(
        "POST",
        f"/api/documentation/sessions/{doc_session['id']}/texts",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
        json_body={"text": "Patient reported sleep difficulty.", "source_metadata": {"source": "http-test"}},
    )
    assert status == 201
    text = body["text"]

    status, _, body = _http_request(
        "PUT",
        f"/api/documentation/sessions/{doc_session['id']}/texts/{text['id']}",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
        json_body={"text": "Patient reported improved sleep."},
    )
    assert status == 200
    assert body["text"]["text"] == "Patient reported improved sleep."

    status, _, body = _http_request(
        "POST",
        f"/api/documentation/sessions/{doc_session['id']}/notes/reviewed",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
        json_body={"source_text_id": text["id"], "note_json": {"summary": "Reviewed over HTTP."}},
    )
    assert status == 201
    assert body["note"]["status"] == "reviewed"
    assert body["note"]["reviewer_id"] == DEMO_CLARA_THERAPIST_USER_ID

    status, _, body = _http_request(
        "POST",
        f"/api/documentation/sessions/{doc_session['id']}/notes/generate",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
        json_body={"source_text_id": text["id"]},
    )
    assert status == 201
    generated_note = body["note"]
    assert generated_note["status"] == "draft"
    assert generated_note["note_json"]["version"] == "session_note_v0.1"

    reviewed_json = dict(generated_note["note_json"])
    reviewed_json["summary"] = "Reviewed generated note over HTTP."
    status, _, body = _http_request(
        "PUT",
        f"/api/documentation/notes/{generated_note['id']}/reviewed",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
        json_body={"reviewed_json": reviewed_json},
    )
    assert status == 200
    assert body["note"]["status"] == "reviewed"
    assert body["note"]["reviewed_json"]["summary"] == "Reviewed generated note over HTTP."

    status, _, body = _http_request(
        "GET",
        f"/api/documentation/sessions/{doc_session['id']}",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
    )
    assert status == 200
    assert body["session"]["id"] == doc_session["id"]
    assert [item["id"] for item in body["texts"]] == [text["id"]]
    assert {item["id"] for item in body["notes"]} >= {generated_note["id"]}


def test_admin_can_seed_clara_transcript_only_documentation_sessions() -> None:
    _prepare_documentation_demo()
    session = SessionLocal()
    try:
        seeded = seed_clara_demo_documentation_transcripts(session)
        session.commit()
    finally:
        session.close()
    assert seeded["patient_id"] == DOC_PATIENT_ID
    assert seeded["therapist_id"] == DEMO_CLEAN_THERAPIST_ID
    assert seeded["session_count"] == 12
    assert seeded["text_count"] == 12

    status, _, body = _http_request(
        "GET",
        f"/api/documentation/sessions?patient_id={DOC_PATIENT_ID}",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
    )
    assert status == 200
    sessions = body["sessions"]
    assert len(sessions) == 12
    assert {session["note_status"] for session in sessions} == {"no_draft"}
    assert all(session["has_transcript"] for session in sessions)

    status, _, detail = _http_request(
        "GET",
        f"/api/documentation/sessions/{sessions[0]['id']}",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
    )
    assert status == 200
    assert len(detail["texts"]) == 1
    assert detail["notes"] == []

    status, _, overview = _http_request(
        "GET",
        "/api/documentation/therapists/all/patients/overview",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
    )
    assert status == 200
    assert overview["patients"][0]["patient_key"] == DOC_PATIENT_ID
    assert overview["patients"][0]["session_count"] == 12

    status, _, dashboard = _http_request(
        "GET",
        f"/api/documentation/patients/{DOC_PATIENT_ID}/dashboard",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
    )
    assert status == 200
    assert dashboard["patient"]["patient_key"] == DOC_PATIENT_ID
    assert len(dashboard["sessions"]) == 12
    assert dashboard["sessions"][0]["transcript_text"]
    assert dashboard["sessions"][0]["latest_note"] is None

    status, _, progress = _http_request(
        "POST",
        f"/api/documentation/patients/{DOC_PATIENT_ID}/progress-overview/generate",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
    )
    assert status == 200
    assert progress["progress_overview"]["source_session_count"] == 12


def test_admin_is_denied_documentation_api_over_http() -> None:
    _prepare_documentation_demo()
    status, _, body = _http_request(
        "POST",
        "/api/documentation/sessions",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
        json_body={"patient_id": DOC_PATIENT_ID},
    )
    assert status == 201
    doc_session_id = body["session"]["id"]

    admin_requests = [
        ("GET", "/api/documentation/patients", None),
        ("GET", "/api/documentation/sessions", None),
        ("POST", "/api/documentation/sessions", {"patient_id": DOC_PATIENT_ID}),
        ("GET", f"/api/documentation/sessions/{doc_session_id}", None),
        ("POST", f"/api/documentation/sessions/{doc_session_id}/texts", {"text": "Blocked."}),
        ("POST", f"/api/documentation/sessions/{doc_session_id}/notes/generate", {}),
        (
            "POST",
            f"/api/documentation/sessions/{doc_session_id}/notes/reviewed",
            {"note_json": {"summary": "Blocked."}},
        ),
    ]
    for method, path, payload in admin_requests:
        status, _, body = _http_request(method, path, user_id=DEMO_USER_ID, json_body=payload)
        assert status == 403
        assert "active therapist" in body["detail"]


def test_clara_is_denied_admin_api_over_http() -> None:
    _prepare_documentation_demo()

    admin_requests = [
        ("GET", "/api/referrals", None),
        ("GET", "/api/review-tasks", None),
        ("GET", "/api/therapists", None),
        ("POST", "/api/demo/clean-referral/reset", None),
        ("GET", "/api/integrations/health", None),
        ("GET", "/api/security/posture", None),
    ]
    for method, path, payload in admin_requests:
        status, _, body = _http_request(method, path, user_id=DEMO_CLARA_THERAPIST_USER_ID, json_body=payload)
        assert status == 403
        assert "admin" in body["detail"]


def test_stale_unassigned_documentation_session_is_blocked_over_http() -> None:
    _prepare_documentation_demo()
    unassigned_patient_id = f"unassigned-{uuid4()}"
    stale_session_id = f"stale-session-{uuid4()}"
    stale_text_id = f"stale-text-{uuid4()}"
    session = SessionLocal()
    try:
        patient = Patient(
            id=unassigned_patient_id,
            tenant_id=DEMO_TENANT_ID,
            display_name="HTTP Stale Patient",
            contact_email="http.stale@example.com",
        )
        session.add(patient)
        session.flush()
        session.add(
            DocumentationSession(
                id=stale_session_id,
                tenant_id=DEMO_TENANT_ID,
                patient_id=unassigned_patient_id,
                therapist_id=DEMO_CLEAN_THERAPIST_ID,
                title="HTTP stale session",
                status="active",
            )
        )
        session.add(
            DocumentationSessionText(
                id=stale_text_id,
                tenant_id=DEMO_TENANT_ID,
                documentation_session_id=stale_session_id,
                text="Stale text.",
                input_type="manual_text",
                source_metadata={"source": "http-test"},
                raw_source_stored=False,
            )
        )
        session.commit()
    finally:
        session.close()

    status, _, body = _http_request("GET", "/api/documentation/sessions", user_id=DEMO_CLARA_THERAPIST_USER_ID)
    assert status == 200
    assert stale_session_id not in {item["id"] for item in body["sessions"]}

    protected_requests = [
        ("GET", f"/api/documentation/sessions?patient_id={unassigned_patient_id}", None),
        ("GET", f"/api/documentation/sessions/{stale_session_id}", None),
        ("POST", f"/api/documentation/sessions/{stale_session_id}/texts", {"text": "Blocked."}),
        ("PUT", f"/api/documentation/sessions/{stale_session_id}/texts/{stale_text_id}", {"text": "Blocked."}),
        (
            "POST",
            f"/api/documentation/sessions/{stale_session_id}/notes/reviewed",
            {"note_json": {"summary": "Blocked."}},
        ),
    ]
    for method, path, payload in protected_requests:
        status, _, body = _http_request(method, path, user_id=DEMO_CLARA_THERAPIST_USER_ID, json_body=payload)
        assert status == 403
        assert "not assigned" in body["detail"]


def test_wrong_owner_documentation_session_is_rejected_over_http() -> None:
    _prepare_documentation_demo()
    other_user_id = f"other-therapist-user-{uuid4()}"
    other_therapist_id = f"other-therapist-{uuid4()}"
    other_patient_id = f"other-patient-{uuid4()}"
    other_session_id = f"other-session-{uuid4()}"
    other_email = f"other.therapist.{uuid4()}@example.com"
    session = SessionLocal()
    try:
        session.add(
            User(
                id=other_user_id,
                tenant_id=DEMO_TENANT_ID,
                email=other_email,
                display_name="Other Therapist",
                role="therapist",
                active=True,
            )
        )
        session.add(
            Therapist(
                id=other_therapist_id,
                tenant_id=DEMO_TENANT_ID,
                name="Other Therapist",
                email=other_email,
                specialties=[],
                age_groups=["adult"],
                languages=["English"],
                modalities=["CBT"],
                insurers=[],
                capacity_per_week=4,
                active=True,
                availability_blocks=[],
            )
        )
        session.add(
            Patient(
                id=other_patient_id,
                tenant_id=DEMO_TENANT_ID,
                display_name="Other Therapist Patient",
                contact_email="other.patient@example.com",
            )
        )
        session.flush()
        session.add(
            DocumentationSession(
                id=other_session_id,
                tenant_id=DEMO_TENANT_ID,
                patient_id=other_patient_id,
                therapist_id=other_therapist_id,
                title="Other therapist session",
                status="active",
            )
        )
        session.commit()
    finally:
        session.close()

    status, _, body = _http_request(
        "GET",
        f"/api/documentation/sessions/{other_session_id}",
        user_id=DEMO_CLARA_THERAPIST_USER_ID,
    )
    assert status == 403
    assert "not assigned" in body["detail"]


def test_therapist_spa_entry_routes_are_registered_for_index_html() -> None:
    for path in ("/documentation", "/my-patients", "/patients/{patient_key}/dashboard"):
        route = next(item for item in app.routes if getattr(item, "path", None) == path)
        response = route.endpoint(patient_key=DOC_PATIENT_ID) if "{patient_key}" in path else route.endpoint()

        assert Path(response.path).as_posix().endswith("frontend/index.html")
        assert response.media_type == "text/html"
