"""Shared test fixtures.

Isolation is the whole point of this module. Before any application code is
imported we repoint every filesystem- and network-touching setting at throwaway
locations, so a test run can never read or write the developer's real database,
uploads directory, or API quota.
"""

import os
import tempfile
from pathlib import Path

import pytest

# Must happen before `app.core.config` is imported, because Settings is
# instantiated at import time and reads the environment exactly once.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="shelfit-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_ROOT / 'import-time.db'}"
os.environ["UPLOAD_DIR"] = str(_TMP_ROOT / "uploads")
os.environ["MODEL_PATH"] = str(_TMP_ROOT / "model-that-does-not-exist.pt")
# Unset by default so the shelf-life cascade cannot reach Spoonacular and the
# chatbot cannot reach OpenAI unless a test opts in explicitly.
os.environ["SHELF_LIFE_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.deps import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402

# StaticPool keeps a single connection alive, which is what makes an in-memory
# SQLite database visible to both the test body and the request handlers.
test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)


@pytest.fixture
def db():
    """A clean schema per test."""
    Base.metadata.create_all(bind=test_engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client(db):
    """TestClient whose handlers share the test's database session."""

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def block_outbound_http(monkeypatch):
    """Fail loudly if a test makes a real outbound HTTP call via requests.

    Tests that need to exercise an outbound-call path monkeypatch the specific
    function they care about instead.
    """
    import requests

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "Unmocked outbound HTTP request during tests. "
            "Monkeypatch the call site instead."
        )

    monkeypatch.setattr(requests.sessions.Session, "request", _forbidden)


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    """Redirect uploads at a per-test temporary directory.

    Nested inside tmp_path rather than created with mkdtemp, because the label
    endpoint derives the training-data location from `upload_dir.parent`. A
    top-level temp directory would make that parent the shared system temp root
    and leak training artifacts between tests.
    """
    from app.core import config

    target = tmp_path / "uploads"
    target.mkdir()
    monkeypatch.setattr(config.settings, "upload_dir", str(target))
    return target


@pytest.fixture
def sample_image_bytes():
    """A tiny valid PNG, enough for endpoints that only persist the bytes."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
