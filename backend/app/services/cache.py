"""Caching for expensive lookups.

Two motivations, and the second matters more than the first:

1. Cost. Every avoided call to a paid API or an LLM is money not spent.
2. Determinism. An LLM asked "how long does milk keep?" can answer 5 today and 7
   tomorrow. Caching the first answer makes the system's behaviour reproducible,
   which turns a correctness problem into a non-problem.

The backend is swappable behind the `Cache` protocol -- the same seam pattern the
classifier uses -- so caching can be persisted, held in memory, or switched off
entirely without any caller changing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.models.cache import CacheEntry


class _Miss:
    """Distinguishes "nothing cached" from "None was cached".

    This distinction is the whole point of the cache. Resolving an unknown item
    is exactly as expensive as resolving a known one, so a negative result must
    be cacheable. If a miss and a cached None were both represented by None,
    every unresolvable item would be re-queried forever.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<cache miss>"

    def __bool__(self) -> bool:
        return False


MISS = _Miss()


@dataclass
class CacheStats:
    """Counters for observability, and for demonstrating the cache earns its keep."""

    hits: int = 0
    misses: int = 0
    writes: int = 0

    @property
    def lookups(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.lookups if self.lookups else 0.0

    def as_dict(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "lookups": self.lookups,
            "hit_rate": round(self.hit_rate, 4),
        }


@runtime_checkable
class Cache(Protocol):
    stats: CacheStats

    def get(self, namespace: str, key: str) -> Any: ...

    def set(
        self, namespace: str, key: str, value: Any, ttl: timedelta | None = None
    ) -> None: ...

    def clear(self, namespace: str | None = None) -> None: ...


class NullCache:
    """Caches nothing. Used to isolate tests and to debug cache-related issues."""

    def __init__(self) -> None:
        self.stats = CacheStats()

    def get(self, namespace: str, key: str) -> Any:
        self.stats.misses += 1
        return MISS

    def set(
        self, namespace: str, key: str, value: Any, ttl: timedelta | None = None
    ) -> None:
        return None

    def clear(self, namespace: str | None = None) -> None:
        return None


class InMemoryCache:
    """Process-local cache. Fast, but everything is lost on restart."""

    def __init__(self) -> None:
        self.stats = CacheStats()
        self._entries: dict[tuple[str, str], tuple[Any, Any]] = {}

    def get(self, namespace: str, key: str) -> Any:
        entry = self._entries.get((namespace, key))
        if entry is None:
            self.stats.misses += 1
            return MISS
        value, expires_at = entry
        if expires_at is not None and expires_at <= utcnow():
            del self._entries[(namespace, key)]
            self.stats.misses += 1
            return MISS
        self.stats.hits += 1
        return value

    def set(
        self, namespace: str, key: str, value: Any, ttl: timedelta | None = None
    ) -> None:
        expires_at = utcnow() + ttl if ttl is not None else None
        self._entries[(namespace, key)] = (value, expires_at)
        self.stats.writes += 1

    def clear(self, namespace: str | None = None) -> None:
        if namespace is None:
            self._entries.clear()
            return
        for entry_key in [k for k in self._entries if k[0] == namespace]:
            del self._entries[entry_key]


class SqlCache:
    """Database-backed cache that survives process restarts.

    Values are JSON-encoded, so anything stored must be JSON-serialisable. The
    session factory is injectable so tests can point it at a throwaway database.
    """

    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self.stats = CacheStats()
        self._session_factory = session_factory

    def _factory(self) -> Callable[[], Session]:
        if self._session_factory is not None:
            return self._session_factory
        # Imported lazily so that constructing a SqlCache does not require the
        # engine to be configured yet.
        from app.db.session import SessionLocal

        return SessionLocal

    def get(self, namespace: str, key: str) -> Any:
        session = self._factory()()
        try:
            entry = session.get(CacheEntry, {"namespace": namespace, "key": key})
            if entry is None:
                self.stats.misses += 1
                return MISS
            if entry.expires_at is not None and entry.expires_at <= utcnow():
                session.delete(entry)
                session.commit()
                self.stats.misses += 1
                return MISS
            self.stats.hits += 1
            return json.loads(entry.value_json)
        finally:
            session.close()

    def set(
        self, namespace: str, key: str, value: Any, ttl: timedelta | None = None
    ) -> None:
        session = self._factory()()
        try:
            session.merge(
                CacheEntry(
                    namespace=namespace,
                    key=key,
                    value_json=json.dumps(value),
                    created_at=utcnow(),
                    expires_at=utcnow() + ttl if ttl is not None else None,
                )
            )
            session.commit()
            self.stats.writes += 1
        finally:
            session.close()

    def clear(self, namespace: str | None = None) -> None:
        session = self._factory()()
        try:
            query = session.query(CacheEntry)
            if namespace is not None:
                query = query.filter(CacheEntry.namespace == namespace)
            query.delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()


BACKENDS: dict[str, Callable[[], Cache]] = {
    "sql": SqlCache,
    "memory": InMemoryCache,
    "none": NullCache,
}


def build_cache(backend: str | None = None) -> Cache:
    name = (backend or settings.cache_backend).strip().lower()
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown cache backend {name!r}. Choose one of {sorted(BACKENDS)}."
        ) from None


_cache: Cache | None = None


def get_cache() -> Cache:
    """The process-wide cache, created on first use."""
    global _cache
    if _cache is None:
        _cache = build_cache()
    return _cache


def reset_cache() -> None:
    """Drop the process-wide instance so the next call rebuilds it."""
    global _cache
    _cache = None
