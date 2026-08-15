"""Tests for the learned shelf-life store.

This is the second source of truth alongside the curated file. What matters is
that learned values are kept distinct from curated ones, that each entry records
what it was derived from, and that a human can inspect and correct it.
"""

import pytest

from app.models.shelf_life import LearnedShelfLife
from app.services.learned_store import (
    LearnedEntry,
    LearnedShelfLifeStore,
    get_learned_store,
    reset_learned_store,
)


@pytest.fixture
def store(db):
    return LearnedShelfLifeStore(session_factory=lambda: db)


class TestRememberAndGet:
    def test_unknown_name_returns_none(self, store):
        assert store.get("paneer") is None

    def test_remembered_value_is_returned(self, store):
        store.remember("paneer", days=12)
        entry = store.get("paneer")
        assert entry.name == "paneer"
        assert entry.days == 12

    def test_anchor_is_persisted(self, store):
        """The anchor is what makes an entry reviewable."""
        store.remember("baby spinach", days=4, anchor="spinach", anchor_days=4)
        entry = store.get("baby spinach")
        assert entry.anchor == "spinach"
        assert entry.anchor_days == 4
        assert entry.is_anchored

    def test_unanchored_entry_is_marked_as_such(self, store):
        store.remember("saffron", days=365)
        entry = store.get("saffron")
        assert entry.anchor is None
        assert not entry.is_anchored

    def test_model_is_recorded(self, store):
        store.remember("paneer", days=12, model="gpt-4o-mini")
        assert store.get("paneer").model == "gpt-4o-mini"

    def test_remembering_twice_replaces(self, store, db):
        store.remember("paneer", days=12)
        store.remember("paneer", days=20)
        assert store.get("paneer").days == 20
        assert db.query(LearnedShelfLife).count() == 1

    def test_entries_are_detached_snapshots(self, store):
        """Callers get a frozen value object, never a live ORM row."""
        store.remember("paneer", days=12)
        entry = store.get("paneer")
        assert isinstance(entry, LearnedEntry)
        with pytest.raises(Exception):
            entry.days = 99


class TestConfirmation:
    def test_new_entries_start_unconfirmed(self, store):
        store.remember("paneer", days=12)
        assert store.get("paneer").confirmed is False

    def test_confirm_marks_the_entry(self, store):
        store.remember("paneer", days=12)
        assert store.confirm("paneer") is True
        assert store.get("paneer").confirmed is True

    def test_confirming_an_unknown_name_reports_failure(self, store):
        assert store.confirm("nope") is False

    def test_pending_lists_only_unconfirmed(self, store):
        store.remember("paneer", days=12)
        store.remember("tofu", days=9)
        store.confirm("paneer")
        assert [entry.name for entry in store.pending()] == ["tofu"]

    def test_pending_is_sorted_by_name(self, store):
        for name in ["tofu", "apple", "paneer"]:
            store.remember(name, days=5)
        assert [entry.name for entry in store.pending()] == [
            "apple",
            "paneer",
            "tofu",
        ]


class TestForget:
    def test_forget_removes_the_entry(self, store):
        store.remember("coconut milk", days=5)
        assert store.forget("coconut milk") is True
        assert store.get("coconut milk") is None

    def test_forgetting_an_unknown_name_reports_failure(self, store):
        assert store.forget("nope") is False

    def test_a_bad_entry_can_be_corrected_by_replacing_it(self, store):
        """Correctability is the point of a store rather than an opaque cache."""
        store.remember("coconut milk", days=5, anchor="milk", anchor_days=5)
        store.remember("coconut milk", days=730)
        entry = store.get("coconut milk")
        assert entry.days == 730


class TestStaleness:
    def test_entry_is_stale_when_its_anchor_value_changed(self, store):
        """The reason anchor_days is stored at all."""
        store.remember("baby spinach", days=4, anchor="spinach", anchor_days=4)
        stale = store.stale(curated={"spinach": 6})
        assert [entry.name for entry in stale] == ["baby spinach"]

    def test_entry_is_not_stale_when_its_anchor_is_unchanged(self, store):
        store.remember("baby spinach", days=4, anchor="spinach", anchor_days=4)
        assert store.stale(curated={"spinach": 4}) == []

    def test_unanchored_entries_are_never_stale(self, store):
        """Nothing was derived from, so nothing can drift."""
        store.remember("saffron", days=365)
        assert store.stale(curated={"spinach": 6}) == []

    def test_anchor_missing_from_the_curated_file_is_not_stale(self, store):
        """It may have been learned rather than curated; absence is not a change."""
        store.remember("silken tofu", days=9, anchor="tofu", anchor_days=9)
        assert store.stale(curated={"spinach": 4}) == []


class TestBulkAccess:
    def test_all_returns_every_entry_sorted(self, store):
        for name in ["tofu", "apple"]:
            store.remember(name, days=5)
        assert [entry.name for entry in store.all()] == ["apple", "tofu"]

    def test_all_is_empty_initially(self, store):
        assert store.all() == []

    def test_clear_removes_everything(self, store):
        store.remember("paneer", days=12)
        store.clear()
        assert store.all() == []


class TestModuleSingleton:
    def test_get_learned_store_is_a_singleton(self):
        reset_learned_store()
        try:
            assert get_learned_store() is get_learned_store()
        finally:
            reset_learned_store()

    def test_reset_forces_a_rebuild(self):
        reset_learned_store()
        try:
            first = get_learned_store()
            reset_learned_store()
            assert get_learned_store() is not first
        finally:
            reset_learned_store()

    def test_falls_back_to_the_configured_session_factory(self):
        from app.db.session import SessionLocal

        assert LearnedShelfLifeStore()._factory() is SessionLocal
