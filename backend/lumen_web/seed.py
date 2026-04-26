"""Local seed data for the Lumen demo clinic."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Patient, Role, Tenant, Therapist, User


DEMO_TENANT_ID = "demo-clinic"
DEMO_USER_ID = "demo-admin"


def seed_demo_data(session: Session) -> None:
    tenant = session.get(Tenant, DEMO_TENANT_ID)
    if tenant is None:
        tenant = Tenant(id=DEMO_TENANT_ID, name="Demo Clinic", slug="demo-clinic")
        session.add(tenant)
        session.flush()

    for role in ("admin", "therapist", "clinic_director", "compliance_owner"):
        exists = session.scalar(select(Role).where(Role.tenant_id == DEMO_TENANT_ID, Role.name == role))
        if exists is None:
            session.add(Role(tenant_id=DEMO_TENANT_ID, name=role))

    user = session.get(User, DEMO_USER_ID)
    if user is None:
        session.add(
            User(
                id=DEMO_USER_ID,
                tenant_id=DEMO_TENANT_ID,
                email="admin@demo-clinic.local",
                display_name="Demo Admin",
                role="admin",
            )
        )

    patient = session.get(Patient, "demo-patient-001")
    if patient is None:
        session.add(
            Patient(
                id="demo-patient-001",
                tenant_id=DEMO_TENANT_ID,
                display_name="Demo Patient",
                contact_email="demo.patient@example.com",
                language="Portuguese",
            )
        )

    therapist_count = session.scalar(select(Therapist).where(Therapist.tenant_id == DEMO_TENANT_ID).limit(1))
    if therapist_count is not None:
        return

    session.add_all(
        [
            Therapist(
                id="demo-therapist-001",
                tenant_id=DEMO_TENANT_ID,
                name="Dr. Sofia Almeida",
                email="sofia.almeida@demo-clinic.local",
                specialties=["anxiety", "adjustment", "work stress"],
                age_groups=["adult", "older_adult"],
                languages=["Portuguese", "English"],
                modalities=["online", "hybrid"],
                insurers=["Multicare", "AdvanceCare", "self-pay"],
                capacity_per_week=6,
                availability_blocks=[
                    {"weekday": "Tuesday", "start": "10:00", "end": "13:00", "modality": "online"},
                    {"weekday": "Thursday", "start": "14:00", "end": "18:00", "modality": "hybrid"},
                ],
            ),
            Therapist(
                id="demo-therapist-002",
                tenant_id=DEMO_TENANT_ID,
                name="Miguel Costa",
                email="miguel.costa@demo-clinic.local",
                specialties=["adolescent mental health", "family transitions", "school stress"],
                age_groups=["adolescent", "adult"],
                languages=["Portuguese", "Spanish"],
                modalities=["in_person", "hybrid"],
                insurers=["Médis", "self-pay"],
                capacity_per_week=4,
                availability_blocks=[
                    {"weekday": "Monday", "start": "15:00", "end": "19:00", "modality": "in_person"},
                    {"weekday": "Wednesday", "start": "09:00", "end": "12:00", "modality": "hybrid"},
                ],
            ),
            Therapist(
                id="demo-therapist-003",
                tenant_id=DEMO_TENANT_ID,
                name="Ines Martins",
                email="ines.martins@demo-clinic.local",
                specialties=["trauma-informed care", "acute stress", "risk review"],
                age_groups=["adult"],
                languages=["Portuguese", "English"],
                modalities=["online", "in_person"],
                insurers=["AdvanceCare", "self-pay"],
                capacity_per_week=3,
                availability_blocks=[
                    {"weekday": "Friday", "start": "10:00", "end": "16:00", "modality": "online"},
                ],
            ),
        ]
    )
