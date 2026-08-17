"""Tests for the expiry reminders query."""

from datetime import date, timedelta

from app.core import clock

import pytest

from app.models.inventory import Expiration, InventoryItem


def add_item(db, name: str, days_from_today: int | None, *, with_expiration=True):
    item = InventoryItem(
        name=name, quantity=1.0, unit="count", user_id=db.info["user"].id
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
    return item


def reminder_names(client, **params):
    response = client.get("/api/inventory/reminders", params=params)
    assert response.status_code == 200
    return [entry["name"] for entry in response.json()["items"]]


class TestWindow:
    def test_item_inside_the_window_is_included(self, client, db):
        add_item(db, "Milk", 3)
        assert reminder_names(client, days=7) == ["Milk"]

    def test_item_beyond_the_window_is_excluded(self, client, db):
        add_item(db, "Rice", 60)
        assert reminder_names(client, days=7) == []

    def test_item_on_the_boundary_is_included(self, client, db):
        """The filter is <= cutoff, so the last day of the window counts."""
        add_item(db, "Bread", 7)
        assert reminder_names(client, days=7) == ["Bread"]

    def test_item_one_day_past_the_boundary_is_excluded(self, client, db):
        add_item(db, "Bread", 8)
        assert reminder_names(client, days=7) == []

    def test_default_window_is_seven_days(self, client, db):
        add_item(db, "Inside", 6)
        add_item(db, "Outside", 20)
        assert reminder_names(client) == ["Inside"]

    def test_window_can_be_widened(self, client, db):
        add_item(db, "Rice", 30)
        assert reminder_names(client, days=60) == ["Rice"]

    def test_zero_days_returns_only_today_and_earlier(self, client, db):
        add_item(db, "Today", 0)
        add_item(db, "Tomorrow", 1)
        assert reminder_names(client, days=0) == ["Today"]


class TestExclusions:
    def test_item_without_an_expiration_row_is_excluded(self, client, db):
        """The inner join drops items that have no expiration at all."""
        add_item(db, "Salt", None, with_expiration=False)
        assert reminder_names(client, days=7) == []

    def test_item_with_a_null_expiration_date_is_excluded(self, client, db):
        """An unresolved date must not be treated as expiring."""
        add_item(db, "Saffron", None)
        assert reminder_names(client, days=7) == []

    def test_empty_inventory_returns_an_empty_list(self, client):
        assert reminder_names(client, days=7) == []


class TestPayload:
    def test_entry_exposes_provenance_and_quantity(self, client, db):
        add_item(db, "Milk", 2)
        entry = client.get("/api/inventory/reminders", params={"days": 7}).json()[
            "items"
        ][0]
        assert entry["name"] == "Milk"
        assert entry["quantity"] == 1.0
        assert entry["source"] == "user"
        assert entry["expiration_date"] == str(clock.today() + timedelta(days=2))
        assert entry["id"]

    def test_multiple_items_are_all_returned(self, client, db):
        add_item(db, "Milk", 1)
        add_item(db, "Bread", 4)
        assert sorted(reminder_names(client, days=7)) == ["Bread", "Milk"]


class TestAlreadyExpired:
    def test_expired_items_are_included_by_default(self, client, db):
        """Something already spoiled is the most urgent thing in the fridge."""
        add_item(db, "Ancient Yogurt", -200)
        assert reminder_names(client, days=7) == ["Ancient Yogurt"]

    def test_expired_items_are_distinguishable_from_upcoming(self, client, db):
        """The bug this feature fixes: these used to be presented identically."""
        add_item(db, "Ancient Yogurt", -200)
        add_item(db, "Fresh Milk", 2)
        entries = client.get("/api/inventory/reminders", params={"days": 7}).json()[
            "items"
        ]
        by_name = {entry["name"]: entry for entry in entries}
        assert by_name["Ancient Yogurt"]["urgency"] == "expired"
        assert by_name["Fresh Milk"]["urgency"] == "soon"

    def test_expired_items_can_be_excluded(self, client, db):
        add_item(db, "Ancient Yogurt", -200)
        add_item(db, "Fresh Milk", 2)
        names = reminder_names(client, days=7, include_expired=False)
        assert names == ["Fresh Milk"]

    def test_negative_day_counts_are_reported(self, client, db):
        add_item(db, "Ancient Yogurt", -200)
        entry = client.get("/api/inventory/reminders", params={"days": 7}).json()[
            "items"
        ][0]
        assert entry["days_remaining"] == -200


class TestUrgencyLabelling:
    def test_each_bucket_is_labelled(self, client, db):
        add_item(db, "Gone", -5)
        add_item(db, "Today", 0)
        add_item(db, "Soon", 2)
        add_item(db, "This Week", 6)
        entries = client.get("/api/inventory/reminders", params={"days": 7}).json()[
            "items"
        ]
        labelled = {entry["name"]: entry["urgency"] for entry in entries}
        assert labelled == {
            "Gone": "expired",
            "Today": "today",
            "Soon": "soon",
            "This Week": "this_week",
        }

    def test_entries_are_sorted_most_urgent_first(self, client, db):
        add_item(db, "Later", 6)
        add_item(db, "Gone", -5)
        add_item(db, "Soon", 2)
        assert reminder_names(client, days=7) == ["Gone", "Soon", "Later"]

    def test_counts_summarise_each_bucket(self, client, db):
        add_item(db, "Gone A", -5)
        add_item(db, "Gone B", -1)
        add_item(db, "Today", 0)
        add_item(db, "Soon", 3)
        body = client.get("/api/inventory/reminders", params={"days": 7}).json()
        assert body["counts"]["expired"] == 2
        assert body["counts"]["today"] == 1
        assert body["counts"]["soon"] == 1
        assert body["counts"]["this_week"] == 0

    def test_counts_include_every_bucket_even_when_zero(self, client, db):
        add_item(db, "Soon", 2)
        counts = client.get("/api/inventory/reminders", params={"days": 7}).json()[
            "counts"
        ]
        for bucket in ["expired", "today", "soon", "this_week", "later", "unknown"]:
            assert bucket in counts

    def test_action_required_counts_the_urgent_buckets(self, client, db):
        add_item(db, "Gone", -5)
        add_item(db, "Today", 0)
        add_item(db, "Soon", 2)
        add_item(db, "This Week", 6)
        body = client.get("/api/inventory/reminders", params={"days": 7}).json()
        assert body["action_required"] == 3, "this_week is not urgent"

    def test_empty_inventory_reports_zero_action_required(self, client):
        body = client.get("/api/inventory/reminders", params={"days": 7}).json()
        assert body["action_required"] == 0
        assert body["items"] == []


class TestItemUrgency:
    """Urgency is also exposed on inventory items themselves."""

    def test_items_carry_urgency_and_day_count(self, client, db):
        add_item(db, "Milk", 2)
        item = client.get("/api/inventory/").json()[0]
        assert item["urgency"] == "soon"
        assert item["days_remaining"] == 2

    def test_expired_item_is_labelled(self, client, db):
        add_item(db, "Old Yogurt", -3)
        item = client.get("/api/inventory/").json()[0]
        assert item["urgency"] == "expired"
        assert item["days_remaining"] == -3

    def test_item_without_a_date_is_unknown(self, client, db):
        add_item(db, "Salt", None)
        item = client.get("/api/inventory/").json()[0]
        assert item["urgency"] == "unknown"
        assert item["days_remaining"] is None

    def test_item_with_no_expiration_row_is_unknown(self, client, db):
        add_item(db, "Salt", None, with_expiration=False)
        item = client.get("/api/inventory/").json()[0]
        assert item["urgency"] == "unknown"
        assert item["days_remaining"] is None

    def test_single_item_fetch_includes_urgency(self, client, db):
        item = add_item(db, "Milk", 1)
        body = client.get(f"/api/inventory/{item.id}").json()
        assert body["urgency"] == "soon"
