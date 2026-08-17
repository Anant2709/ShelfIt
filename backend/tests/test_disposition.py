"""Consume-or-waste events, and the analytics that read them.

Delete is a correction. These tests are the reason delete is not the outcome
path: throwing something out has to leave a record, or the waste numbers are
fiction.
"""

from datetime import date, datetime, timedelta

from app.core import clock
from app.core.clock import utcnow
from types import SimpleNamespace

import pytest

from app.models.inventory import Disposition, Expiration, InventoryItem
from app.services.disposition import (
    AlreadyResolvedError,
    DispositionError,
    ExcessQuantityError,
    apply_disposition,
    revert_disposition,
    summarise_waste,
    waste_report,
)


def add_item(
    db, name="Milk", quantity=1.0, unit="l", days_from_today=5, category="dairy"
):
    item = InventoryItem(
        name=name,
        quantity=quantity,
        unit=unit,
        category=category,
        user_id=db.info["user"].id,
    )
    db.add(item)
    db.flush()
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


def event(**overrides):
    defaults = dict(
        item_id="x",
        outcome="wasted",
        item_name="Milk",
        item_category="dairy",
        quantity=1.0,
        unit="l",
        days_remaining=-1,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestApplyDisposition:
    def test_omitting_quantity_disposes_the_remainder(self, db):
        item = add_item(db, quantity=400, unit="g")
        recorded = apply_disposition(db, item, "consumed")
        db.commit()
        assert recorded.quantity == 400
        assert recorded.outcome == "consumed"
        assert item.quantity == 0
        assert item.resolved_at is not None
        assert item.is_resolved

    def test_partial_consume_leaves_the_item_active(self, db):
        item = add_item(db, quantity=400, unit="g")
        apply_disposition(db, item, "consumed", quantity=150)
        db.commit()
        assert item.quantity == 250
        assert item.resolved_at is None

    def test_remainder_below_the_floor_resolves_the_item(self, db):
        """0.005 of yogurt is not a real leftover; it is float dust."""
        item = add_item(db, quantity=0.1, unit="kg")
        apply_disposition(db, item, "consumed", quantity=0.095)
        db.commit()
        assert item.quantity == 0
        assert item.resolved_at is not None

    def test_waste_snapshots_name_and_days_remaining(self, db):
        item = add_item(db, name="Yogurt", days_from_today=-3)
        recorded = apply_disposition(db, item, "wasted", reason="mould")
        db.commit()
        assert recorded.item_name == "Yogurt"
        assert recorded.days_remaining == -3
        assert recorded.reason == "mould"
        assert recorded.unit == "l"

    def test_category_is_snapshotted_at_disposal(self, db):
        """Recategorising an item later must not rewrite last month's report."""
        item = add_item(db, name="Yogurt", category="dairy")
        recorded = apply_disposition(db, item, "wasted")
        db.commit()
        item.category = "produce"
        db.commit()
        assert recorded.item_category == "dairy"

    def test_uncategorised_item_snapshots_no_category(self, db):
        item = add_item(db, name="Leftover Curry", category=None)
        assert apply_disposition(db, item, "wasted").item_category is None

    def test_undated_item_snapshots_unknown_days(self, db):
        item = add_item(db, name="Salt", days_from_today=None)
        recorded = apply_disposition(db, item, "wasted")
        assert recorded.days_remaining is None
        assert recorded.expiration_date is None

    def test_already_resolved_item_cannot_be_disposed_again(self, db):
        item = add_item(db)
        apply_disposition(db, item, "consumed")
        db.commit()
        with pytest.raises(AlreadyResolvedError):
            apply_disposition(db, item, "wasted")

    def test_quantity_above_remaining_is_rejected(self, db):
        item = add_item(db, quantity=1.0)
        with pytest.raises(ExcessQuantityError) as exc:
            apply_disposition(db, item, "consumed", quantity=2.0)
        assert exc.value.remaining == 1.0
        assert item.quantity == 1.0
        assert item.resolved_at is None

    def test_non_positive_quantity_is_rejected(self, db):
        item = add_item(db)
        with pytest.raises(DispositionError, match="positive"):
            apply_disposition(db, item, "consumed", quantity=0)

    def test_unknown_outcome_is_rejected(self, db):
        item = add_item(db)
        with pytest.raises(DispositionError, match="Unknown outcome"):
            apply_disposition(db, item, "lost")

    def test_backdated_event_computes_days_remaining_as_of_then(self, db):
        item = add_item(db, days_from_today=2)
        # utcnow rather than datetime.now: stored timestamps are naive UTC, and a
        # local one is a different date for part of every evening.
        then = utcnow() - timedelta(days=5)
        recorded = apply_disposition(db, item, "wasted", occurred_at=then)
        # Expiry is two days from today, so five days ago it had seven days left.
        assert recorded.days_remaining == 7

    def test_events_are_attributed_to_the_user_by_default(self, db):
        """The assistant can also write here, so the default must be explicit."""
        item = add_item(db)
        assert apply_disposition(db, item, "consumed").source == "user"

    def test_source_can_be_overridden(self, db):
        item = add_item(db)
        recorded = apply_disposition(db, item, "consumed", source="assistant")
        assert recorded.source == "assistant"


class TestRevertDisposition:
    """Undo exists because the assistant can record outcomes itself.

    A model's write can be plausible and still wrong, so anything it does has to
    be reversible by the person it was done to.
    """

    def test_quantity_goes_back_on_the_shelf(self, db):
        item = add_item(db, quantity=400, unit="g")
        event = apply_disposition(db, item, "consumed", quantity=150)
        db.commit()
        revert_disposition(db, event)
        db.commit()
        db.refresh(item)
        assert item.quantity == 400

    def test_a_resolved_item_becomes_live_again(self, db):
        item = add_item(db, quantity=1.0)
        event = apply_disposition(db, item, "consumed")
        db.commit()
        assert item.resolved_at is not None

        revert_disposition(db, event)
        db.commit()
        db.refresh(item)
        assert item.resolved_at is None
        assert item.quantity == 1.0

    def test_the_event_is_removed_not_negated(self, db):
        """A matched pair of opposite events would inflate the waste counts."""
        item = add_item(db, quantity=1.0)
        event = apply_disposition(db, item, "wasted")
        db.commit()
        revert_disposition(db, event)
        db.commit()
        assert db.query(Disposition).count() == 0

    def test_reverting_one_of_several_leaves_the_others(self, db):
        item = add_item(db, quantity=400, unit="g")
        first = apply_disposition(db, item, "consumed", quantity=100)
        apply_disposition(db, item, "wasted", quantity=100)
        db.commit()

        revert_disposition(db, first)
        db.commit()
        db.refresh(item)
        assert item.quantity == 300
        assert db.query(Disposition).count() == 1

    def test_waste_analytics_forget_a_reverted_event(self, db):
        item = add_item(db, quantity=1.0)
        event = apply_disposition(db, item, "wasted")
        db.commit()
        revert_disposition(db, event)
        db.commit()
        assert waste_report(db, window_days=30).wasted.events == 0

    def test_an_orphaned_event_cannot_be_reverted(self, db):
        item = add_item(db, quantity=1.0)
        event = apply_disposition(db, item, "consumed")
        db.commit()
        # Detach the event so its item cannot be found, as a deleted item would.
        event.item_id = "gone"
        db.add(event)
        db.flush()
        with pytest.raises(DispositionError):
            revert_disposition(db, event)


class TestUndoEndpoint:
    def add(self, client, name="Milk", quantity=1.0, unit="l"):
        return client.post(
            "/api/inventory/",
            json={"name": name, "quantity": quantity, "unit": unit},
        ).json()

    def test_undo_returns_the_restored_item(self, client):
        created = self.add(client)
        event = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        ).json()["disposition"]

        response = client.delete(
            f"/api/inventory/{created['id']}/dispositions/{event['id']}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["quantity"] == 1.0
        assert body["is_resolved"] is False

    def test_undone_item_reappears_in_the_list(self, client):
        created = self.add(client)
        event = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "wasted"},
        ).json()["disposition"]
        assert client.get("/api/inventory/").json() == []

        client.delete(f"/api/inventory/{created['id']}/dispositions/{event['id']}")
        names = [item["name"] for item in client.get("/api/inventory/").json()]
        assert names == ["Milk"]

    def test_undo_removes_it_from_the_waste_report(self, client):
        created = self.add(client)
        event = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "wasted"},
        ).json()["disposition"]
        assert client.get("/api/analytics/waste").json()["wasted"]["events"] == 1

        client.delete(f"/api/inventory/{created['id']}/dispositions/{event['id']}")
        assert client.get("/api/analytics/waste").json()["wasted"]["events"] == 0

    def test_unknown_disposition_is_a_404(self, client):
        created = self.add(client)
        response = client.delete(
            f"/api/inventory/{created['id']}/dispositions/nope"
        )
        assert response.status_code == 404

    def test_a_disposition_belonging_to_another_item_is_a_404(self, client):
        """The id must match the item in the path, not just exist."""
        first = self.add(client, name="Milk")
        second = self.add(client, name="Bread", unit="count")
        event = client.post(
            f"/api/inventory/{first['id']}/dispositions",
            json={"outcome": "consumed"},
        ).json()["disposition"]

        response = client.delete(
            f"/api/inventory/{second['id']}/dispositions/{event['id']}"
        )
        assert response.status_code == 404
        # And the real event survived the mismatched attempt.
        assert (
            len(client.get(f"/api/inventory/{first['id']}/dispositions").json()) == 1
        )

    def test_an_event_whose_item_has_gone_is_not_found(self, client, db):
        """A missing item is 404, same as an id that was never yours.

        Constructed directly: deleting an item cascades the events, so this
        shape cannot arise through the API. The ownership check runs first, so
        the handler must not leak that a disposition row still exists for an
        item that does not.
        """
        from app.models.inventory import Disposition

        created = self.add(client)
        event = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        ).json()["disposition"]

        orphan = db.get(Disposition, event["id"])
        orphan.item_id = "gone"
        db.add(orphan)
        db.commit()

        response = client.delete(f"/api/inventory/gone/dispositions/{event['id']}")
        assert response.status_code == 404

    def test_a_revert_failure_is_a_conflict(self, client, db, monkeypatch):
        """The handler still maps a service error, even if ownership passed."""
        from app.services import disposition as disposition_service

        created = self.add(client)
        event = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        ).json()["disposition"]

        def _fail(*_args, **_kwargs):
            raise disposition_service.DispositionError("cannot undo")

        monkeypatch.setattr(
            "app.api.endpoints.inventory.revert_disposition", _fail
        )
        response = client.delete(
            f"/api/inventory/{created['id']}/dispositions/{event['id']}"
        )
        assert response.status_code == 409

    def test_an_assistant_recorded_event_can_be_undone(self, client, db):
        """The case this endpoint exists for."""
        from app.models.inventory import InventoryItem
        from app.services.disposition import apply_disposition

        created = self.add(client, name="Paneer", quantity=200, unit="g")
        item = db.get(InventoryItem, created["id"])
        event = apply_disposition(db, item, "consumed", source="assistant")
        db.commit()
        event_id = event.id

        response = client.delete(
            f"/api/inventory/{created['id']}/dispositions/{event_id}"
        )
        assert response.status_code == 200
        assert response.json()["quantity"] == 200


