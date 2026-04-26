"""Database setup for the Lumen web product layer."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = f"sqlite:///{BASE_DIR / 'lumen_dev.db'}"


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    url = os.getenv("LUMEN_APP_DATABASE_URL") or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _connect_args(url: str) -> dict[str, object]:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


engine = create_engine(database_url(), future=True, connect_args=_connect_args(database_url()))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, future=True)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
    if engine.url.get_backend_name() != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_database() -> None:
    from . import models  # noqa: F401
    from .seed import seed_demo_data

    Base.metadata.create_all(bind=engine)
    with session_scope() as session:
        seed_demo_data(session)
