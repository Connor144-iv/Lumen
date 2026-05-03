from __future__ import annotations

from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import Referral, Tenant
from backend.lumen_web.repositories import (
    approve_session_note,
    create_clinical_library_record,
    create_session_note,
    generate_report_draft,
    patient_workspace,
    search_retrieval_chunks,
    sign_off_report_draft,
)


def test_phase6_session_note_library_and_retrieval_foundation() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-phase6", name="Phase 6 Test", slug="test-phase6")
        session.add(tenant)
        session.flush()
        referral = Referral(
            tenant_id=tenant.id,
            source_channel="webform",
            raw_text="Adult anxiety referral with sleep impairment.",
            status="contacted",
            patient_name="Phase Six Patient",
        )
        session.add(referral)
        session.flush()

        note = create_session_note(
            session,
            referral_id=referral.id,
            therapist_id=None,
            title="Initial session",
            body="Patient described anxiety, sleep impairment, and work stress. Risk denied.",
        )
        assert note["status"] == "draft"

        approved = approve_session_note(session, note["id"])
        assert approved["status"] == "approved"

        library = create_clinical_library_record(
            session,
            tenant_id=tenant.id,
            record_type="protocol",
            title="Anxiety protocol",
            body="Anxiety protocol requires sleep, risk, functional impairment, and support review.",
        )
        assert library["record_type"] == "protocol"

        chunks = search_retrieval_chunks(
            session,
            tenant_id=tenant.id,
            query_text="anxiety sleep risk",
            patient_id=approved["patient_id"],
        )
        assert chunks
        assert chunks[0]["source_type"] in {"session_note", "protocol"}

        workspace = patient_workspace(session, approved["patient_id"])
        assert workspace["session_notes"][0]["status"] == "approved"

        report = generate_report_draft(
            session,
            referral_id=referral.id,
            report_type="session_summary",
            title="Phase 6 summary",
            request_text="Summarize anxiety, sleep, and risk.",
        )
        assert report["status"] == "pending_signoff"
        assert report["claim_evidence_map"]

        signed = sign_off_report_draft(session, report["id"])
        assert signed["status"] == "signed_off"
    finally:
        session.rollback()
        session.close()
