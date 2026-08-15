"""Tests for the demo seed script.

The seed script backs the demo walkthrough, so it needs to be reliable: it must
produce a spread of expiry horizons and must be safely re-runnable.
"""

from datetime import date

from app.models.inventory import Disposition, Expiration, InventoryItem
from app.services.category import ASSIGNABLE
from scripts.seed import DEMO_HISTORY, DEMO_ITEMS, main, seed


def counts(session):
    return session.query(InventoryItem).count(), session.query(Expiration).count()


def live_and_history():
    return len(DEMO_ITEMS) + len(DEMO_HISTORY)


def test_seed_populates_inventory(db):
    seed(session=db)
    items, expirations = counts(db)
    assert items == live_and_history()
    assert expirations == live_and_history()
    assert db.query(Disposition).count() == len(DEMO_HISTORY)


def test_seed_without_reset_appends(db):
    seed(session=db)
    seed(session=db)
    items, _ = counts(db)
    assert items == 2 * live_and_history()


def test_seed_with_reset_replaces(db):
    seed(session=db)
    seed(reset=True, session=db)
    items, expirations = counts(db)
    assert items == live_and_history()
    assert expirations == live_and_history(), "reset must not orphan expiration rows"
    assert db.query(Disposition).count() == len(DEMO_HISTORY), (
        "reset must not orphan disposition rows"
    )


def test_demo_data_spans_every_urgency_horizon(db):
    """Guards the property the demo depends on."""
    seed(reset=True, session=db)
    offsets = [
        (row.expiration_date - date.today()).days
        for row in db.query(Expiration).all()
        if row.expiration_date is not None
    ]
    assert any(offset < 0 for offset in offsets), "no already-expired item"
    assert any(offset == 0 for offset in offsets), "nothing expiring today"
    assert any(0 < offset <= 7 for offset in offsets), "nothing expiring this week"
    assert any(offset > 30 for offset in offsets), "nothing long-dated"


def test_unresolved_shelf_life_is_represented(db):
    """At least one item must exercise the 'no date could be inferred' path."""
    seed(reset=True, session=db)
    unresolved = db.query(Expiration).filter(Expiration.expiration_date.is_(None)).all()
    assert len(unresolved) >= 1
    assert any(row.source == "unknown" for row in unresolved)


def test_cli_uses_the_configured_database(monkeypatch):
    """main() drives seed() with its own session, not an injected one."""
    calls = []
    monkeypatch.setattr("scripts.seed.seed", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("sys.argv", ["seed"])
    main()
    assert calls == [{"reset": False}]


def test_cli_reset_flag_is_forwarded(monkeypatch):
    calls = []
    monkeypatch.setattr("scripts.seed.seed", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("sys.argv", ["seed", "--reset"])
    main()
    assert calls == [{"reset": True}]


def test_seed_manages_its_own_session_when_none_is_given(monkeypatch):
    """Without an injected session the script opens and closes its own."""
    import scripts.seed as seed_module

    class SpySession:
        def __init__(self):
            self.closed = False

        def add(self, *_):
            pass

        def flush(self):
            pass

        def commit(self):
            pass

        def query(self, *_):
            class Q:
                def filter(self, *a, **k):
                    return self

                def delete(self):
                    return 0

                def count(self):
                    return 0

            return Q()

        def close(self):
            self.closed = True

    spy = SpySession()
    monkeypatch.setattr(seed_module, "SessionLocal", lambda: spy)
    monkeypatch.setattr(seed_module.Base.metadata, "create_all", lambda **_: None)
    monkeypatch.setattr(seed_module, "DEMO_ITEMS", [])
    monkeypatch.setattr(seed_module, "DEMO_HISTORY", [])
    monkeypatch.setattr(
        seed_module, "print", lambda *a, **k: None, raising=False
    )
    seed_module.seed()
    assert spy.closed is True, "script-owned session must be closed"


def test_every_demo_item_declares_a_provenance():
    valid = {"user", "dataset", "llm", "unknown"}
    for entry in DEMO_ITEMS:
        assert entry.source in valid, (
            f"{entry.name} has unrecognised provenance {entry.source!r}"
        )
    assert "api" not in valid
    assert "heuristic" not in valid


def test_every_demo_category_is_a_real_category():
    """A typo here would create a category no filter could ever select."""
    for entry in list(DEMO_ITEMS) + list(DEMO_HISTORY):
        if entry.category is not None:
            assert entry.category in ASSIGNABLE, (
                f"{entry.name} has unrecognised category {entry.category!r}"
            )


def test_demo_data_covers_the_uncategorised_case(db):
    """The list and the report both have to render an item with no category."""
    seed(reset=True, session=db)
    assert (
        db.query(InventoryItem).filter(InventoryItem.category.is_(None)).count() >= 1
    )
    assert (
        db.query(Disposition).filter(Disposition.item_category.is_(None)).count() >= 1
    )


def test_history_items_are_resolved_and_live_items_are_not(db):
    seed(reset=True, session=db)
    live = {
        item.name
        for item in db.query(InventoryItem).filter(
            InventoryItem.resolved_at.is_(None)
        )
    }
    resolved = {
        item.name
        for item in db.query(InventoryItem).filter(
            InventoryItem.resolved_at.isnot(None)
        )
    }
    assert live == {entry.name for entry in DEMO_ITEMS}
    assert resolved == {entry.name for entry in DEMO_HISTORY}
