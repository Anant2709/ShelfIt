"""Tests for the expiry reminders query."""

from datetime import date, timedelta

import pytest

from app.models.inventory import Expiration, InventoryItem


def add_item(db, name: str, days_from_today: int | None, *, with_expiration=True):
    item = InventoryItem(name=name, quantity=1.0, unit="count")
    db.add(item)
    db.flush()
    if with_expiration:
        db.add(
            Expiration(
                item_id=item.id,
                expiration_date=(
                    date.today() + timedelta(days=days_from_today)
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
        assert entry["expiration_date"] == str(date.today() + timedelta(days=2))
        assert entry["id"]

    def test_multiple_items_are_all_returned(self, client, db):
        add_item(db, "Milk", 1)
        add_item(db, "Bread", 4)
        assert sorted(reminder_names(client, days=7)) == ["Bread", "Milk"]


class TestAlreadyExpired:
    def test_expired_items_are_currently_included(self, client, db):
        """Documents present behaviour: the query has no lower bound."""
        add_item(db, "Ancient Yogurt", -200)
        assert reminder_names(client, days=7) == ["Ancient Yogurt"]

    @pytest.mark.xfail(
        reason=(
            "Known gap: 'Upcoming Expirations' has no lower bound, so items that "
            "expired months ago are presented identically to items expiring "
            "tomorrow. Urgency bucketing will separate expired from upcoming."
        ),
        strict=True,
    )
    def test_expired_items_should_be_distinguishable_from_upcoming(self, client, db):
        add_item(db, "Ancient Yogurt", -200)
        add_item(db, "Fresh Milk", 2)
        entries = client.get("/api/inventory/reminders", params={"days": 7}).json()[
            "items"
        ]
        assert any(entry.get("status") == "expired" for entry in entries)
