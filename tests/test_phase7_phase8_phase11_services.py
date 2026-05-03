from __future__ import annotations

import pytest

from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import Referral, Tenant
from backend.lumen_web.repositories import (
    approve_session_note,
    create_clinical_library_record,
    create_session_note,
    draft_feedback_metrics,
    export_report_draft,
    generate_report_draft,
    governance_posture,
    import_referral_batch,
    integration_health,
    record_draft_feedback,
    sign_off_report_draft,
    update_report_draft,
)


def test_phase7_report_validation_export_phase8_import_and_phase11_feedback() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-phase789", name="Phase 789 Test", slug="test-phase789")
        session.add(tenant)
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Adult anxiety referral with sleep impairment and work stress.",
            status="contacted",
            patient_name="Phase Seven Patient",
        )
        session.add(referral)
        session.flush()

        note = create_session_note(
            session,
            referral_id=referral.id,
            therapist_id=None,
            title="Approved session",
            body="Patient described anxiety, sleep impairment, and work stress. Risk denied.",
        )
        approve_session_note(session, note["id"])
        create_clinical_library_record(
            session,
            tenant_id=tenant.id,
            record_type="protocol",
            title="Anxiety protocol",
            body="Anxiety protocol requires sleep, risk, functional impairment, and support review.",
        )
        create_clinical_library_record(
            session,
            tenant_id=tenant.id,
            record_type="template",
            title="Summary template",
            body="Session summaries include presenting concern, intervention focus, risk update, and next step.",
        )

        report = generate_report_draft(
            session,
            referral_id=referral.id,
            report_type="session_summary",
            title="Evidence summary",
            request_text="Summarize anxiety, sleep, and risk.",
        )
        assert report["status"] == "pending_signoff"
        assert report["claim_evidence_map"]
        assert not report["unsupported_claims"]

        unsupported_body = report["body"].replace(
            "## Evidence-Grounded Summary\n",
            "## Evidence-Grounded Summary\n- Added clinical claim without source citation.\n",
            1,
        )
        edited = update_report_draft(session, report["id"], body=unsupported_body)
        assert edited["unsupported_claims"] == ["Added clinical claim without source citation."]
        with pytest.raises(ValueError):
            sign_off_report_draft(session, report["id"])

        fixed = update_report_draft(session, report["id"], body=report["body"])
        assert not fixed["unsupported_claims"]
        signed = sign_off_report_draft(session, report["id"])
        assert signed["status"] == "signed_off"

        exported = export_report_draft(session, report["id"])
        assert exported["file_name"].endswith(".md")
        assert "# Evidence summary" in exported["content"]

        feedback = record_draft_feedback(
            session,
            report["id"],
            feedback_type="approved_report",
            usable_for_practice_memory=True,
        )
        assert feedback["usable_for_practice_memory"] is True
        metrics = draft_feedback_metrics(session, tenant_id=tenant.id)
        assert metrics["practice_memory_eligible"] >= 1

        batch = import_referral_batch(
            session,
            tenant_id=tenant.id,
            file_name="batch.csv",
            content_text=(
                'patient_name,email,raw_text,insurer\n'
                '"Batch Patient","batch@example.com","Adult referral for online anxiety support with Multicare.","Multicare"\n'
                '"Broken Row","","",""\n'
            ),
        )
        assert batch["batch"]["status"] == "partial"
        assert batch["batch"]["imported_count"] == 1
        assert batch["batch"]["error_count"] == 1
        assert batch["errors"][0]["row_number"] == 3

        health = integration_health(session, tenant_id=tenant.id)
        assert health["checks"][0]["status"] == "ok"
        posture = governance_posture(session, tenant_id=tenant.id)
        assert posture["audit_events"] > 0
    finally:
        session.rollback()
        session.close()
