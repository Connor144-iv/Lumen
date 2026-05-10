from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import (
    DocumentationSession,
    DocumentationSessionNote,
    DocumentationSessionText,
    Patient,
    Tenant,
    Therapist,
)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def test_documentation_session_text_and_note_use_canonical_patient_and_therapist_ids() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        tenant = Tenant(id=_id("tenant"), name="Documentation Tenant", slug=_id("documentation"))
        session.add(tenant)
        session.flush()

        patient = Patient(
            tenant_id=tenant.id,
            display_name="Documentation Patient",
            contact_email="documentation.patient@example.com",
        )
        therapist = Therapist(
            tenant_id=tenant.id,
            name="Documentation Therapist",
            email="documentation.therapist@example.com",
            specialties=[],
            age_groups=["adult"],
            languages=["English"],
            modalities=["online"],
            insurers=["self-pay"],
            capacity_per_week=3,
            availability_blocks=[],
        )
        session.add_all([patient, therapist])
        session.flush()

        doc_session = DocumentationSession(
            tenant_id=tenant.id,
            patient_id=patient.id,
            therapist_id=therapist.id,
            title="Initial documentation session",
            patient_label_snapshot=patient.display_name,
            therapist_label_snapshot=therapist.name,
            status="active",
        )
        session.add(doc_session)
        session.flush()

        source_text = DocumentationSessionText(
            tenant_id=tenant.id,
            documentation_session_id=doc_session.id,
            text="Patient described anxiety symptoms and agreed to a grounding exercise.",
            input_type="manual_text",
            source_metadata={"origin": "test"},
            raw_source_stored=False,
        )
        session.add(source_text)
        session.flush()

        note = DocumentationSessionNote(
            tenant_id=tenant.id,
            documentation_session_id=doc_session.id,
            source_text_id=source_text.id,
            note_json={"summary": "Anxiety symptoms discussed."},
            status="draft",
            generator="test",
            generated_at=datetime.now(timezone.utc),
        )
        session.add(note)
        session.flush()

        saved = session.get(DocumentationSession, doc_session.id)
        saved_text = session.get(DocumentationSessionText, source_text.id)
        saved_note = session.get(DocumentationSessionNote, note.id)

        assert saved is not None
        assert saved.patient_id == patient.id
        assert saved.therapist_id == therapist.id
        assert saved.patient_label_snapshot == "Documentation Patient"
        assert saved.therapist_label_snapshot == "Documentation Therapist"
        assert saved_text is not None
        assert saved_text.documentation_session_id == saved.id
        assert saved_note is not None
        assert saved_note.documentation_session_id == saved.id
        assert saved_note.source_text_id == saved_text.id
        assert saved_note.note_json["summary"] == "Anxiety symptoms discussed."
    finally:
        session.rollback()
        session.close()
