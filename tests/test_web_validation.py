from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
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


def test_examples_endpoint_returns_samples() -> None:
    response = client.get("/api/examples")

    assert response.status_code == 200
    assert response.json()["examples"]
