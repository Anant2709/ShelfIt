"""Tests for the time helpers.

Timestamps flow into stored records, cache expiry, and upload filenames, so the
two spellings need to be pinned: one naive-UTC value for storage, and one true
POSIX timestamp.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.clock import epoch_seconds, today, utcnow

APP_DIR = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def timezone_far_from_utc():
    """Run a test where the local date is definitely not UTC's date.

    The original bug only appeared for four hours a day, in one timezone, which is
    exactly why it survived a full test suite. Picking a fixed offset would recreate
    that: it would agree with UTC for part of the day and the test would quietly stop
    testing anything. So the offset is chosen from the current UTC hour -- UTC-11
    before midday, UTC+14 after -- which puts the local date a day either side of UTC
    whatever time the suite runs.
    """
    ahead = datetime.now(timezone.utc).hour >= 11
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Pacific/Kiritimati" if ahead else "Pacific/Midway"
    time.tzset()
    assert datetime.now().date() != datetime.now(timezone.utc).date(), (
        "the fixture failed to produce a local date that differs from UTC"
    )
    yield
    if original is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = original
    time.tzset()


class TestUtcnow:
    def test_returns_a_naive_datetime(self):
        """Stored values are naive-UTC, so they never mix with aware values."""
        assert utcnow().tzinfo is None

    def test_tracks_utc_not_local_time(self):
        expected = datetime.now(timezone.utc).replace(tzinfo=None)
        assert abs((utcnow() - expected).total_seconds()) < 5

    def test_advances(self):
        first = utcnow()
        time.sleep(0.01)
        assert utcnow() >= first


class TestEpochSeconds:
    def test_matches_the_real_posix_clock(self):
        assert abs(epoch_seconds() - time.time()) < 5

    def test_is_not_the_naive_timestamp_spelling(self):
        """Regression guard for a subtle bug.

        `datetime.utcnow().timestamp()` treats a naive UTC value as local time,
        so it is wrong by the local UTC offset. In a non-UTC timezone the two
        spellings differ by hours; in UTC they agree, so this only asserts the
        helper matches the real clock.
        """
        naive_spelling = utcnow().timestamp()
        offset = time.timezone if time.daylight == 0 else time.altzone
        assert abs(epoch_seconds() - (naive_spelling - offset)) < 5

    def test_is_monotonic_enough_for_filenames(self):
        first = epoch_seconds()
        time.sleep(0.01)
        assert epoch_seconds() > first


class TestToday:
    """One definition of "today", because there used to be two.

    Timestamps came from `utcnow()` but every expiry calculation called
    `date.today()`, which is local. In UTC-4 after 20:00 those disagree by a day, so
    an item three days past its date was recorded as four days past -- and because
    the waste report splits on `days_remaining < 0`, something thrown out on its
    expiry date could be filed as wasted after expiry rather than before it.
    """

    def test_it_agrees_with_the_stored_timestamps(self):
        """The invariant: one date, derived the same way as everything stored."""
        assert today() == utcnow().date()

    def test_it_is_utc_even_where_local_is_a_different_date(
        self, timezone_far_from_utc
    ):
        assert today() == datetime.now(timezone.utc).date()

    def test_it_ignores_the_local_date(self, timezone_far_from_utc):
        """The assertion the old code would have failed."""
        assert today() != datetime.now().date()

    def test_the_invariant_holds_under_a_shifted_timezone(
        self, timezone_far_from_utc
    ):
        assert today() == utcnow().date()

    def test_a_named_timezone_uses_that_calendar(self):
        from zoneinfo import ZoneInfo

        assert today("America/New_York") == datetime.now(
            ZoneInfo("America/New_York")
        ).date()
        assert today("UTC") == datetime.now(timezone.utc).date()

    def test_opposite_sides_of_the_date_line_can_disagree(self):
        """Proof the helper is not ignoring the zone and always returning UTC."""
        from zoneinfo import ZoneInfo

        east = today("Pacific/Kiritimati")
        west = today("Pacific/Pago_Pago")
        real_east = datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
        real_west = datetime.now(ZoneInfo("Pacific/Pago_Pago")).date()
        assert east == real_east
        assert west == real_west
        assert (east != west) == (real_east != real_west)


class TestNothingBypassesTheClock:
    """A source check, because the bug was a call site rather than a behaviour.

    `date.today()` is locally correct-looking everywhere it appears, which is why it
    spread across five modules unnoticed. Nothing in the arithmetic can detect it;
    only agreement between call sites can, so this asserts the call sites.
    """

    def test_no_application_module_calls_date_today_directly(self):
        offenders = []
        for path in sorted(APP_DIR.rglob("*.py")):
            # clock.py names it to explain why nothing else should call it.
            if path.name == "clock.py":
                continue
            body = path.read_text(encoding="utf-8")
            for number, line in enumerate(body.splitlines(), start=1):
                if "date.today()" in line and "clock.today()" not in line:
                    offenders.append(f"{path.relative_to(APP_DIR)}:{number}")
        assert offenders == [], (
            "these call sites use the local date instead of app.core.clock.today: "
            + ", ".join(offenders)
        )

    def test_no_application_module_calls_datetime_now_directly(self):
        """`datetime.now()` is the same mistake with a timestamp attached."""
        offenders = []
        for path in sorted(APP_DIR.rglob("*.py")):
            if path.name == "clock.py":
                continue
            body = path.read_text(encoding="utf-8")
            for number, line in enumerate(body.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "datetime.now()" in stripped:
                    offenders.append(f"{path.relative_to(APP_DIR)}:{number}")
        assert offenders == [], (
            "these call sites use local time instead of app.core.clock: "
            + ", ".join(offenders)
        )


class TestExpiryArithmeticIsTimezoneIndependent:
    """The consequence the invariant exists to protect.

    Shelf life resolves to a date, and later something subtracts today's date from
    it. If those two steps disagree about what day it is, the answer is off by one
    for part of every day -- which is the difference between "expires today" and
    "expired yesterday".
    """

    def test_an_item_expiring_today_has_zero_days_left(self, timezone_far_from_utc):
        from app.services.urgency import Urgency, classify, days_until

        assert days_until(today()) == 0
        assert classify(today()) is Urgency.TODAY

    def test_a_date_resolved_from_shelf_life_reads_back_as_that_many_days(
        self, timezone_far_from_utc
    ):
        """Round-trips the two halves that used different clocks."""
        from app.services.urgency import days_until

        for shelf_life in (0, 1, 3, 7, 30):
            resolved = today() + timedelta(days=shelf_life)
            assert days_until(resolved) == shelf_life

    def test_an_expired_item_is_not_off_by_one(self, timezone_far_from_utc):
        """The exact failure: three days past its date, reported as four."""
        from app.services.urgency import days_until

        assert days_until(today() - timedelta(days=3)) == -3
