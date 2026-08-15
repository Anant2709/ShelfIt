"""Tests for the cache backends.

Every backend is held to the same contract, so the parametrised class is the
important part: it proves the backends are genuinely interchangeable.
"""

from datetime import timedelta

import pytest

from app.services import cache as cache_module
from app.services.cache import (
    MISS,
    Cache,
    CacheStats,
    InMemoryCache,
    NullCache,
    SqlCache,
    build_cache,
    get_cache,
    reset_cache,
)


@pytest.fixture(params=["memory", "sql"])
def caching_backend(request, db):
    """Each backend that actually stores values, wired to the test database."""
    if request.param == "memory":
        return InMemoryCache()
    return SqlCache(session_factory=lambda: db)


class TestSharedContract:
    def test_miss_on_unknown_key(self, caching_backend):
        assert caching_backend.get("ns", "absent") is MISS

    def test_round_trips_a_value(self, caching_backend):
        caching_backend.set("ns", "milk", {"days": 5, "source": "api"})
        assert caching_backend.get("ns", "milk") == {"days": 5, "source": "api"}

    def test_caches_a_negative_result(self, caching_backend):
        """The reason MISS exists: None must be storable and retrievable."""
        caching_backend.set("ns", "saffron", {"days": None, "source": "unknown"})
        result = caching_backend.get("ns", "saffron")
        assert result is not MISS
        assert result == {"days": None, "source": "unknown"}

    def test_namespaces_are_isolated(self, caching_backend):
        caching_backend.set("a", "key", "from-a")
        caching_backend.set("b", "key", "from-b")
        assert caching_backend.get("a", "key") == "from-a"
        assert caching_backend.get("b", "key") == "from-b"

    def test_writing_the_same_key_replaces(self, caching_backend):
        caching_backend.set("ns", "milk", {"days": 5})
        caching_backend.set("ns", "milk", {"days": 7})
        assert caching_backend.get("ns", "milk") == {"days": 7}

    @pytest.mark.parametrize(
        "value", [None, 0, False, "", [], {}, {"nested": {"list": [1, 2]}}]
    )
    def test_falsy_and_nested_values_survive(self, caching_backend, value):
        caching_backend.set("ns", "key", value)
        assert caching_backend.get("ns", "key") == value

    def test_expired_entry_is_a_miss(self, caching_backend):
        caching_backend.set("ns", "milk", {"days": 5}, ttl=timedelta(seconds=-1))
        assert caching_backend.get("ns", "milk") is MISS

    def test_unexpired_entry_is_a_hit(self, caching_backend):
        caching_backend.set("ns", "milk", {"days": 5}, ttl=timedelta(days=1))
        assert caching_backend.get("ns", "milk") == {"days": 5}

    def test_no_ttl_means_no_expiry(self, caching_backend):
        caching_backend.set("ns", "milk", {"days": 5})
        assert caching_backend.get("ns", "milk") == {"days": 5}

    def test_clear_removes_everything(self, caching_backend):
        caching_backend.set("a", "x", 1)
        caching_backend.set("b", "y", 2)
        caching_backend.clear()
        assert caching_backend.get("a", "x") is MISS
        assert caching_backend.get("b", "y") is MISS

    def test_clear_can_target_one_namespace(self, caching_backend):
        caching_backend.set("a", "x", 1)
        caching_backend.set("b", "y", 2)
        caching_backend.clear(namespace="a")
        assert caching_backend.get("a", "x") is MISS
        assert caching_backend.get("b", "y") == 2

    def test_stats_track_hits_and_misses(self, caching_backend):
        caching_backend.get("ns", "absent")
        caching_backend.set("ns", "milk", {"days": 5})
        caching_backend.get("ns", "milk")
        caching_backend.get("ns", "milk")
        assert caching_backend.stats.hits == 2
        assert caching_backend.stats.misses == 1
        assert caching_backend.stats.writes == 1
        assert caching_backend.stats.hit_rate == pytest.approx(2 / 3)

    def test_satisfies_the_cache_protocol(self, caching_backend):
        assert isinstance(caching_backend, Cache)


class TestNullCache:
    def test_never_returns_a_value(self):
        cache = NullCache()
        cache.set("ns", "milk", {"days": 5})
        assert cache.get("ns", "milk") is MISS

    def test_counts_every_lookup_as_a_miss(self):
        cache = NullCache()
        cache.get("ns", "a")
        cache.get("ns", "b")
        assert cache.stats.misses == 2
        assert cache.stats.hits == 0

    def test_clear_is_a_no_op(self):
        cache = NullCache()
        cache.clear()
        cache.clear(namespace="ns")

    def test_satisfies_the_cache_protocol(self):
        assert isinstance(NullCache(), Cache)


class TestSqlPersistence:
    def test_values_outlive_the_cache_object(self, db):
        """This is the property an in-memory cache cannot provide."""
        SqlCache(session_factory=lambda: db).set("ns", "milk", {"days": 5})
        revived = SqlCache(session_factory=lambda: db)
        assert revived.get("ns", "milk") == {"days": 5}

    def test_expired_rows_are_deleted_on_read(self, db):
        from app.models.cache import CacheEntry

        cache = SqlCache(session_factory=lambda: db)
        cache.set("ns", "milk", {"days": 5}, ttl=timedelta(seconds=-1))
        assert db.query(CacheEntry).count() == 1
        cache.get("ns", "milk")
        assert db.query(CacheEntry).count() == 0, "stale row should be reaped"

    def test_falls_back_to_the_configured_session_factory(self):
        """Constructed without a factory, it resolves SessionLocal lazily."""
        from app.db.session import SessionLocal

        assert SqlCache()._factory() is SessionLocal


class TestStats:
    def test_hit_rate_of_an_unused_cache_is_zero(self):
        assert CacheStats().hit_rate == 0.0

    def test_as_dict_shape(self):
        stats = CacheStats(hits=3, misses=1, writes=1)
        assert stats.as_dict() == {
            "hits": 3,
            "misses": 1,
            "writes": 1,
            "lookups": 4,
            "hit_rate": 0.75,
        }


class TestFactory:
    @pytest.mark.parametrize(
        "name,expected",
        [("sql", SqlCache), ("memory", InMemoryCache), ("none", NullCache)],
    )
    def test_builds_each_backend(self, name, expected):
        assert isinstance(build_cache(name), expected)

    @pytest.mark.parametrize("name", ["SQL", " memory ", "None"])
    def test_backend_name_is_case_and_space_insensitive(self, name):
        assert build_cache(name) is not None

    def test_unknown_backend_is_rejected_with_the_valid_options(self):
        with pytest.raises(ValueError, match="Unknown cache backend"):
            build_cache("redis")

    def test_defaults_to_the_configured_backend(self, monkeypatch):
        monkeypatch.setattr(cache_module.settings, "cache_backend", "memory")
        assert isinstance(build_cache(), InMemoryCache)

    def test_get_cache_returns_a_singleton(self, monkeypatch):
        monkeypatch.setattr(cache_module.settings, "cache_backend", "memory")
        reset_cache()
        try:
            assert get_cache() is get_cache()
        finally:
            reset_cache()

    def test_reset_cache_forces_a_rebuild(self, monkeypatch):
        monkeypatch.setattr(cache_module.settings, "cache_backend", "memory")
        reset_cache()
        try:
            first = get_cache()
            reset_cache()
            assert get_cache() is not first
        finally:
            reset_cache()


def test_miss_sentinel_is_falsy_and_readable():
    assert not MISS
    assert "miss" in repr(MISS)
