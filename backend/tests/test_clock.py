"""Tests for the time helpers.

Timestamps flow into stored records, cache expiry, and upload filenames, so the
two spellings need to be pinned: one naive-UTC value for storage, and one true
POSIX timestamp.
"""

import time
from datetime import datetime, timezone

from app.core.clock import epoch_seconds, utcnow


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
