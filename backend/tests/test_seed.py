"""Tests for the demo seed script.

The seed script backs the demo walkthrough, so it needs to be reliable: it must
produce a spread of expiry horizons and must be safely re-runnable.
"""

from datetime import date

from app.models.inventory import Expiration, InventoryItem
from scripts.seed import DEMO_ITEMS, main, seed


def counts(session):
    return session.query(InventoryItem).count(), session.query(Expiration).count()


def test_seed_populates_inventory(db):
    seed(session=db)
    items, expirations = counts(db)
    assert items == len(DEMO_ITEMS)
    assert expirations == len(DEMO_ITEMS)


def test_seed_without_reset_appends(db):
    seed(session=db)
    seed(session=db)
    items, _ = counts(db)
    assert items == 2 * len(DEMO_ITEMS)


def test_seed_with_reset_replaces(db):
    seed(session=db)
    seed(reset=True, session=db)
    items, expirations = counts(db)
    assert items == len(DEMO_ITEMS)
    assert expirations == len(DEMO_ITEMS), "reset must not orphan expiration rows"


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
    assert unresolved[0].source == "unknown"


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
            return type("Q", (), {"count": staticmethod(lambda: 0)})()

        def close(self):
            self.closed = True

    spy = SpySession()
    monkeypatch.setattr(seed_module, "SessionLocal", lambda: spy)
    monkeypatch.setattr(seed_module.Base.metadata, "create_all", lambda **_: None)
    monkeypatch.setattr(seed_module, "DEMO_ITEMS", [])
    monkeypatch.setattr(
        seed_module, "print", lambda *a, **k: None, raising=False
    )
    seed_module.seed()
    assert spy.closed is True, "script-owned session must be closed"


def test_every_demo_item_declares_a_provenance():
    valid = {"user", "dataset", "api", "heuristic", "unknown"}
    for name, _, _, _, source in DEMO_ITEMS:
        assert source in valid, f"{name} has unrecognised provenance {source!r}"
