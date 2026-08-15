"""Single source of truth for "now".

Centralised for two reasons: `datetime.utcnow()` is deprecated from Python 3.12,
and routing every timestamp through one function lets tests freeze time by
patching a single place.
"""

from datetime import datetime, timezone


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
