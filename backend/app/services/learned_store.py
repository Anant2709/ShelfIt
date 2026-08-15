"""Storage for shelf lives the system worked out for itself.

This is the second of two sources of truth. The first is the curated file, which
is human-authored and read-only at runtime; this one holds machine-derived values,
reported with their own provenance so they are never mistaken for curated data.

It replaces the generic TTL cache for shelf-life answers. A cache is the wrong
shape here: these values are semantic rather than opaque, should not silently
expire, need to be inspected and corrected by a person, and carry an audit trail.
The generic cache remains in use for image recognition, where none of that applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models.shelf_life import LearnedShelfLife


@dataclass(frozen=True)
class LearnedEntry:
    """A detached snapshot, so callers never hold a live ORM row."""

    name: str
    days: int
    anchor: str | None = None
    anchor_days: int | None = None
    model: str | None = None
    confirmed: bool = False

    @property
    def is_anchored(self) -> bool:
        return self.anchor is not None


def _to_entry(row: LearnedShelfLife) -> LearnedEntry:
    return LearnedEntry(
        name=row.name,
        days=row.days,
        anchor=row.anchor,
        anchor_days=row.anchor_days,
        model=row.model,
        confirmed=row.confirmed_at is not None,
    )


class LearnedShelfLifeStore:
    """Repository over the learned table.

    Owns its own short-lived sessions so the shelf-life cascade stays free of a
    session parameter. The factory is injectable for tests.
    """

    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory

    def _factory(self) -> Callable[[], Session]:
        if self._session_factory is not None:
            return self._session_factory
        from app.db.session import SessionLocal

        return SessionLocal

    def get(self, name: str) -> LearnedEntry | None:
        session = self._factory()()
        try:
            row = session.get(LearnedShelfLife, name)
            return _to_entry(row) if row is not None else None
        finally:
            session.close()

    def remember(
        self,
        name: str,
        days: int,
        anchor: str | None = None,
        anchor_days: int | None = None,
        model: str | None = None,
    ) -> LearnedEntry:
        session = self._factory()()
        try:
            row = session.merge(
                LearnedShelfLife(
                    name=name,
                    days=days,
                    anchor=anchor,
                    anchor_days=anchor_days,
                    model=model,
                    created_at=utcnow(),
                )
            )
            session.commit()
            return _to_entry(row)
        finally:
            session.close()

    def all(self) -> list[LearnedEntry]:
        session = self._factory()()
        try:
            rows = session.query(LearnedShelfLife).order_by(LearnedShelfLife.name).all()
            return [_to_entry(row) for row in rows]
        finally:
            session.close()

    def pending(self) -> list[LearnedEntry]:
        """Entries a human has not yet reviewed."""
        session = self._factory()()
        try:
            rows = (
                session.query(LearnedShelfLife)
                .filter(LearnedShelfLife.confirmed_at.is_(None))
                .order_by(LearnedShelfLife.name)
                .all()
            )
            return [_to_entry(row) for row in rows]
        finally:
            session.close()

    def confirm(self, name: str) -> bool:
        session = self._factory()()
        try:
            row = session.get(LearnedShelfLife, name)
            if row is None:
                return False
            row.confirmed_at = utcnow()
            session.add(row)
            session.commit()
            return True
        finally:
            session.close()

    def forget(self, name: str) -> bool:
        session = self._factory()()
        try:
            row = session.get(LearnedShelfLife, name)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True
        finally:
            session.close()

    def stale(self, curated: dict[str, int]) -> list[LearnedEntry]:
        """Entries whose anchor no longer holds the value they were derived from.

        This is what the anchor buys: after editing the curated file, the affected
        derived values can be found instead of silently drifting.
        """
        return [
            entry
            for entry in self.all()
            if entry.anchor is not None
            and entry.anchor in curated
            and curated[entry.anchor] != entry.anchor_days
        ]

    def clear(self) -> None:
        session = self._factory()()
        try:
            session.query(LearnedShelfLife).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()


_store: LearnedShelfLifeStore | None = None


def get_learned_store() -> LearnedShelfLifeStore:
    global _store
    if _store is None:
        _store = LearnedShelfLifeStore()
    return _store


def reset_learned_store() -> None:
    global _store
    _store = None
