"""Tests for the scan -> confidence gate -> manual label flow.

This is the human-in-the-loop path: when the model is not confident enough, the
API refuses to guess, asks the user, and turns the correction into training data.
"""

import json
from datetime import date, timedelta

import pytest

from app.api.endpoints import inventory as inventory_endpoint
from app.core import config
from app.models.inventory import InventoryItem


@pytest.fixture
def stub_classifier(monkeypatch):
    """Replace the vision model with a deterministic stub."""

    def _stub(label: str, confidence: float):
        monkeypatch.setattr(
            inventory_endpoint, "classify_image", lambda path: (label, confidence)
        )

    return _stub


def post_scan(client, image_bytes, **form):
    return client.post(
        "/api/inventory/scan",
        files={"file": ("fridge.png", image_bytes, "image/png")},
        data=form,
    )


class TestConfidenceGate:
    def test_low_confidence_does_not_create_an_item(
        self, client, db, uploads_dir, sample_image_bytes, stub_classifier
    ):
        stub_classifier("milk", 0.42)
        response = post_scan(client, sample_image_bytes)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "needs_label"
        assert body["suggested_label"] == "milk"
        assert body["confidence"] == pytest.approx(0.42)
        assert body["item"] is None
        assert db.query(InventoryItem).count() == 0, "low confidence must not persist"

    def test_confidence_above_threshold_creates_the_item(
        self, client, db, uploads_dir, sample_image_bytes, stub_classifier
    ):
        stub_classifier("bread", 0.95)
        body = post_scan(client, sample_image_bytes).json()
        assert body["status"] == "created"
        assert body["item"]["name"] == "bread"
        assert body["item"]["confidence"] == pytest.approx(0.95)
        assert db.query(InventoryItem).count() == 1

    def test_confidence_exactly_at_threshold_is_accepted(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        """The gate is a strict less-than, so the boundary value passes."""
        threshold = config.settings.model_confidence_threshold
        stub_classifier("bread", threshold)
        assert post_scan(client, sample_image_bytes).json()["status"] == "created"

    def test_confidence_just_below_threshold_is_rejected(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        threshold = config.settings.model_confidence_threshold
        stub_classifier("bread", threshold - 0.001)
        assert post_scan(client, sample_image_bytes).json()["status"] == "needs_label"

    def test_unknown_label_is_rejected_even_at_high_confidence(
        self, client, db, uploads_dir, sample_image_bytes, stub_classifier
    ):
        """A confident 'unknown' is still unusable as an item name."""
        stub_classifier("unknown", 0.99)
        body = post_scan(client, sample_image_bytes).json()
        assert body["status"] == "needs_label"
        assert db.query(InventoryItem).count() == 0


class TestScanPersistence:
    def test_uploaded_image_is_written_to_the_uploads_directory(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        stub_classifier("bread", 0.95)
        post_scan(client, sample_image_bytes)
        written = list(uploads_dir.iterdir())
        assert len(written) == 1
        assert written[0].read_bytes() == sample_image_bytes

    def test_stored_filename_is_timestamp_prefixed_to_avoid_collisions(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        stub_classifier("bread", 0.95)
        post_scan(client, sample_image_bytes)
        post_scan(client, sample_image_bytes)
        names = [p.name for p in uploads_dir.iterdir()]
        assert len(names) == 2, "identical filenames must not overwrite each other"
        assert all(name.endswith("fridge.png") for name in names)

    def test_scan_records_the_image_path_on_the_item(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        stub_classifier("bread", 0.95)
        item = post_scan(client, sample_image_bytes).json()["item"]
        assert item["image_uri"].endswith("fridge.png")

    def test_scan_honours_supplied_quantity_and_unit(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        stub_classifier("milk", 0.95)
        item = post_scan(
            client, sample_image_bytes, quantity="2.5", unit="l"
        ).json()["item"]
        assert item["quantity"] == 2.5
        assert item["unit"] == "l"

    def test_scan_honours_supplied_expiration_date(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        stub_classifier("milk", 0.95)
        target = str(date.today() + timedelta(days=3))
        item = post_scan(
            client, sample_image_bytes, expiration_date=target
        ).json()["item"]
        assert item["expiration"]["expiration_date"] == target
        assert item["expiration"]["source"] == "user"

    def test_scan_requires_a_file(self, client):
        assert client.post("/api/inventory/scan", data={}).status_code == 422


class TestManualLabel:
    def label(self, client, image_id, **overrides):
        payload = {"image_id": image_id, "label": "Paneer", "quantity": 1, "unit": "count"}
        payload.update(overrides)
        return client.post("/api/inventory/label", json=payload)

    def scan_to_get_image_id(self, client, image_bytes, stub_classifier):
        stub_classifier("unknown", 0.0)
        return post_scan(client, image_bytes).json()["image_id"]

    def test_label_creates_the_item(
        self, client, db, uploads_dir, sample_image_bytes, stub_classifier
    ):
        image_id = self.scan_to_get_image_id(client, sample_image_bytes, stub_classifier)
        response = self.label(client, image_id)
        assert response.status_code == 200
        assert response.json()["name"] == "Paneer"
        assert db.query(InventoryItem).count() == 1

    def test_labelled_item_carries_no_model_confidence(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        """A human-supplied name has no model confidence to report."""
        image_id = self.scan_to_get_image_id(client, sample_image_bytes, stub_classifier)
        assert self.label(client, image_id).json()["confidence"] is None

    def test_label_copies_the_image_into_the_training_set(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        """The correction becomes a labelled training example."""
        image_id = self.scan_to_get_image_id(client, sample_image_bytes, stub_classifier)
        self.label(client, image_id, label="Paneer")
        training_dir = uploads_dir.parent / "training" / "labels" / "paneer"
        assert training_dir.is_dir()
        assert (training_dir / image_id).read_bytes() == sample_image_bytes

    def test_label_appends_a_manifest_record(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        image_id = self.scan_to_get_image_id(client, sample_image_bytes, stub_classifier)
        self.label(client, image_id, label="Paneer")
        manifest = uploads_dir.parent / "training" / "manifest.jsonl"
        record = json.loads(manifest.read_text(encoding="utf-8").strip())
        assert record["image_id"] == image_id
        assert record["label"] == "Paneer"
        assert record["created_at"]

    def test_manifest_accumulates_across_corrections(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        for label in ["Paneer", "Tofu"]:
            image_id = self.scan_to_get_image_id(
                client, sample_image_bytes, stub_classifier
            )
            self.label(client, image_id, label=label)
        manifest = uploads_dir.parent / "training" / "manifest.jsonl"
        lines = manifest.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert [json.loads(line)["label"] for line in lines] == ["Paneer", "Tofu"]

    @pytest.mark.parametrize(
        "label,expected_dir",
        [
            ("Amul Milk", "amul_milk"),
            ("Ben & Jerry's", "ben___jerry_s"),
            ("UPPER-case_ok", "upper-case_ok"),
        ],
    )
    def test_label_is_sanitised_into_a_safe_directory_name(
        self,
        client,
        uploads_dir,
        sample_image_bytes,
        stub_classifier,
        label,
        expected_dir,
    ):
        image_id = self.scan_to_get_image_id(client, sample_image_bytes, stub_classifier)
        self.label(client, image_id, label=label)
        assert (uploads_dir.parent / "training" / "labels" / expected_dir).is_dir()

    def test_unknown_image_id_returns_404(self, client, uploads_dir):
        assert self.label(client, "no-such-image.png").status_code == 404

    def test_path_traversal_in_image_id_is_neutralised(self, client, uploads_dir):
        """Only the basename is honoured, so '../' cannot escape the directory."""
        response = self.label(client, "../../../../etc/passwd")
        assert response.status_code == 404

    def test_label_applies_the_shelf_life_cascade(
        self, client, uploads_dir, sample_image_bytes, stub_classifier, monkeypatch
    ):
        monkeypatch.setattr(
            inventory_endpoint, "lookup_shelf_life_days", lambda name: (4, "dataset")
        )
        image_id = self.scan_to_get_image_id(client, sample_image_bytes, stub_classifier)
        body = self.label(client, image_id).json()
        assert body["expiration"]["source"] == "dataset"
        assert body["expiration"]["expiration_date"] == str(
            date.today() + timedelta(days=4)
        )

    def test_label_requires_a_non_empty_label(self, client, uploads_dir):
        response = client.post(
            "/api/inventory/label", json={"image_id": "x.png", "quantity": 1}
        )
        assert response.status_code == 422


class TestUploadImageToExistingItem:
    def test_upload_attaches_image_and_confidence(
        self, client, uploads_dir, sample_image_bytes, stub_classifier
    ):
        stub_classifier("milk", 0.88)
        created = client.post(
            "/api/inventory/",
            json={"name": "Milk", "quantity": 1, "expiration_date": str(date.today())},
        ).json()
        response = client.post(
            f"/api/inventory/{created['id']}/image",
            files={"file": ("m.png", sample_image_bytes, "image/png")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["confidence"] == pytest.approx(0.88)
        assert body["image_uri"].endswith("m.png")
        assert body["name"] == "Milk", "an existing name must not be overwritten"

    def test_upload_fills_in_a_placeholder_name(
        self, client, db, uploads_dir, sample_image_bytes, stub_classifier
    ):
        """Items literally named 'unknown' adopt the model's suggestion."""
        stub_classifier("bread", 0.9)
        item = InventoryItem(name="unknown")
        db.add(item)
        db.commit()
        response = client.post(
            f"/api/inventory/{item.id}/image",
            files={"file": ("b.png", sample_image_bytes, "image/png")},
        )
        assert response.json()["name"] == "bread"

    def test_upload_to_unknown_item_returns_404(
        self, client, uploads_dir, sample_image_bytes
    ):
        response = client.post(
            "/api/inventory/nope/image",
            files={"file": ("b.png", sample_image_bytes, "image/png")},
        )
        assert response.status_code == 404