class TestSummariseWaste:
    def test_empty_window_is_zero_not_undefined(self):
        report = summarise_waste([], window_days=30)
        assert report.waste_rate == 0.0
        assert report.wasted.events == 0
        assert report.consumed.events == 0
        assert report.by_name == ()

    def test_waste_rate_is_events_not_quantities(self):
        """A litre and 200g cannot be added; the rate is a count of outcomes."""
        report = summarise_waste(
            [
                event(outcome="wasted", item_id="a", quantity=1, unit="l"),
                event(outcome="consumed", item_id="b", quantity=200, unit="g"),
                event(outcome="consumed", item_id="c", quantity=6, unit="count"),
            ],
            window_days=30,
        )
        assert report.waste_rate == pytest.approx(1 / 3)
        assert report.wasted.events == 1
        assert report.consumed.events == 2

    def test_distinct_items_are_counted_separately_from_events(self):
        report = summarise_waste(
            [
                event(outcome="wasted", item_id="a", item_name="Yogurt"),
                event(outcome="wasted", item_id="a", item_name="Yogurt"),
                event(outcome="wasted", item_id="b", item_name="Milk"),
            ],
            window_days=30,
        )
        assert report.wasted.events == 3
        assert report.wasted.items == 2

    def test_expiry_split_uses_the_snapshot_not_today(self):
        report = summarise_waste(
            [
                event(days_remaining=-2),
                event(days_remaining=3, item_id="b", item_name="Bread"),
                event(days_remaining=None, item_id="c", item_name="Salt"),
            ],
            window_days=30,
        )
        assert report.wasted_after_expiry == 1
        assert report.wasted_before_expiry == 1
        assert report.wasted_undated == 1

    def test_by_name_sums_quantity_only_within_one_unit(self):
        report = summarise_waste(
            [
                event(item_name="Yogurt", quantity=200, unit="g", item_id="a"),
                event(item_name="Yogurt", quantity=150, unit="g", item_id="b"),
                event(item_name="Milk", quantity=1, unit="l", item_id="c"),
            ],
            window_days=30,
        )
        yogurt, milk = report.by_name
        assert yogurt.name == "Yogurt"
        assert yogurt.events == 2
        assert yogurt.quantity == 350
        assert yogurt.unit == "g"
        assert milk.name == "Milk"
        assert milk.quantity == 1
        assert milk.unit == "l"

    def test_mixed_units_of_one_name_decline_the_quantity(self):
        report = summarise_waste(
            [
                event(item_name="Milk", quantity=1, unit="l", item_id="a"),
                event(item_name="Milk", quantity=500, unit="ml", item_id="b"),
            ],
            window_days=30,
        )
        assert report.by_name[0].quantity is None
        assert report.by_name[0].unit is None
        assert report.by_name[0].events == 2

    def test_by_name_ignores_consumed_events(self):
        report = summarise_waste(
            [
                event(outcome="consumed", item_name="Eggs"),
                event(outcome="wasted", item_name="Yogurt", item_id="b"),
            ],
            window_days=30,
        )
        assert [row.name for row in report.by_name] == ["Yogurt"]

    def test_by_category_groups_across_different_names(self):
        """The point of categories: "mostly dairy" is not visible per name."""
        report = summarise_waste(
            [
                event(item_name="Yogurt", item_category="dairy", item_id="a"),
                event(item_name="Paneer", item_category="dairy", item_id="b"),
                event(item_name="Lettuce", item_category="produce", item_id="c"),
            ],
            window_days=30,
        )
        assert [(row.category, row.events) for row in report.by_category] == [
            ("dairy", 2),
            ("produce", 1),
        ]

    def test_by_category_counts_distinct_items(self):
        report = summarise_waste(
            [
                event(item_category="dairy", item_id="a"),
                event(item_category="dairy", item_id="a"),
            ],
            window_days=30,
        )
        assert report.by_category[0].events == 2
        assert report.by_category[0].items == 1

    def test_uncategorised_waste_is_reported_as_null_not_dropped(self):
        report = summarise_waste(
            [event(item_category=None, item_name="Mystery Sauce")],
            window_days=30,
        )
        assert report.by_category[0].category is None
        assert report.by_category[0].events == 1

    def test_uncategorised_never_leads_the_breakdown(self):
        """It is the absence of a finding, so it must not read as the headline."""
        report = summarise_waste(
            [
                event(item_category=None, item_id="a"),
                event(item_category=None, item_id="b"),
                event(item_category=None, item_id="c"),
                event(item_category="dairy", item_id="d"),
            ],
            window_days=30,
        )
        assert report.by_category[0].category == "dairy"
        assert report.by_category[-1].category is None

    def test_by_category_carries_no_quantity(self):
        """A category mixes litres with grams, so a total would mean nothing."""
        report = summarise_waste([event()], window_days=30)
        assert not hasattr(report.by_category[0], "quantity")

    def test_by_category_ignores_consumed_events(self):
        report = summarise_waste(
            [
                event(outcome="consumed", item_category="dairy"),
                event(outcome="wasted", item_category="produce", item_id="b"),
            ],
            window_days=30,
        )
        assert [row.category for row in report.by_category] == ["produce"]

    def test_consumed_events_are_not_classified_by_expiry(self):
        report = summarise_waste(
            [event(outcome="consumed", days_remaining=-5)],
            window_days=30,
        )
        assert report.wasted_after_expiry == 0
        assert report.consumed.events == 1


