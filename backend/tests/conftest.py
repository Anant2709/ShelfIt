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
# Point the curated files at paths that do not exist, so a test that does not
# explicitly install a dataset cannot silently depend on the repo's real one.
os.environ["SHELF_LIFE_PATH"] = str(_TMP_ROOT / "no-shelf-life.json")
os.environ["CATEGORIES_PATH"] = str(_TMP_ROOT / "no-categories.json")
os.environ["RECIPES_PATH"] = str(_TMP_ROOT / "no-recipes.json")
# A leftover frontend/dist on the laptop must not steal /api routes in tests.
os.environ["STATIC_DIR"] = str(_TMP_ROOT / "no-frontend-dist")
# Unset by default so no code path can reach OpenAI unless a test opts in.
os.environ["OPENAI_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.deps import get_db  # noqa: E402
from app.db.schema import upgrade_to_head  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.services.auth import COOKIE_NAME, create_session, create_user  # noqa: E402

# The learned-value stores and the SQL cache deliberately open their own sessions
# against the configured engine rather than the request's, because a value learned
# while handling a request has to outlive that request's transaction. They therefore
# need a real schema on the throwaway database above.
#
# Until migrations existed, that schema arrived as a side effect of importing
# app.main, which called create_all. Relying on production code to set up the test
# environment is how removing that call broke forty tests that had nothing to do
# with it. Building it here, from the migrations, states the dependency and
# exercises the same path a deployment takes.
upgrade_to_head()

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
    """A clean schema per test.

    Built from the models rather than by running migrations, because this happens
    once per test and migrations are far slower than create_all. That shortcut is
    only safe because test_migrations.py asserts the two produce the same schema; if
    they ever diverge, that test fails rather than this fixture quietly lying.
    """
    Base.metadata.create_all(bind=test_engine)
    session = TestSession()
    account = create_user(
        session, email="test@local", password="testpass1", timezone="UTC"
    )
    session.info["user"] = account
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def user(db):
    """The account most tests act as.

    Created with the schema so helpers that build items by hand can attach them
    without every test threading a user argument through.
    """
    return db.info["user"]


@pytest.fixture
def client(db, user):
    """TestClient signed in as `user`, sharing the test's database session."""

    def override_get_db():
        yield db

    token = create_session(db, user)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        test_client.cookies.set(COOKIE_NAME, token)
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client(db):
    """A client with no session cookie, for proving endpoints reject strangers."""

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    """Disable caching by default and reset every module-level cache.

    Without this, a value cached by one test would be visible to the next, and
    the suite would pass or fail depending on execution order. Tests that
    exercise caching opt in by constructing a real backend and passing it in.
    """
    from app.services import cache as cache_module
    from app.services import category as category_module
    from app.services import category_store as category_store_module
    from app.services import learned_store as learned_store_module
    from app.services import recipes as recipes_module
    from app.services import shelf_life as shelf_life_module

    def _reset():
        cache_module.reset_cache()
        shelf_life_module.reset_dataset_cache()
        learned_store_module.reset_learned_store()
        category_module.reset_category_dataset_cache()
        category_store_module.reset_category_store()
        recipes_module.reset_recipe_cache()

    _reset()
    monkeypatch.setattr(cache_module.settings, "cache_backend", "none")
    yield
    _reset()


@pytest.fixture(autouse=True)
def block_outbound_http(monkeypatch):
    """Fail loudly if a test makes a real outbound HTTP call via requests.

    No application code uses `requests` any more, so this is a guard against a
    future change reintroducing an unmocked network call. Tests that exercise an
    outbound path monkeypatch the specific client they care about instead.
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


SAMPLE_RECIPES = [
    {
        "id": "tomato-rice",
        "title": "Tomato rice",
        "slots": ["lunch", "dinner", "breakfast", "snack"],
        "patterns": ["omnivore", "vegetarian", "eggetarian", "vegan"],
        "allergens": [],
        "ingredients": [
            {"name": "Tomatoes", "aliases": ["Tomato", "Tomatoes"]},
            {"name": "Rice", "aliases": ["Basmati Rice", "Rice"]},
        ],
        "kcal": 450,
    },
    {
        "id": "chicken-rice",
        "title": "Chicken and rice",
        "slots": ["lunch", "dinner"],
        "patterns": ["omnivore"],
        "allergens": [],
        "ingredients": [
            {"name": "Chicken", "aliases": ["Chicken Breast", "Chicken"]},
            {"name": "Rice", "aliases": ["Basmati Rice", "Rice"]},
        ],
        "kcal": 520,
    },
    {
        "id": "paneer-tomato",
        "title": "Paneer and tomato",
        "slots": ["lunch", "dinner"],
        "patterns": ["omnivore", "vegetarian"],
        "allergens": ["dairy"],
        "ingredients": [
            {"name": "Paneer", "aliases": ["Paneer"]},
            {"name": "Tomatoes", "aliases": ["Tomato", "Tomatoes"]},
        ],
        "kcal": 480,
    },
    {
        "id": "veg-omelette",
        "title": "Vegetable omelette",
        "slots": ["breakfast"],
        "patterns": ["omnivore", "eggetarian"],
        "allergens": ["eggs"],
        "ingredients": [
            {"name": "Eggs", "aliases": ["Eggs", "Egg"]},
            {"name": "Tomatoes", "aliases": ["Tomato", "Tomatoes"]},
        ],
        "kcal": 350,
    },
]


@pytest.fixture
def recipes(tmp_path, monkeypatch):
    """Install a small curated set so diet tests do not read the repo file."""
    import json

    from app.core import config
    from app.services import recipes as recipes_module

    path = tmp_path / "recipes.json"
    path.write_text(json.dumps(SAMPLE_RECIPES), encoding="utf-8")
    monkeypatch.setattr(config.settings, "recipes_path", str(path))
    recipes_module.reset_recipe_cache()
    return path
