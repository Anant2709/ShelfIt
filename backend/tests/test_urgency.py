"""Tests for urgency classification.

A pure function over dates, so every boundary is asserted directly. `today` is
always passed explicitly -- a test that depends on the real clock would pass or
fail based on when it ran.
"""

from datetime import date, timedelta

from app.core import clock

import pytest

from app.services.urgency import (
    ACTIONABLE,
    SOON_THRESHOLD,
    THIS_WEEK_THRESHOLD,
    URGENCY_ORDER,
    Urgency,
    classify,
    days_until,
    is_actionable,
    sort_key,
)

TODAY = date(2026, 8, 14)


def offset(days: int) -> date:
    return TODAY + timedelta(days=days)


class TestDaysUntil:
    @pytest.mark.parametrize("days", [-200, -1, 0, 1, 7, 365])
    def test_returns_the_signed_difference(self, days):
        assert days_until(offset(days), today=TODAY) == days

    def test_no_date_yields_none(self):
        assert days_until(None, today=TODAY) is None

    def test_defaults_to_the_real_today(self):
        assert days_until(clock.today()) == 0


class TestClassify:
    def test_no_date_is_unknown(self):
        """Absent is not the same as safe: nothing was ever established."""
        assert classify(None, today=TODAY) is Urgency.UNKNOWN

    @pytest.mark.parametrize("days", [-365, -30, -2, -1])
    def test_past_dates_are_expired(self, days):
        assert classify(offset(days), today=TODAY) is Urgency.EXPIRED

    def test_today_is_its_own_bucket(self):
        assert classify(TODAY, today=TODAY) is Urgency.TODAY

    @pytest.mark.parametrize("days", [1, 2, 3])
    def test_next_three_days_are_soon(self, days):
        assert classify(offset(days), today=TODAY) is Urgency.SOON

    @pytest.mark.parametrize("days", [4, 5, 6, 7])
    def test_rest_of_the_week(self, days):
        assert classify(offset(days), today=TODAY) is Urgency.THIS_WEEK

    @pytest.mark.parametrize("days", [8, 30, 365])
    def test_beyond_a_week_is_later(self, days):
        assert classify(offset(days), today=TODAY) is Urgency.LATER

    def test_boundaries_are_exact(self):
        """Guards off-by-one errors at every bucket edge."""
        assert classify(offset(-1), today=TODAY) is Urgency.EXPIRED
        assert classify(offset(0), today=TODAY) is Urgency.TODAY
        assert classify(offset(1), today=TODAY) is Urgency.SOON
        assert classify(offset(SOON_THRESHOLD), today=TODAY) is Urgency.SOON
        assert classify(offset(SOON_THRESHOLD + 1), today=TODAY) is Urgency.THIS_WEEK
        assert classify(offset(THIS_WEEK_THRESHOLD), today=TODAY) is Urgency.THIS_WEEK
        assert classify(offset(THIS_WEEK_THRESHOLD + 1), today=TODAY) is Urgency.LATER

    def test_expired_and_upcoming_are_distinguishable(self):
        """The whole point: these two used to be indistinguishable."""
        assert classify(offset(-200), today=TODAY) != classify(offset(1), today=TODAY)


class TestSerialisation:
    def test_urgency_is_a_string_in_json(self):
        """Serialised into API responses, so it must render as a plain string."""
        assert Urgency.EXPIRED == "expired"
        assert f"{Urgency.SOON}" == "soon"

    def test_every_bucket_appears_in_the_order(self):
        assert set(URGENCY_ORDER) == set(Urgency)

    def test_order_runs_most_urgent_first(self):
        assert URGENCY_ORDER[0] is Urgency.EXPIRED
        assert URGENCY_ORDER[-1] is Urgency.UNKNOWN


class TestActionable:
    @pytest.mark.parametrize(
        "urgency", [Urgency.EXPIRED, Urgency.TODAY, Urgency.SOON]
    )
    def test_urgent_buckets_require_action(self, urgency):
        assert is_actionable(urgency)

    @pytest.mark.parametrize(
        "urgency", [Urgency.THIS_WEEK, Urgency.LATER, Urgency.UNKNOWN]
    )
    def test_other_buckets_do_not(self, urgency):
        assert not is_actionable(urgency)

    def test_actionable_set_is_immutable(self):
        assert isinstance(ACTIONABLE, frozenset)


class TestSortKey:
    def test_sorts_most_urgent_first(self):
        dates = [offset(5), offset(-3), offset(0), offset(100)]
        ordered = sorted(dates, key=lambda d: sort_key(d, today=TODAY))
        assert ordered == [offset(-3), offset(0), offset(5), offset(100)]

    def test_undated_items_sort_last(self):
        """An item with no date cannot be prioritised, so it goes to the end."""
        dates = [None, offset(100), offset(-1)]
        ordered = sorted(dates, key=lambda d: sort_key(d, today=TODAY))
        assert ordered == [offset(-1), offset(100), None]