class TestDispositionEndpoint:
    def test_consume_returns_the_event_and_updated_item(self, client):
        created = client.post(
            "/api/inventory/",
            json={"name": "Milk", "quantity": 1, "unit": "l"},
        ).json()
        response = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["disposition"]["outcome"] == "consumed"
        assert body["disposition"]["quantity"] == 1
        assert body["item"]["is_resolved"] is True
        assert body["item"]["quantity"] == 0

    def test_an_event_recorded_through_the_api_is_attributed_to_the_user(
        self, client
    ):
        created = client.post(
            "/api/inventory/", json={"name": "Milk", "quantity": 1, "unit": "l"}
        ).json()
        response = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        assert response.json()["disposition"]["source"] == "user"

    def test_partial_waste_keeps_the_item_on_the_list(self, client):
        created = client.post(
            "/api/inventory/",
            json={"name": "Yogurt", "quantity": 400, "unit": "g"},
        ).json()
        client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "wasted", "quantity": 150, "reason": "split"},
        )
        names = [item["name"] for item in client.get("/api/inventory/").json()]
        assert names == ["Yogurt"]
        remaining = client.get(f"/api/inventory/{created['id']}").json()
        assert remaining["quantity"] == 250
        assert remaining["is_resolved"] is False

    def test_fully_wasted_item_leaves_the_default_list(self, client):
        created = client.post(
            "/api/inventory/",
            json={"name": "Yogurt", "quantity": 400, "unit": "g"},
        ).json()
        client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "wasted"},
        )
        assert client.get("/api/inventory/").json() == []
        included = client.get("/api/inventory/", params={"include_resolved": True})
        assert [item["name"] for item in included.json()] == ["Yogurt"]

    def test_resolved_item_is_still_fetchable_by_id(self, client):
        created = client.post(
            "/api/inventory/", json={"name": "Milk", "quantity": 1}
        ).json()
        client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        body = client.get(f"/api/inventory/{created['id']}").json()
        assert body["is_resolved"] is True

    def test_second_disposition_on_a_resolved_item_is_conflict(self, client):
        created = client.post(
            "/api/inventory/", json={"name": "Milk", "quantity": 1}
        ).json()
        client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        response = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "wasted"},
        )
        assert response.status_code == 409

    def test_quantity_above_remaining_is_unprocessable(self, client):
        created = client.post(
            "/api/inventory/", json={"name": "Milk", "quantity": 1}
        ).json()
        response = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed", "quantity": 9},
        )
        assert response.status_code == 422

    def test_unknown_item_returns_404(self, client):
        response = client.post(
            "/api/inventory/nope/dispositions",
            json={"outcome": "wasted"},
        )
        assert response.status_code == 404

    def test_unknown_outcome_is_rejected_at_the_schema(self, client):
        created = client.post(
            "/api/inventory/", json={"name": "Milk", "quantity": 1}
        ).json()
        response = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "lost"},
        )
        assert response.status_code == 422

    def test_listing_dispositions_returns_them_in_order(self, client):
        created = client.post(
            "/api/inventory/",
            json={"name": "Yogurt", "quantity": 400, "unit": "g"},
        ).json()
        client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed", "quantity": 100},
        )
        client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "wasted", "quantity": 300},
        )
        events = client.get(f"/api/inventory/{created['id']}/dispositions").json()
        assert [event["outcome"] for event in events] == ["consumed", "wasted"]
        assert [event["quantity"] for event in events] == [100, 300]

    def test_listing_dispositions_for_unknown_item_returns_404(self, client):
        assert client.get("/api/inventory/nope/dispositions").status_code == 404

    def test_patch_on_a_resolved_item_is_conflict(self, client):
        created = client.post(
            "/api/inventory/", json={"name": "Milk", "quantity": 1}
        ).json()
        client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        response = client.patch(
            f"/api/inventory/{created['id']}", json={"name": "Oat Milk"}
        )
        assert response.status_code == 409

    def test_expiration_on_a_resolved_item_is_conflict(self, client):
        created = client.post(
            "/api/inventory/", json={"name": "Milk", "quantity": 1}
        ).json()
        client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        response = client.post(
            f"/api/inventory/{created['id']}/expiration",
            json={"expiration_date": str(clock.today())},
        )
        assert response.status_code == 409

    def test_delete_still_erases_the_history(self, client, db):
        """DELETE is the correction path: the events go with the mistaken row."""
        created = client.post(
            "/api/inventory/", json={"name": "Milk", "quantity": 1}
        ).json()
        client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        assert db.query(Disposition).count() == 1
        client.delete(f"/api/inventory/{created['id']}")
        assert db.query(Disposition).count() == 0


