"""Adding an item, and resolving what the app can infer about it.

This lived inside the inventory endpoint until the assistant gained the ability to
add items through a tool call. Two callers needed the same behaviour, and an
endpoint module is the wrong place for a service to import from -- so the domain
logic moved here and the endpoint became a caller like any other.

Keeping it in one place is what guarantees an item the assistant adds is resolved
exactly like one a person typed: same category cascade, same shelf-life cascade,
same provenance. A second implementation would drift.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.core import clock

from sqlalchemy.orm import Session

from app.models.inventory import Expiration, InventoryItem
from app.services.category import Category, lookup_category
from app.services.shelf_life import lookup_shelf_life_days


def assign_user_category(item: InventoryItem, value: Category | None) -> None:
    """Record a category the user stated, including an explicit "unknown".

    UNKNOWN is stored as NULL so there is one representation of "no category" in
    the database, but the source is still `user`, which stops inference from
    later overriding a deliberate answer.
    """
    item.category = None if value in (None, Category.UNKNOWN) else value.value
    item.category_source = "user"


def infer_category(item: InventoryItem) -> None:
    category, source = lookup_category(item.name)
    item.category = category.value if category is not None else None
    item.category_source = source


def ensure_expiration(
    db: Session, item: InventoryItem, expiration_date: date | None
) -> None:
    """Attach an expiration row, inferring the date when none was supplied."""
    if expiration_date:
        db.add(
            Expiration(
                item_id=item.id,
                expiration_date=expiration_date,
                source="user",
                shelf_life_days=None,
            )
        )
        db.commit()
        return

    shelf_life_days, source = lookup_shelf_life_days(item.name)
    resolved = None
    if shelf_life_days:
        resolved = clock.today() + timedelta(days=shelf_life_days)
    db.add(
        Expiration(
            item_id=item.id,
            expiration_date=resolved,
            source=source,
            shelf_life_days=shelf_life_days,
        )
    )
    db.commit()


def create_item(
    db: Session,
    name: str,
    quantity: float = 1.0,
    unit: str = "count",
    expiration_date: date | None = None,
    category: Category | None = None,
    user_id: str | None = None,
) -> InventoryItem:
    """Add an item, resolving its category and expiry through the cascades."""
    item = InventoryItem(name=name, quantity=quantity, unit=unit, user_id=user_id)
    if category is not None:
        assign_user_category(item, category)
    else:
        infer_category(item)
    db.add(item)
    db.commit()
    db.refresh(item)
    ensure_expiration(db, item, expiration_date)
    db.refresh(item)
    return item
