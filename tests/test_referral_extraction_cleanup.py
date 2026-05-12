from __future__ import annotations

from backend.lumen_web.db import SessionLocal
from backend.lumen_web.models import Referral, Tenant, WorkflowRun
from backend.lumen_web.repositories import update_referral_from_result


def test_email_extraction_cleanup_prefers_explicit_patient_name_and_discards_invented_fields() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-extraction-cleanup", name="Extraction Cleanup", slug="extraction-cleanup")
        referral = Referral(
            id="cleanup-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text="My name is Simon Anderson, I would like to book an appointment. You can use this email to contact me.",
            status="normalising",
            contact_email="lumenpatientdemo@gmail.com",
        )
        run = WorkflowRun(
            id="cleanup-run",
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="completed",
            input_summary="Email referral",
            request_payload={
                "raw_input": {
                    "source_channel": "email",
                    "raw_text": referral.raw_text,
                    "contact_email": "lumenpatientdemo@gmail.com",
                }
            },
            result={
                "outputs": {
                    "referral": {
                        "patient_name": "Michael Anderson",
                        "date_of_birth": "1985-04-15",
                        "contact_email": "lumenpatientdemo@gmail.com",
                        "contact_phone": "1234567890",
                        "insurer": "Blue Cross Blue Shield",
                        "referring_entity": "Lumen Health Demo",
                        "dedupe_candidates": [],
                    },
                    "clinical_signals": {"missing_required_fields": []},
                }
            },
        )
        session.add(tenant)
        session.flush()
        session.add_all([referral, run])
        session.flush()

        update_referral_from_result(session, run)

        assert referral.patient_name == "Simon Anderson"
        assert referral.date_of_birth is None
        assert referral.contact_phone is None
        assert referral.insurer is None
        assert referral.referring_entity is None
        assert set(referral.missing_fields) >= {"date_of_birth", "contact_phone", "insurer", "referring_entity"}
    finally:
        session.rollback()
        session.close()


def test_email_extraction_cleanup_keeps_facts_present_in_source() -> None:
    session = SessionLocal()
    try:
        tenant = Tenant(id="test-extraction-keep", name="Extraction Keep", slug="extraction-keep")
        raw_text = (
            "My name is Simon Anderson. DOB: 1990-07-12. Phone: 077384893339. "
            "Insurer Pancakes. Referred by mother."
        )
        referral = Referral(
            id="cleanup-keep-referral",
            tenant_id=tenant.id,
            source_channel="email",
            raw_text=raw_text,
            status="normalising",
            contact_email="lumenpatientdemo@gmail.com",
        )
        run = WorkflowRun(
            id="cleanup-keep-run",
            tenant_id=tenant.id,
            referral_id=referral.id,
            workflow_type="new_referral",
            status="completed",
            input_summary="Email referral",
            request_payload={
                "raw_input": {
                    "source_channel": "email",
                    "raw_text": raw_text,
                    "contact_email": "lumenpatientdemo@gmail.com",
                }
            },
            result={
                "outputs": {
                    "referral": {
                        "patient_name": "Simon Anderson",
                        "date_of_birth": "1990-07-12",
                        "contact_email": "lumenpatientdemo@gmail.com",
                        "contact_phone": "077384893339",
                        "insurer": "Pancakes",
                        "referring_entity": "mother",
                        "dedupe_candidates": [],
                    },
                    "clinical_signals": {"missing_required_fields": []},
                }
            },
        )
        session.add(tenant)
        session.flush()
        session.add_all([referral, run])
        session.flush()

        update_referral_from_result(session, run)

        assert referral.patient_name == "Simon Anderson"
        assert referral.date_of_birth == "1990-07-12"
        assert referral.contact_phone == "077384893339"
        assert referral.insurer == "Pancakes"
        assert referral.referring_entity == "mother"
        assert referral.missing_fields == []
    finally:
        session.rollback()
        session.close()
