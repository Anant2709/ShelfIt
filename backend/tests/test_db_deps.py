"""Tests for the database session dependency.

Handlers receive their session through FastAPI's dependency injection, which is
what guarantees the session is closed after every request and what lets the test
suite substitute a throwaway database.
"""

import pytest

from app.db import deps


class SpySession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def spy_session(monkeypatch):
    session = SpySession()
    monkeypatch.setattr(deps, "SessionLocal", lambda: session)
    return session


def test_yields_a_session(spy_session):
    generator = deps.get_db()
    assert next(generator) is spy_session


def test_session_is_closed_after_normal_completion(spy_session):
    generator = deps.get_db()
    next(generator)
    assert spy_session.closed is False
    with pytest.raises(StopIteration):
        next(generator)
    assert spy_session.closed is True


def test_session_is_closed_even_when_the_handler_raises(spy_session):
    """The finally block is what prevents connection leaks on error paths."""
    generator = deps.get_db()
    next(generator)
    with pytest.raises(RuntimeError):
        generator.throw(RuntimeError("handler blew up"))
    assert spy_session.closed is True


def test_each_request_gets_its_own_session(monkeypatch):
    created = []

    def factory():
        session = SpySession()
        created.append(session)
        return session

    monkeypatch.setattr(deps, "SessionLocal", factory)
    for _ in range(2):
        generator = deps.get_db()
        next(generator)
        with pytest.raises(StopIteration):
            next(generator)
    assert len(created) == 2
    assert all(session.closed for session in created)
