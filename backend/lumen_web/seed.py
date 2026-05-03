"""Local seed data for the Lumen demo clinic."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ClinicalLibraryRecord, DocumentChunk, IntakeTemplate, Patient, Role, Tenant, Therapist, User


DEMO_TENANT_ID = "demo-clinic"
DEMO_USER_ID = "demo-admin"
DEMO_THERAPIST_USER_ID = "demo-therapist-user"
DEMO_DIRECTOR_USER_ID = "demo-director"


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
    therapist_user = session.get(User, DEMO_THERAPIST_USER_ID)
    if therapist_user is None:
        session.add(
            User(
                id=DEMO_THERAPIST_USER_ID,
                tenant_id=DEMO_TENANT_ID,
                email="therapist@demo-clinic.local",
                display_name="Demo Therapist",
                role="therapist",
            )
        )
    director_user = session.get(User, DEMO_DIRECTOR_USER_ID)
    if director_user is None:
        session.add(
            User(
                id=DEMO_DIRECTOR_USER_ID,
                tenant_id=DEMO_TENANT_ID,
                email="director@demo-clinic.local",
                display_name="Demo Director",
                role="clinic_director",
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

    template = session.scalar(select(IntakeTemplate).where(IntakeTemplate.tenant_id == DEMO_TENANT_ID).limit(1))
    if template is None:
        session.add(
            IntakeTemplate(
                id="demo-intake-standard",
                tenant_id=DEMO_TENANT_ID,
                name="Standard first-session intake",
                patient_type="standard",
                required_items=[
                    {
                        "key": "privacy_notice",
                        "label": "Privacy notice acknowledged",
                        "type": "consent",
                        "consent_scope": "privacy_notice",
                        "due_days": 3,
                    },
                    {
                        "key": "telehealth_consent",
                        "label": "Telehealth consent",
                        "type": "consent",
                        "consent_scope": "telehealth",
                        "due_days": 3,
                    },
                    {
                        "key": "intake_form",
                        "label": "Clinical intake form",
                        "type": "form",
                        "due_days": 5,
                    },
                    {
                        "key": "screening_questionnaire",
                        "label": "Pre-session screening questionnaire",
                        "type": "questionnaire",
                        "due_days": 5,
                    },
                ],
                questionnaire_schema={
                    "name": "generic_screening",
                    "questions": [
                        {"key": "mood", "label": "Mood difficulty", "type": "number", "min": 0, "max": 3},
                        {"key": "anxiety", "label": "Anxiety difficulty", "type": "number", "min": 0, "max": 3},
                        {"key": "sleep", "label": "Sleep difficulty", "type": "number", "min": 0, "max": 3},
                    ],
                },
            )
        )

    library_record = session.scalar(
        select(ClinicalLibraryRecord).where(ClinicalLibraryRecord.tenant_id == DEMO_TENANT_ID).limit(1)
    )
    if library_record is None:
        library_records = [
            ClinicalLibraryRecord(
                tenant_id=DEMO_TENANT_ID,
                record_type="protocol",
                title="Demo anxiety intake protocol",
                version="2026.04",
                body=(
                    "For adult anxiety referrals, collect presenting concerns, risk signals, medication status, "
                    "current support, sleep impact, and functional impairment before first session."
                ),
                metadata_json={"scope": "demo"},
            ),
            ClinicalLibraryRecord(
                tenant_id=DEMO_TENANT_ID,
                record_type="template",
                title="Brief session summary template",
                version="2026.04",
                body=(
                    "Session summaries should include presenting concern, intervention focus, risk update, "
                    "home practice, agreed next step, and evidence references."
                ),
                metadata_json={"scope": "demo"},
            ),
        ]
        session.add_all(library_records)
        session.flush()
        for record in library_records:
            _seed_chunk_for_library_record(session, record)
    else:
        missing_chunks = not session.scalar(
            select(DocumentChunk).where(DocumentChunk.tenant_id == DEMO_TENANT_ID).limit(1)
        )
        if missing_chunks:
            for record in session.scalars(
                select(ClinicalLibraryRecord).where(ClinicalLibraryRecord.tenant_id == DEMO_TENANT_ID)
            ):
                _seed_chunk_for_library_record(session, record)

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


def _seed_chunk_for_library_record(session: Session, record: ClinicalLibraryRecord) -> None:
    exists = session.scalar(
        select(DocumentChunk).where(
            DocumentChunk.source_type == record.record_type,
            DocumentChunk.source_id == record.id,
            DocumentChunk.chunk_index == 0,
        )
    )
    if exists is not None:
        return
    session.add(
        DocumentChunk(
            tenant_id=record.tenant_id,
            source_type=record.record_type,
            source_id=record.id,
            chunk_index=0,
            text=record.body,
            metadata_json={"title": record.title, "version": record.version, "record_type": record.record_type},
            embedding_model="keyword-mvp",
        )
    )
