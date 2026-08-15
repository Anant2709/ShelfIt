from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


def build_connect_args(database_url: str) -> dict:
    """Driver-specific connection arguments.

    SQLite defaults to rejecting cross-thread use, which breaks under FastAPI's
    threadpool. Other drivers must not receive the flag at all.
    """
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


connect_args = build_connect_args(settings.database_url)
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
