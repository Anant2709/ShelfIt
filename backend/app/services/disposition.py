"""Consume-or-waste: the event log, not a delete.

Deleting an item is a correction ("I added this by mistake"). Using it or
throwing it out is a real outcome, and the analytics that make this product
honest — how much was wasted, whether it had already expired — can only exist
if those outcomes are recorded.

Quantity on the inventory row remains "what is on the shelf now". This module
writes the event, reduces that quantity, and marks the row resolved when
nothing remains. Analytics read the events, never the live fridge.

Quantities are never summed across units. A litre of milk and 200g of spinach
are not a number, and inventing a rupee total would be the same kind of lie.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Literal

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models.inventory import Disposition, InventoryItem
from app.services.urgency import days_until

Outcome = Literal["consumed", "wasted"]

CONSUMED = "consumed"
WASTED = "wasted"
OUTCOMES = frozenset({CONSUMED, WASTED})

# Matches the create-item minimum. Leftover below this is treated as gone,
# because floating point would otherwise leave a ghost 0.0000001 of yogurt.
REMAINING_FLOOR = 0.01


class DispositionError(ValueError):
    """A client-correctable mistake while recording an outcome."""


class AlreadyResolvedError(DispositionError):
    """The item has already left the shelf; further outcomes are not possible."""


class ExcessQuantityError(DispositionError):
    """The requested amount is more than what is still on the shelf."""

    def __init__(self, requested: float, remaining: float) -> None:
        self.requested = requested
        self.remaining = remaining
        super().__init__(
            f"Cannot dispose {requested}: only {remaining} remains"
        )


def apply_disposition(
    db: Session,
    item: InventoryItem,
    outcome: str,
    quantity: float | None = None,
    reason: str | None = None,
    occurred_at: datetime | None = None,
) -> Disposition:
    """Record one consume or waste event and update remaining quantity.

    Omitting `quantity` disposes whatever is left. Does not commit: the caller
    owns the transaction, so a failed later step can still roll back.
    """
    if outcome not in OUTCOMES:
        raise DispositionError(f"Unknown outcome {outcome!r}")
    if item.resolved_at is not None:
        raise AlreadyResolvedError("Item has already been fully disposed")

    remaining = item.quantity
    amount = remaining if quantity is None else quantity
    if amount <= 0:
        raise DispositionError("Quantity must be positive")
    if amount > remaining + 1e-9:
        raise ExcessQuantityError(requested=amount, remaining=remaining)
    amount = min(amount, remaining)

    when = occurred_at or utcnow()
    expiration_date = (
        item.expiration.expiration_date if item.expiration else None
    )
    event = Disposition(
        item_id=item.id,
        outcome=outcome,
        quantity=amount,
        unit=item.unit,
        reason=reason,
        occurred_at=when,
        item_name=item.name,
        days_remaining=days_until(expiration_date, when.date()),
        expiration_date=expiration_date,
    )
    db.add(event)

    leftover = remaining - amount
    if leftover < REMAINING_FLOOR:
        item.quantity = 0.0
        item.resolved_at = when
    else:
        item.quantity = leftover
    db.add(item)
    db.flush()
    return event


@dataclass(frozen=True)
class OutcomeTotals:
    events: int
    items: int


@dataclass(frozen=True)
class NameBreakdown:
    name: str
    events: int
    quantity: float | None
    unit: str | None


@dataclass(frozen=True)
class WasteReport:
    window_days: int
    consumed: OutcomeTotals
    wasted: OutcomeTotals
    waste_rate: float
    wasted_after_expiry: int
    wasted_before_expiry: int
    wasted_undated: int
    by_name: tuple[NameBreakdown, ...]


def _totals(events: list[Disposition]) -> OutcomeTotals:
    return OutcomeTotals(
        events=len(events),
        items=len({event.item_id for event in events}),
    )


def _wasted_by_name(events: Iterable[Disposition]) -> tuple[NameBreakdown, ...]:
    grouped: dict[str, list[Disposition]] = defaultdict(list)
    for event in events:
        grouped[event.item_name].append(event)

    rows: list[NameBreakdown] = []
    for name, group in grouped.items():
        units = {event.unit for event in group}
        if len(units) == 1:
            unit = next(iter(units))
            quantity = sum(event.quantity for event in group)
        else:
            # Mixed units cannot be added without lying. Count the events,
            # decline the quantity.
            unit = None
            quantity = None
        rows.append(
            NameBreakdown(
                name=name,
                events=len(group),
                quantity=quantity,
                unit=unit,
            )
        )
    rows.sort(key=lambda row: (-row.events, row.name))
    return tuple(rows)


def summarise_waste(
    events: Iterable[Disposition],
    window_days: int,
) -> WasteReport:
    """Pure summary over a pre-filtered event list.

    Kept free of the session so the arithmetic can be tested with plain
    objects and the query stays a thin wrapper.
    """
    collected = list(events)
    consumed = [event for event in collected if event.outcome == CONSUMED]
    wasted = [event for event in collected if event.outcome == WASTED]
    total_events = len(consumed) + len(wasted)
    waste_rate = (len(wasted) / total_events) if total_events else 0.0

    after = 0
    before = 0
    undated = 0
    for event in wasted:
        if event.days_remaining is None:
            undated += 1
        elif event.days_remaining < 0:
            after += 1
        else:
            before += 1

    return WasteReport(
        window_days=window_days,
        consumed=_totals(consumed),
        wasted=_totals(wasted),
        waste_rate=waste_rate,
        wasted_after_expiry=after,
        wasted_before_expiry=before,
        wasted_undated=undated,
        by_name=_wasted_by_name(wasted),
    )


def waste_report(db: Session, window_days: int = 30) -> WasteReport:
    """Events in the trailing window, inclusive of now."""
    cutoff = utcnow() - timedelta(days=window_days)
    events = (
        db.query(Disposition)
        .filter(Disposition.occurred_at >= cutoff)
        .all()
    )
    return summarise_waste(events, window_days)