class TestRemindersIgnoreResolved:
    def test_wasted_expired_item_drops_out_of_reminders(self, client):
        created = client.post(
            "/api/inventory/",
            json={
                "name": "Old Yogurt",
                "quantity": 1,
                "expiration_date": str(clock.today() - timedelta(days=3)),
            },
        ).json()
        assert client.get("/api/inventory/reminders").json()["items"]
        client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "wasted"},
        )
        body = client.get("/api/inventory/reminders").json()
        assert body["items"] == []
        assert body["counts"]["expired"] == 0

    def test_resolved_undated_item_drops_out_of_needs_expiry_date(self, client, db):
        item = InventoryItem(
            name="Salt", quantity=1.0, unit="kg", user_id=db.info["user"].id
        )
        db.add(item)
        db.flush()
        db.add(Expiration(item_id=item.id, expiration_date=None, source="unknown"))
        db.commit()
        assert client.get("/api/inventory/reminders").json()["needs_expiry_date"] == 1
        client.post(
            f"/api/inventory/{item.id}/dispositions",
            json={"outcome": "consumed"},
        )
        assert client.get("/api/inventory/reminders").json()["needs_expiry_date"] == 0


class TestAnalyticsEndpoint:
    def test_empty_inventory_reports_zeroes(self, client):
        body = client.get("/api/analytics/waste").json()
        assert body["window_days"] == 30
        assert body["wasted"] == {"events": 0, "items": 0}
        assert body["consumed"] == {"events": 0, "items": 0}
        assert body["waste_rate"] == 0
        assert body["by_name"] == []
        assert "cost" not in body
        assert "rupees" not in body

    def test_report_counts_recent_outcomes(self, client):
        yogurt = client.post(
            "/api/inventory/",
            json={
                "name": "Yogurt",
                "quantity": 400,
                "unit": "g",
                "expiration_date": str(clock.today() - timedelta(days=2)),
            },
        ).json()
        milk = client.post(
            "/api/inventory/",
            json={"name": "Milk", "quantity": 1, "unit": "l"},
        ).json()
        client.post(
            f"/api/inventory/{yogurt['id']}/dispositions",
            json={"outcome": "wasted"},
        )
        client.post(
            f"/api/inventory/{milk['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        body = client.get("/api/analytics/waste").json()
        assert body["wasted"]["events"] == 1
        assert body["consumed"]["events"] == 1
        assert body["waste_rate"] == pytest.approx(0.5)
        assert body["wasted_after_expiry"] == 1
        assert body["by_name"][0]["name"] == "Yogurt"

    def test_report_groups_waste_by_category(self, client, db):
        for name, category in [
            ("Yogurt", "dairy"),
            ("Paneer", "dairy"),
            ("Lettuce", "produce"),
        ]:
            item = add_item(db, name=name, category=category, days_from_today=-1)
            apply_disposition(db, item, "wasted")
        db.commit()
        body = client.get("/api/analytics/waste").json()
        assert body["by_category"][0] == {
            "category": "dairy",
            "events": 2,
            "items": 2,
        }

    def test_empty_report_has_no_category_rows(self, client):
        assert client.get("/api/analytics/waste").json()["by_category"] == []

    def test_events_outside_the_window_are_excluded(self, client, db, monkeypatch):
        now = datetime(2026, 8, 15, 12, 0, 0)
        monkeypatch.setattr("app.services.disposition.utcnow", lambda: now)

        item = add_item(db, name="Lettuce", quantity=1, unit="count", days_from_today=-1)
        apply_disposition(
            db, item, "wasted", occurred_at=now - timedelta(days=40)
        )
        db.commit()

        body = client.get("/api/analytics/waste", params={"days": 30}).json()
        assert body["wasted"]["events"] == 0

        body = client.get("/api/analytics/waste", params={"days": 60}).json()
        assert body["wasted"]["events"] == 1

    def test_days_must_be_at_least_one(self, client):
        assert (
            client.get("/api/analytics/waste", params={"days": 0}).status_code
            == 422
        )


class TestDispositionErrorMapping:
    def test_unexpected_disposition_error_is_unprocessable(self, client, monkeypatch):
        """The handler translates domain errors; they must not become 500s."""
        created = client.post(
            "/api/inventory/", json={"name": "Milk", "quantity": 1}
        ).json()

        def _boom(*args, **kwargs):
            raise DispositionError("nope")

        monkeypatch.setattr(
            "app.api.endpoints.inventory.apply_disposition", _boom
        )
        response = client.post(
            f"/api/inventory/{created['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "nope"


def test_waste_report_reads_from_the_session(db):
    item = add_item(db, name="Yogurt", quantity=1, unit="count", days_from_today=-1)
    apply_disposition(db, item, "wasted")
    db.commit()
    report = waste_report(db, window_days=30)
    assert report.wasted.events == 1
    assert report.by_name[0].name == "Yogurt"
