"""Seed the database with realistic demo inventory.

Demo data is deliberately spread across expiry horizons -- already expired, due
today, due this week, and long-dated -- and across categories, so urgency and
grouping behaviour are visible without hand-crafting rows before a walkthrough.
A separate history set is created and immediately disposed so waste analytics
has something to show without emptying the live fridge.

Categories are declared here rather than resolved through the cascade. A seed
script has to be deterministic and free, and running it must not depend on an
API key or spend credits on names the curated file does not cover.

Usage, from the backend/ directory:
    python -m scripts.seed            # add demo items
    python -m scripts.seed --reset    # wipe existing inventory first
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.models.base import Base
from app.models.inventory import Disposition, Expiration, InventoryItem
from app.services.disposition import apply_disposition


class DemoItem(NamedTuple):
    name: str
    quantity: float
    unit: str
    # None means the shelf life could not be resolved.
    days: int | None
    # Provenance values match the live cascade: dataset, llm, user, unknown.
    # The old "api" and "heuristic" labels are gone.
    source: str
    # None means no category could be established.
    category: str | None


class DemoOutcome(NamedTuple):
    name: str
    quantity: float
    unit: str
    days: int | None
    category: str | None
    outcome: str
    reason: str


DEMO_ITEMS: list[DemoItem] = [
    DemoItem("Amul Yogurt", 400, "g", -6, "dataset", "dairy"),
    DemoItem("Baby Spinach", 200, "g", -2, "dataset", "produce"),
    DemoItem("Whole Wheat Bread", 1, "count", 0, "dataset", "bakery"),
    DemoItem("Paneer", 200, "g", 1, "llm", "dairy"),
    DemoItem("Milk", 1, "l", 2, "dataset", "dairy"),
    DemoItem("Chicken Breast", 500, "g", 3, "dataset", "meat_seafood"),
    DemoItem("Tomatoes", 6, "count", 5, "dataset", "produce"),
    DemoItem("Cheddar Cheese", 250, "g", 9, "dataset", "dairy"),
    DemoItem("Eggs", 12, "count", 18, "dataset", "dairy"),
    DemoItem("Basmati Rice", 5, "kg", 240, "user", "grains_pulses"),
    DemoItem("Olive Oil", 1, "l", 300, "user", "pantry"),
    DemoItem("Salt", 1, "kg", None, "unknown", "spices_condiments"),
    DemoItem("Leftover Curry", 1, "count", None, "unknown", None),
]

# Created and fully disposed, so the fridge stays DEMO_ITEMS while analytics has
# a recent window of outcomes. Produce-heavy waste on purpose: that is what
# actually rots first, and the report should tell the true story.
DEMO_HISTORY: list[DemoOutcome] = [
    DemoOutcome("Lettuce", 1, "count", -3, "produce", "wasted", "went slimy"),
    DemoOutcome("Strawberries", 250, "g", -1, "produce", "wasted", "mould"),
    DemoOutcome("Coriander", 1, "count", -4, "produce", "wasted", "wilted"),
    DemoOutcome(
        "Greek Yogurt", 400, "g", 2, "dairy", "wasted", "didn't finish in time"
    ),
    DemoOutcome(
        "Mystery Sauce",
        1,
        "count",
        None,
        None,
        "wasted",
        "couldn't tell if it was still good",
    ),
    DemoOutcome("Eggs", 6, "count", 10, "dairy", "consumed", "omelette"),
    DemoOutcome("Milk", 1, "l", 3, "dairy", "consumed", "cereal"),
    DemoOutcome("Tomatoes", 4, "count", 4, "produce", "consumed", "dal"),
]


def _add_item(
    session: Session,
    name: str,
    quantity: float,
    unit: str,
    offset: int | None,
    source: str,
    category: str | None,
) -> InventoryItem:
    item = InventoryItem(
        name=name,
        quantity=quantity,
        unit=unit,
        category=category,
        category_source="dataset" if category is not None else "unknown",
    )
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

        for entry in DEMO_ITEMS:
            _add_item(
                session,
                entry.name,
                entry.quantity,
                entry.unit,
                entry.days,
                entry.source,
                entry.category,
            )

        for outcome in DEMO_HISTORY:
            item = _add_item(
                session,
                outcome.name,
                outcome.quantity,
                outcome.unit,
                outcome.days,
                "user",
                outcome.category,
            )
            apply_disposition(
                session, item, outcome=outcome.outcome, reason=outcome.reason
            )

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
