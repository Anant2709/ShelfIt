"""Seed the database with realistic demo inventory.

Demo data is deliberately spread across expiry horizons -- already expired, due
today, due this week, and long-dated -- so urgency behaviour is visible without
hand-crafting rows before a walkthrough.

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
from app.models.inventory import Expiration, InventoryItem

# (name, quantity, unit, days_until_expiry, provenance)
# days_until_expiry of None means the shelf life could not be resolved.
DEMO_ITEMS: list[tuple[str, float, str, int | None, str]] = [
    ("Amul Yogurt", 400, "g", -6, "dataset"),
    ("Baby Spinach", 200, "g", -2, "heuristic"),
    ("Whole Wheat Bread", 1, "count", 0, "dataset"),
    ("Paneer", 200, "g", 1, "api"),
    ("Milk", 1, "l", 2, "dataset"),
    ("Chicken Breast", 500, "g", 3, "heuristic"),
    ("Tomatoes", 6, "count", 5, "dataset"),
    ("Cheddar Cheese", 250, "g", 9, "dataset"),
    ("Eggs", 12, "count", 18, "dataset"),
    ("Basmati Rice", 5, "kg", 240, "user"),
    ("Olive Oil", 1, "l", 300, "user"),
    ("Salt", 1, "kg", None, "unknown"),
]


def seed(reset: bool = False, session: Session | None = None) -> None:
    """Insert the demo items.

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
            session.query(Expiration).delete()
            session.query(InventoryItem).delete()
            session.commit()
            print(f"Removed {deleted} existing item(s).")

        for name, quantity, unit, offset, source in DEMO_ITEMS:
            item = InventoryItem(name=name, quantity=quantity, unit=unit)
            session.add(item)
            session.flush()
            session.add(
                Expiration(
                    item_id=item.id,
                    expiration_date=(
                        date.today() + timedelta(days=offset)
                        if offset is not None
                        else None
                    ),
                    source=source,
                    shelf_life_days=offset if offset and offset > 0 else None,
                )
            )
        session.commit()
        print(f"Seeded {len(DEMO_ITEMS)} item(s).")
        print(f"Inventory now holds {session.query(InventoryItem).count()} item(s).")
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
