from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from starlette.requests import Request

from app import my_therapist, my_therapist_patients
from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import Appointment, HumanReviewTask, Patient
from backend.lumen_web.repositories import DEMO_CLEAN_THERAPIST_ID, reset_clean_demo_referral
from backend.lumen_web.seed import DEMO_CLARA_EMAIL, DEMO_CLARA_THERAPIST_USER_ID, DEMO_TENANT_ID, DEMO_USER_ID


def _request_for_user(user_id: str) -> Request:
    return Request({"type": "http", "headers": [(b"x-lumen-user-id", user_id.encode("utf-8"))]})


def _prepare_clara_without_appointments() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        reset_clean_demo_referral(session)
        appointment_ids = list(
            session.scalars(select(Appointment.id).where(Appointment.therapist_id == DEMO_CLEAN_THERAPIST_ID))
        )
        if appointment_ids:
            session.execute(delete(HumanReviewTask).where(HumanReviewTask.appointment_id.in_(appointment_ids)))
            session.execute(delete(Appointment).where(Appointment.id.in_(appointment_ids)))
        session.commit()
    finally:
        session.close()


def test_clara_can_resolve_current_therapist_record() -> None:
    _prepare_clara_without_appointments()

    response = my_therapist(_request_for_user(DEMO_CLARA_THERAPIST_USER_ID))

    therapist = response["therapist"]
    assert therapist["id"] == DEMO_CLEAN_THERAPIST_ID
    assert therapist["email"] == DEMO_CLARA_EMAIL
    assert therapist["active"] is True


def test_admin_cannot_resolve_current_therapist_record() -> None:
    _prepare_clara_without_appointments()

    with pytest.raises(HTTPException) as exc_info:
        my_therapist(_request_for_user(DEMO_USER_ID))

    assert exc_info.value.status_code == 403
    assert "active therapist" in exc_info.value.detail


def test_clara_patient_list_is_empty_without_appointments() -> None:
    _prepare_clara_without_appointments()

    response = my_therapist_patients(_request_for_user(DEMO_CLARA_THERAPIST_USER_ID))

    assert response["patients"] == []


def test_clara_patient_list_includes_patient_from_appointment() -> None:
    _prepare_clara_without_appointments()
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    session = SessionLocal()
    try:
        patient = session.get(Patient, "test-clara-patient")
        if patient is None:
            patient = Patient(id="test-clara-patient", tenant_id=DEMO_TENANT_ID)
            session.add(patient)
        patient.display_name = "Test Clara Patient"
        patient.contact_email = "test.clara.patient@example.com"
        patient.language = "English"
        session.flush()
        session.add(
            Appointment(
                id="test-clara-appointment",
                tenant_id=DEMO_TENANT_ID,
                patient_id=patient.id,
                therapist_id=DEMO_CLEAN_THERAPIST_ID,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=60),
                status="confirmed",
            )
        )
        session.commit()
    finally:
        session.close()

    response = my_therapist_patients(_request_for_user(DEMO_CLARA_THERAPIST_USER_ID))

    patients = response["patients"]
    assert [patient["id"] for patient in patients] == ["test-clara-patient"]
    assert patients[0]["display_name"] == "Test Clara Patient"
