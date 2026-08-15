"""Urgency classification for expiry dates.

Computed on the server rather than in the browser for three reasons: the rule is
domain logic and belongs with the domain, every client gets the same answer, and
it can be tested without a browser.

The buckets are deliberately coarse. A user deciding what to cook tonight needs to
know "this is already gone" versus "use this today" versus "this can wait", not an
exact day count -- though the day count is returned too, for sorting and copy.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

# Boundaries, in days from today.
SOON_THRESHOLD = 3
THIS_WEEK_THRESHOLD = 7


class Urgency(StrEnum):
    EXPIRED = "expired"
    TODAY = "today"
    SOON = "soon"
    THIS_WEEK = "this_week"
    LATER = "later"
    UNKNOWN = "unknown"


# Ordered most urgent first, so callers can sort or iterate without re-deriving it.
URGENCY_ORDER: tuple[Urgency, ...] = (
    Urgency.EXPIRED,
    Urgency.TODAY,
    Urgency.SOON,
    Urgency.THIS_WEEK,
    Urgency.LATER,
    Urgency.UNKNOWN,
)

# Whether an item in this bucket needs the user to do something now.
ACTIONABLE: frozenset[Urgency] = frozenset(
    {Urgency.EXPIRED, Urgency.TODAY, Urgency.SOON}
)


def days_until(expiration_date: date | None, today: date | None = None) -> int | None:
    """Days remaining, negative if already past. None when there is no date."""
    if expiration_date is None:
        return None
    return (expiration_date - (today or date.today())).days


def classify(expiration_date: date | None, today: date | None = None) -> Urgency:
    remaining = days_until(expiration_date, today)
    if remaining is None:
        # No date is genuinely unknown, not "safe". It must not be presented as
        # fresh, because nothing was ever established about it.
        return Urgency.UNKNOWN
    if remaining < 0:
        return Urgency.EXPIRED
    if remaining == 0:
        return Urgency.TODAY
    if remaining <= SOON_THRESHOLD:
        return Urgency.SOON
    if remaining <= THIS_WEEK_THRESHOLD:
        return Urgency.THIS_WEEK
    return Urgency.LATER


def sort_key(expiration_date: date | None, today: date | None = None) -> tuple:
    """Sort key placing the most urgent first and undated items last."""
    remaining = days_until(expiration_date, today)
    if remaining is None:
        return (1, 0)
    return (0, remaining)


def is_actionable(urgency: Urgency) -> bool:
    return urgency in ACTIONABLE
