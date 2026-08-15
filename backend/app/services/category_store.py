"""Storage for categories the system worked out for itself.

Deliberately not the generic TTL cache. These values are semantic rather than
opaque, should not silently expire (a tomato does not stop being produce), and
need to be inspectable and correctable by a person. Same reasoning as the
learned shelf-life store, and the same shape, so there is one pattern to learn
rather than two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models.category import LearnedCategory


@dataclass(frozen=True)
class LearnedCategoryEntry:
    """A detached snapshot, so callers never hold a live ORM row."""

    name: str
    category: str
    model: str | None = None
    confirmed: bool = False


def _to_entry(row: LearnedCategory) -> LearnedCategoryEntry:
    return LearnedCategoryEntry(
        name=row.name,
        category=row.category,
        model=row.model,
        confirmed=row.confirmed_at is not None,
    )


class LearnedCategoryStore:
    """Repository over the learned category table.

    Owns its own short-lived sessions so the resolver stays free of a session
    parameter. The factory is injectable for tests.
    """

    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory

    def _factory(self) -> Callable[[], Session]:
        if self._session_factory is not None:
            return self._session_factory
        from app.db.session import SessionLocal

        return SessionLocal

    def get(self, name: str) -> LearnedCategoryEntry | None:
        session = self._factory()()
        try:
            row = session.get(LearnedCategory, name)
            return _to_entry(row) if row is not None else None
        finally:
            session.close()

    def remember(
        self,
        name: str,
        category: str,
        model: str | None = None,
    ) -> LearnedCategoryEntry:
        session = self._factory()()
        try:
            row = session.merge(
                LearnedCategory(
                    name=name,
                    category=category,
                    model=model,
                    created_at=utcnow(),
                )
            )
            session.commit()
            return _to_entry(row)
        finally:
            session.close()

    def all(self) -> list[LearnedCategoryEntry]:
        session = self._factory()()
        try:
            rows = (
                session.query(LearnedCategory).order_by(LearnedCategory.name).all()
            )
            return [_to_entry(row) for row in rows]
        finally:
            session.close()

    def forget(self, name: str) -> bool:
        session = self._factory()()
        try:
            row = session.get(LearnedCategory, name)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True
        finally:
            session.close()

    def clear(self) -> None:
        session = self._factory()()
        try:
            session.query(LearnedCategory).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()


_store: LearnedCategoryStore | None = None


def get_category_store() -> LearnedCategoryStore:
    global _store
    if _store is None:
        _store = LearnedCategoryStore()
    return _store


def reset_category_store() -> None:
    global _store
    _store = None
