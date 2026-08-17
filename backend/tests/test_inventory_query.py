"""Search, filter, and sort on the inventory list.

The riskiest part is the urgency filter: urgency is a computed label, but
filtering happens in SQL, so the rule exists in two forms. If those two forms
disagree, an item is shown with one badge and matched by another filter. The
consistency test below is the guard, and it is deliberately exhaustive rather
than illustrative.
"""

from datetime import date, timedelta

from app.core import clock

import pytest

from app.models.inventory import Expiration, InventoryItem
from app.services.urgency import (
    URGENCY_ORDER,
    Urgency,
    bucket_bounds,
    classify,
)


def add_item(
    db,
    name,
    days_from_today=5,
    category=None,
    quantity=1.0,
    unit="count",
    with_expiration=True,
):
    item = InventoryItem(
        name=name,
        quantity=quantity,
        unit=unit,
        category=category,
        category_source="dataset" if category else "unknown",
        user_id=db.info["user"].id,
    )
    db.add(item)
    db.flush()
    if with_expiration:
        db.add(
            Expiration(
                item_id=item.id,
                expiration_date=(
                    clock.today() + timedelta(days=days_from_today)
                    if days_from_today is not None
                    else None
                ),
                source="user",
            )
        )
    db.commit()
    db.refresh(item)
    return item


def names(client, **params):
    response = client.get("/api/inventory/", params=params)
    assert response.status_code == 200
    return [item["name"] for item in response.json()]


class TestBucketBoundsAgreeWithClassify:
    """The SQL filter and the displayed label must be the same rule."""

    @pytest.mark.parametrize("offset", range(-30, 31))
    def test_every_offset_falls_in_exactly_one_bucket(self, offset):
        today = date(2026, 8, 15)
        target = today + timedelta(days=offset)
        matching = []
        for bucket in URGENCY_ORDER:
            bounds = bucket_bounds(bucket, today)
            if bounds is None:
                continue
            low, high = bounds
            if (low is None or target >= low) and (high is None or target <= high):
                matching.append(bucket)
        assert matching == [classify(target, today)]

    @pytest.mark.parametrize("offset", [-400, -8, 100, 400])
    def test_far_offsets_also_agree(self, offset):
        today = date(2026, 8, 15)
        target = today + timedelta(days=offset)
        bucket = classify(target, today)
        low, high = bucket_bounds(bucket, today)
        assert low is None or target >= low
        assert high is None or target <= high

    def test_unknown_is_not_a_date_range(self):
        """It cannot be, so it must be matched as NULL instead of by bounds."""
        assert bucket_bounds(Urgency.UNKNOWN) is None

    def test_bounds_default_to_today(self):
        low, high = bucket_bounds(Urgency.TODAY)
        assert low == high == clock.today()


class TestSearch:
    def test_substring_match_is_case_insensitive(self, client, db):
        add_item(db, "Whole Wheat Bread")
        add_item(db, "Milk")
        assert names(client, search="wheat") == ["Whole Wheat Bread"]
        assert names(client, search="WHEAT") == ["Whole Wheat Bread"]

    def test_surrounding_whitespace_is_ignored(self, client, db):
        add_item(db, "Milk")
        assert names(client, search="  milk  ") == ["Milk"]

    def test_no_match_returns_empty(self, client, db):
        add_item(db, "Milk")
        assert names(client, search="saffron") == []

    def test_empty_search_is_not_a_filter(self, client, db):
        add_item(db, "Milk")
        assert names(client, search="") == ["Milk"]

    def test_percent_is_matched_literally(self, client, db):
        """Unescaped, "%" would be a wildcard and match everything."""
        add_item(db, "2% Milk")
        add_item(db, "Bread")
        assert names(client, search="%") == ["2% Milk"]

    def test_underscore_is_matched_literally(self, client, db):
        add_item(db, "brown_sugar")
        add_item(db, "brownies")
        assert names(client, search="brown_") == ["brown_sugar"]


class TestCategoryFilter:
    def test_single_category(self, client, db):
        add_item(db, "Milk", category="dairy")
        add_item(db, "Tomatoes", category="produce")
        assert names(client, category="dairy") == ["Milk"]

    def test_several_categories_are_combined(self, client, db):
        add_item(db, "Milk", category="dairy")
        add_item(db, "Tomatoes", category="produce")
        add_item(db, "Rice", category="grains_pulses")
        assert sorted(names(client, category=["dairy", "produce"])) == [
            "Milk",
            "Tomatoes",
        ]

    def test_unknown_selects_uncategorised_items(self, client, db):
        add_item(db, "Milk", category="dairy")
        add_item(db, "Leftover Curry", category=None)
        assert names(client, category="unknown") == ["Leftover Curry"]

    def test_unknown_combines_with_a_real_category(self, client, db):
        add_item(db, "Milk", category="dairy")
        add_item(db, "Leftover Curry", category=None)
        add_item(db, "Tomatoes", category="produce")
        assert sorted(names(client, category=["dairy", "unknown"])) == [
            "Leftover Curry",
            "Milk",
        ]

    def test_invalid_category_is_rejected(self, client, db):
        add_item(db, "Milk", category="dairy")
        response = client.get("/api/inventory/", params={"category": "dairy products"})
        assert response.status_code == 422


