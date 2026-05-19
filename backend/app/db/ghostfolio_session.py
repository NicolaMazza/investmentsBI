from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.ghostfolio_db_url,
    pool_pre_ping=True,
    connect_args={"options": "-c search_path=ghostfolio"},
)

SessionLocal: sessionmaker[Session] = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
