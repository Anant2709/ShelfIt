"""Tests for the inventory CRUD surface, including validation boundaries."""

from datetime import date, timedelta

import pytest

from app.models.inventory import Expiration, InventoryItem


def make_payload(**overrides):
    payload = {
        "name": "Milk",
        "quantity": 1.0,
        "unit": "l",
        "expiration_date": str(date.today() + timedelta(days=5)),
    }
    payload.update(overrides)
    return payload


class TestCreate:
    def test_create_returns_the_persisted_item(self, client):
        response = client.post("/api/inventory/", json=make_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Milk"
        assert body["unit"] == "l"
        assert body["id"]

    def test_create_assigns_a_uuid_style_id(self, client):
        body = client.post("/api/inventory/", json=make_payload()).json()
        assert len(body["id"]) == 36
        assert body["id"].count("-") == 4

    def test_user_supplied_date_is_recorded_with_user_provenance(self, client):
        target = str(date.today() + timedelta(days=9))
        body = client.post(
            "/api/inventory/", json=make_payload(expiration_date=target)
        ).json()
        assert body["expiration"]["expiration_date"] == target
        assert body["expiration"]["source"] == "user"

    def test_defaults_are_applied(self, client):
        body = client.post(
            "/api/inventory/",
            json={"name": "Eggs", "expiration_date": str(date.today())},
        ).json()
        assert body["quantity"] == 1.0
        assert body["unit"] == "count"

    @pytest.mark.parametrize("bad_quantity", [0, -1, 0.001])
    def test_non_positive_quantity_is_rejected(self, client, bad_quantity):
        """Validation happens at the schema boundary, so handlers stay simple."""
        response = client.post(
            "/api/inventory/", json=make_payload(quantity=bad_quantity)
        )
        assert response.status_code == 422

    def test_name_is_required(self, client):
        assert client.post("/api/inventory/", json={"quantity": 1}).status_code == 422


class TestList:
    def test_empty_inventory_returns_empty_list(self, client):
        assert client.get("/api/inventory/").json() == []

    def test_all_items_are_returned(self, client):
        for name in ["Milk", "Bread", "Eggs"]:
            client.post("/api/inventory/", json=make_payload(name=name))
        names = [item["name"] for item in client.get("/api/inventory/").json()]
        assert sorted(names) == ["Bread", "Eggs", "Milk"]

    def test_each_item_embeds_its_expiration(self, client):
        client.post("/api/inventory/", json=make_payload())
        item = client.get("/api/inventory/").json()[0]
        assert item["expiration"]["source"] == "user"
        assert item["expiration"]["expiration_date"]


class TestRetrieve:
    def test_get_by_id(self, client):
        created = client.post("/api/inventory/", json=make_payload()).json()
        response = client.get(f"/api/inventory/{created['id']}")
        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    def test_unknown_id_returns_404(self, client):
        response = client.get("/api/inventory/does-not-exist")
        assert response.status_code == 404
        assert response.json()["detail"] == "Item not found"


class TestUpdate:
    def test_patch_applies_only_supplied_fields(self, client):
        created = client.post("/api/inventory/", json=make_payload()).json()
        response = client.patch(
            f"/api/inventory/{created['id']}", json={"quantity": 3.0}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["quantity"] == 3.0
        assert body["name"] == "Milk", "unspecified fields must be untouched"

    def test_patch_can_rename(self, client):
        created = client.post("/api/inventory/", json=make_payload()).json()
        body = client.patch(
            f"/api/inventory/{created['id']}", json={"name": "Oat Milk"}
        ).json()
        assert body["name"] == "Oat Milk"

    def test_patch_rejects_invalid_quantity(self, client):
        created = client.post("/api/inventory/", json=make_payload()).json()
        response = client.patch(f"/api/inventory/{created['id']}", json={"quantity": 0})
        assert response.status_code == 422

    def test_patch_unknown_id_returns_404(self, client):
        assert (
            client.patch("/api/inventory/nope", json={"quantity": 2}).status_code == 404
        )

    def test_patch_cannot_change_expiration(self, client):
        """Expiration is owned by its own endpoint, not the item PATCH."""
        created = client.post("/api/inventory/", json=make_payload()).json()
        original = created["expiration"]["expiration_date"]
        body = client.patch(
            f"/api/inventory/{created['id']}",
            json={"expiration_date": str(date.today() + timedelta(days=99))},
        ).json()
        assert body["expiration"]["expiration_date"] == original


class TestDelete:
    def test_delete_removes_the_item(self, client):
        created = client.post("/api/inventory/", json=make_payload()).json()
        assert client.delete(f"/api/inventory/{created['id']}").json() == {
            "status": "deleted"
        }
        assert client.get(f"/api/inventory/{created['id']}").status_code == 404

    def test_delete_cascades_to_the_expiration_row(self, client, db):
        created = client.post("/api/inventory/", json=make_payload()).json()
        assert db.query(Expiration).count() == 1
        client.delete(f"/api/inventory/{created['id']}")
        assert db.query(Expiration).count() == 0, "orphaned expiration row left behind"

    def test_delete_unknown_id_returns_404(self, client):
        assert client.delete("/api/inventory/nope").status_code == 404


class TestSetExpiration:
    def test_setting_expiration_records_user_source(self, client):
        created = client.post(
            "/api/inventory/", json={"name": "Tofu", "quantity": 1}
        ).json()
        target = str(date.today() + timedelta(days=4))
        response = client.post(
            f"/api/inventory/{created['id']}/expiration",
            json={"expiration_date": target},
        )
        assert response.status_code == 200
        assert response.json() == {
            "expiration_date": target,
            "source": "user",
            "shelf_life_days": None,
        }

    def test_setting_expiration_twice_replaces_rather_than_duplicates(
        self, client, db
    ):
        """item_id is the primary key, so one item can only ever hold one row."""
        created = client.post("/api/inventory/", json=make_payload()).json()
        for offset in (3, 8):
            client.post(
                f"/api/inventory/{created['id']}/expiration",
                json={"expiration_date": str(date.today() + timedelta(days=offset))},
            )
        assert db.query(Expiration).count() == 1

    def test_expiration_for_unknown_item_returns_404(self, client):
        response = client.post(
            "/api/inventory/nope/expiration",
            json={"expiration_date": str(date.today())},
        )
        assert response.status_code == 404


class TestInferredExpiration:
    def test_omitting_a_date_triggers_inference(self, client, monkeypatch):
        """With no user date, the cascade supplies one and records its source."""
        from app.api.endpoints import inventory as inventory_endpoint

        monkeypatch.setattr(
            inventory_endpoint, "lookup_shelf_life_days", lambda name: (6, "dataset")
        )
        body = client.post(
            "/api/inventory/", json={"name": "Mystery", "quantity": 1}
        ).json()
        assert body["expiration"]["source"] == "dataset"
        assert body["expiration"]["shelf_life_days"] == 6
        assert body["expiration"]["expiration_date"] == str(
            date.today() + timedelta(days=6)
        )

    def test_unknown_item_gets_no_fabricated_date(self, client, monkeypatch):
        from app.api.endpoints import inventory as inventory_endpoint

        monkeypatch.setattr(
            inventory_endpoint, "lookup_shelf_life_days", lambda name: (None, "unknown")
        )
        body = client.post(
            "/api/inventory/", json={"name": "Saffron", "quantity": 1}
        ).json()
        assert body["expiration"]["expiration_date"] is None
        assert body["expiration"]["source"] == "unknown"


def test_item_model_defaults(db):
    item = InventoryItem(name="Rice")
    db.add(item)
    db.commit()
    db.refresh(item)
    assert item.quantity == 1.0
    assert item.unit == "count"
    assert item.created_at is not None
    assert item.category is None
