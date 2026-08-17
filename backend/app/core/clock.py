"""Single source of truth for "now".

Centralised for two reasons: `datetime.utcnow()` is deprecated from Python 3.12,
and routing every timestamp through one function lets tests freeze time by
patching a single place.
"""

from datetime import date, datetime, timezone


def today(tz_name: str | None = None) -> date:
    """Today's date in `tz_name`, or UTC when none is given.

    Exists because the codebase had two definitions of "today" and used both in the
    same subtraction. Timestamps came from `utcnow()`, but every "is this expired?"
    calculation called `date.today()`, which is local. Between 20:00 and midnight in
    UTC-4 those disagree by a day, so an item given a shelf life of three days ago
    was recorded as having expired four days ago -- and since the waste report splits
    on `days_remaining < 0`, an item thrown out on its expiry date could be counted
    as wasted after expiry instead of before.

    UTC is the fallback so stored timestamps and date arithmetic stay internally
    consistent when no user is in scope. When a user is, their timezone is the
    calendar they actually live on.
    """
    if tz_name:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).date()
    return datetime.now(timezone.utc).date()


def utcnow() -> datetime:
    """Current UTC time, without a tzinfo attached.

    Naive-UTC is stored rather than aware datetimes because SQLite's DateTime
    column discards tzinfo on the way out; keeping every stored value naive-UTC
    avoids comparing an aware value against a naive one later.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def epoch_seconds() -> float:
    """Seconds since the Unix epoch.

    Not `utcnow().timestamp()`: `.timestamp()` interprets a naive datetime as
    local time, so that spelling is silently wrong by the local UTC offset.
    """
    return datetime.now(timezone.utc).timestamp()