class TestUrgencyFilter:
    def test_expired_only(self, client, db):
        add_item(db, "Old Yogurt", -3)
        add_item(db, "Milk", 2)
        assert names(client, urgency="expired") == ["Old Yogurt"]

    def test_today_excludes_tomorrow(self, client, db):
        add_item(db, "Bread", 0)
        add_item(db, "Milk", 1)
        assert names(client, urgency="today") == ["Bread"]

    def test_soon_covers_one_to_three_days(self, client, db):
        add_item(db, "Today", 0)
        add_item(db, "One", 1)
        add_item(db, "Three", 3)
        add_item(db, "Four", 4)
        assert sorted(names(client, urgency="soon")) == ["One", "Three"]

    def test_this_week_covers_four_to_seven_days(self, client, db):
        add_item(db, "Three", 3)
        add_item(db, "Four", 4)
        add_item(db, "Seven", 7)
        add_item(db, "Eight", 8)
        assert sorted(names(client, urgency="this_week")) == ["Four", "Seven"]

    def test_later_is_unbounded_above(self, client, db):
        add_item(db, "Seven", 7)
        add_item(db, "Rice", 400)
        assert names(client, urgency="later") == ["Rice"]

    def test_unknown_matches_a_null_date(self, client, db):
        add_item(db, "Salt", None)
        add_item(db, "Milk", 2)
        assert names(client, urgency="unknown") == ["Salt"]

    def test_unknown_matches_a_missing_expiration_row(self, client, db):
        """The outer join is what makes this item visible at all."""
        add_item(db, "Salt", with_expiration=False)
        add_item(db, "Milk", 2)
        assert names(client, urgency="unknown") == ["Salt"]

    def test_several_buckets_are_combined(self, client, db):
        add_item(db, "Gone", -1)
        add_item(db, "Soon", 2)
        add_item(db, "Later", 100)
        assert sorted(names(client, urgency=["expired", "soon"])) == ["Gone", "Soon"]

    def test_invalid_bucket_is_rejected(self, client, db):
        response = client.get("/api/inventory/", params={"urgency": "quite_soon"})
        assert response.status_code == 422


class TestCombinedFilters:
    def test_filters_narrow_together(self, client, db):
        add_item(db, "Amul Yogurt", -2, category="dairy")
        add_item(db, "Greek Yogurt", 100, category="dairy")
        add_item(db, "Old Spinach", -2, category="produce")
        assert names(client, search="yogurt", category="dairy", urgency="expired") == [
            "Amul Yogurt"
        ]

    def test_resolved_items_stay_excluded_by_default(self, client, db):
        item = add_item(db, "Milk", 2, category="dairy")
        client.post(
            f"/api/inventory/{item.id}/dispositions", json={"outcome": "consumed"}
        )
        assert names(client, category="dairy") == []
        assert names(client, category="dairy", include_resolved=True) == ["Milk"]


class TestSort:
    def test_default_sort_is_most_urgent_first(self, client, db):
        add_item(db, "Later", 30)
        add_item(db, "Gone", -5)
        add_item(db, "Soon", 2)
        assert names(client) == ["Gone", "Soon", "Later"]

    def test_undated_items_sort_last(self, client, db):
        add_item(db, "Salt", None)
        add_item(db, "Milk", 2)
        assert names(client) == ["Milk", "Salt"]

    def test_undated_items_sort_last_descending_too(self, client, db):
        """An item with no date is not the least urgent, it is unknown.

        Reversing the order must not promote a gap to the top of the list.
        """
        add_item(db, "Salt", None)
        add_item(db, "Milk", 2)
        add_item(db, "Rice", 400)
        assert names(client, direction="desc") == ["Rice", "Milk", "Salt"]

    def test_sort_by_name_is_case_insensitive(self, client, db):
        add_item(db, "banana")
        add_item(db, "Apple")
        assert names(client, sort="name") == ["Apple", "banana"]

    def test_sort_by_name_descending(self, client, db):
        add_item(db, "Apple")
        add_item(db, "banana")
        assert names(client, sort="name", direction="desc") == ["banana", "Apple"]

    def test_sort_by_quantity(self, client, db):
        add_item(db, "Few", quantity=2)
        add_item(db, "Many", quantity=9)
        assert names(client, sort="quantity") == ["Few", "Many"]
        assert names(client, sort="quantity", direction="desc") == ["Many", "Few"]

    def test_sort_by_category_puts_uncategorised_last(self, client, db):
        add_item(db, "Curry", category=None)
        add_item(db, "Milk", category="dairy")
        add_item(db, "Tomatoes", category="produce")
        assert names(client, sort="category") == ["Milk", "Tomatoes", "Curry"]

    def test_sort_by_category_descending_keeps_uncategorised_last(self, client, db):
        add_item(db, "Curry", category=None)
        add_item(db, "Milk", category="dairy")
        add_item(db, "Tomatoes", category="produce")
        assert names(client, sort="category", direction="desc") == [
            "Tomatoes",
            "Milk",
            "Curry",
        ]

    def test_sort_by_created(self, client, db):
        first = add_item(db, "First")
        second = add_item(db, "Second")
        first.created_at = first.created_at.replace(year=2020)
        second.created_at = second.created_at.replace(year=2024)
        db.commit()
        assert names(client, sort="created") == ["First", "Second"]
        assert names(client, sort="created", direction="desc") == ["Second", "First"]

    def test_ordering_is_stable_for_equal_keys(self, client, db):
        for index in range(5):
            add_item(db, f"Item {index}", 3)
        assert names(client) == names(client)

    def test_invalid_sort_is_rejected(self, client, db):
        assert (
            client.get("/api/inventory/", params={"sort": "colour"}).status_code == 422
        )

    def test_invalid_direction_is_rejected(self, client, db):
        assert (
            client.get(
                "/api/inventory/", params={"direction": "sideways"}
            ).status_code
            == 422
        )

    def test_expiration_is_not_a_separate_sort_option(self, client, db):
        """Ordering by expiry and by urgency are one behaviour, so one name."""
        assert (
            client.get("/api/inventory/", params={"sort": "expiration"}).status_code
            == 422
        )
