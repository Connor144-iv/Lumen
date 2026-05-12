from __future__ import annotations

from fastapi.testclient import TestClient

from backend.lumen_web import repositories
from app import app
from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import Tenant, Therapist
from backend.lumen_web.workflow_state import canonical_referral_status
from backend.lumen_web.workflow_jobs import WorkflowJobManager, WorkflowRequest


client = TestClient(app)


def test_rejects_unknown_workflow_type() -> None:
    response = client.post("/api/run-workflow", json={"workflow_type": "bogus", "raw_text": "hello"})

    assert response.status_code == 400
    assert "workflow_type" in response.json()["detail"]


def test_rejects_empty_referral_text() -> None:
    response = client.post("/api/run-workflow", json={"workflow_type": "new_referral", "raw_text": "   "})

    assert response.status_code == 400
    assert "Referral intake requires" in response.json()["detail"]


def test_session_workflow_accepts_report_request_without_note() -> None:
    manager = WorkflowJobManager(max_workers=1)
    manager._validate_request(
        WorkflowRequest(
            workflow_type="session_completed",
            tenant_id="demo-clinic",
            raw_input={"source_channel": "webform", "note_text": "", "report_request": "Draft a summary."},
        )
    )


def test_new_referral_execution_input_includes_bounded_backend_context(monkeypatch) -> None:
    Base.metadata.create_all(bind=engine)
    monkeypatch.setenv("LUMEN_GOOGLE_WORKSPACE_ENABLED", "false")
    tenant_id = "test-execution-context"
    session = SessionLocal()
    try:
        tenant = Tenant(id=tenant_id, name="Execution Context Test", slug="execution-context-test")
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Context Therapist",
            email="clara.demo1234@gmail.com",
            specialties=["anxiety"],
            languages=["Portuguese"],
            modalities=["online"],
            insurers=["Multicare"],
            availability_blocks=[
                {"weekday": day, "start": "09:00", "end": "12:00", "modality": "online"}
                for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
            ],
        )
        session.merge(tenant)
        session.add(therapist)
        session.commit()
    finally:
        session.close()

    manager = WorkflowJobManager(max_workers=1)
    raw_input = manager._raw_input_for_execution(
        WorkflowRequest(
            workflow_type="new_referral",
            tenant_id=tenant_id,
            raw_input={
                "source_channel": "email",
                "raw_text": "Adult referral. Portuguese online therapy. Insurer Multicare. Flexible availability.",
            },
        )
    )

    assert raw_input["therapist_profiles"][0]["name"] == "Context Therapist"
    assert raw_input["appointment_options"]
    assert raw_input["appointment_options"][0]["therapist_name"] == "Context Therapist"
    assert "slot_id" in raw_input["appointment_options"][0]


def test_examples_endpoint_returns_samples() -> None:
    response = client.get("/api/examples")

    assert response.status_code == 200
    assert response.json()["examples"]


def test_therapist_spa_routes_return_frontend_html() -> None:
    for path in ("/documentation", "/my-patients", "/patients/{patient_key}/dashboard"):
        route = next(item for item in app.routes if getattr(item, "path", None) == path)
        if path == "/patients/{patient_key}/dashboard":
            response = route.endpoint("demo-clean-patient-001")
        else:
            response = route.endpoint()

        assert response.path.name == "index.html"
        assert response.path.parent.name == "frontend"
        assert response.media_type == "text/html"


def test_integration_health_exposes_manual_google_calendar_placeholder() -> None:
    response = client.get("/api/integrations/health")

    assert response.status_code == 200
    checks = {check["name"]: check for check in response.json()["checks"]}
    calendar = checks["Google Calendar availability"]
    assert calendar["status"] == "manual"
    assert "not connected yet" in calendar["message"]


def test_integration_health_reports_provider_errors_without_500(monkeypatch) -> None:
    monkeypatch.setattr(
        repositories.google_workspace,
        "google_workspace_status",
        lambda refresh=False: (_ for _ in ()).throw(RuntimeError("provider exploded")),
    )
    monkeypatch.setattr(repositories.google_workspace, "is_enabled", lambda: True)

    response = client.get("/api/integrations/health")

    assert response.status_code == 200
    checks = {check["name"]: check for check in response.json()["checks"]}
    assert checks["Gmail send"]["status"] == "failed"
    assert "provider exploded" in checks["Gmail send"]["message"]


def test_legacy_referral_statuses_map_to_admin_workflow_terms() -> None:
    assert canonical_referral_status("new") == "new_referral"
    assert canonical_referral_status("normalizing") == "normalising"
    assert canonical_referral_status("match_pending_approval") == "match_recommended"
    assert canonical_referral_status("ready_to_contact") == "awaiting_patient_contact"
    assert canonical_referral_status("contacted") == "appointment_confirmed"
