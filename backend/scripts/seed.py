"""Seed the database with realistic demo inventory.

Demo data is deliberately spread across expiry horizons -- already expired, due
today, due this week, and long-dated -- so urgency behaviour is visible without
hand-crafting rows before a walkthrough. A separate history set is created and
immediately disposed so waste analytics has something to show without emptying
the live fridge.

Usage, from the backend/ directory:
    python -m scripts.seed            # add demo items
    python -m scripts.seed --reset    # wipe existing inventory first
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.models.base import Base
from app.models.inventory import Disposition, Expiration, InventoryItem
from app.services.disposition import apply_disposition

# (name, quantity, unit, days_until_expiry, provenance)
# days_until_expiry of None means the shelf life could not be resolved.
# Provenance values match the live cascade: dataset, llm, user, unknown.
# The old "api" and "heuristic" labels are gone.
DEMO_ITEMS: list[tuple[str, float, str, int | None, str]] = [
    ("Amul Yogurt", 400, "g", -6, "dataset"),
    ("Baby Spinach", 200, "g", -2, "dataset"),
    ("Whole Wheat Bread", 1, "count", 0, "dataset"),
    ("Paneer", 200, "g", 1, "llm"),
    ("Milk", 1, "l", 2, "dataset"),
    ("Chicken Breast", 500, "g", 3, "dataset"),
    ("Tomatoes", 6, "count", 5, "dataset"),
    ("Cheddar Cheese", 250, "g", 9, "dataset"),
    ("Eggs", 12, "count", 18, "dataset"),
    ("Basmati Rice", 5, "kg", 240, "user"),
    ("Olive Oil", 1, "l", 300, "user"),
    ("Salt", 1, "kg", None, "unknown"),
]

# (name, quantity, unit, days_until_expiry, outcome, reason)
# Created and fully disposed so the fridge stays DEMO_ITEMS while analytics
# has a recent window of consume/waste events.
DEMO_HISTORY: list[tuple[str, float, str, int | None, str, str]] = [
    ("Lettuce", 1, "count", -3, "wasted", "went slimy"),
    ("Strawberries", 250, "g", -1, "wasted", "mould"),
    ("Coriander", 1, "count", -4, "wasted", "wilted"),
    ("Greek Yogurt", 400, "g", 2, "wasted", "didn't finish in time"),
    ("Mystery Sauce", 1, "count", None, "wasted", "couldn't tell if it was still good"),
    ("Eggs", 6, "count", 10, "consumed", "omelette"),
    ("Milk", 1, "l", 3, "consumed", "cereal"),
    ("Tomatoes", 4, "count", 4, "consumed", "dal"),
]


def _add_item(
    session: Session,
    name: str,
    quantity: float,
    unit: str,
    offset: int | None,
    source: str,
) -> InventoryItem:
    item = InventoryItem(name=name, quantity=quantity, unit=unit)
    session.add(item)
    session.flush()
    session.add(
        Expiration(
            item_id=item.id,
            expiration_date=(
                date.today() + timedelta(days=offset) if offset is not None else None
            ),
            source=source,
            shelf_life_days=offset if offset and offset > 0 else None,
        )
    )
    session.flush()
    return item


def seed(reset: bool = False, session: Session | None = None) -> None:
    """Insert the demo items and a window of consume/waste history.

    A session may be supplied so callers -- notably the tests -- can seed into a
    database of their choosing instead of the configured one.
    """
    owns_session = session is None
    if owns_session:
        Base.metadata.create_all(bind=engine)
        session = SessionLocal()
    try:
        if reset:
            deleted = session.query(InventoryItem).count()
            # Bulk deletes skip ORM cascades, so dependents go first.
            session.query(Disposition).delete()
            session.query(Expiration).delete()
            session.query(InventoryItem).delete()
            session.commit()
            print(f"Removed {deleted} existing item(s).")

        for name, quantity, unit, offset, source in DEMO_ITEMS:
            _add_item(session, name, quantity, unit, offset, source)

        for name, quantity, unit, offset, outcome, reason in DEMO_HISTORY:
            item = _add_item(session, name, quantity, unit, offset, "user")
            apply_disposition(session, item, outcome=outcome, reason=reason)

        session.commit()
        active = (
            session.query(InventoryItem)
            .filter(InventoryItem.resolved_at.is_(None))
            .count()
        )
        print(
            f"Seeded {len(DEMO_ITEMS)} live item(s) and "
            f"{len(DEMO_HISTORY)} historical outcome(s)."
        )
        print(f"Live inventory now holds {active} item(s).")
    finally:
        if owns_session:
            session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete all existing inventory before seeding",
    )
    seed(reset=parser.parse_args().reset)


if __name__ == "__main__":
    main()
