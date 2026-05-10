from __future__ import annotations

from backend.lumen_web.db import Base, SessionLocal, engine
from backend.lumen_web.models import User
from backend.lumen_web.repositories import reset_clean_demo_referral, security_context, therapist_for_user
from backend.lumen_web.seed import (
    DEMO_CLARA_EMAIL,
    DEMO_CLARA_THERAPIST_USER_ID,
    DEMO_USER_ID,
    seed_demo_data,
)


def test_clara_user_exists_after_seed_demo_setup() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        seed_demo_data(session)

        user = session.get(User, DEMO_CLARA_THERAPIST_USER_ID)

        assert user is not None
        assert user.email == DEMO_CLARA_EMAIL
        assert user.role == "therapist"
        assert user.active is True
    finally:
        session.rollback()
        session.close()


def test_clara_user_resolves_to_clara_therapist_record() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        reset_clean_demo_referral(session)

        user = session.get(User, DEMO_CLARA_THERAPIST_USER_ID)
        therapist = therapist_for_user(session, user)
        context = security_context(session, DEMO_CLARA_THERAPIST_USER_ID)

        assert user is not None
        assert context["user"]["id"] == DEMO_CLARA_THERAPIST_USER_ID
        assert context["user"]["role"] == "therapist"
        assert therapist is not None
        assert therapist["id"] == "demo-clean-therapist-001"
        assert therapist["email"] == DEMO_CLARA_EMAIL
    finally:
        session.rollback()
        session.close()


def test_clara_therapist_identity_does_not_affect_demo_admin() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        reset_clean_demo_referral(session)

        admin_context = security_context(session, DEMO_USER_ID)
        admin_therapist = therapist_for_user(session, DEMO_USER_ID)

        assert admin_context["user"]["id"] == DEMO_USER_ID
        assert admin_context["user"]["role"] == "admin"
        assert admin_therapist is None
    finally:
        session.rollback()
        session.close()
