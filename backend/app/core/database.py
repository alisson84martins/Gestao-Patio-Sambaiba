"""Conexão SQLAlchemy ao PostgreSQL e dependency get_db."""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,  # valida conexão antes de usar (resiliente a quedas)
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base class para todos os modelos SQLAlchemy."""

    pass


def get_db() -> Generator[Session, None, None]:
    """Dependency injection para FastAPI: fornece uma sessão e fecha ao fim do request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
