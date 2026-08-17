"""Open Food Facts / Exa nutrition lookup and packaged-label gate."""

from types import SimpleNamespace

from app.services import nutrition as nutrition_mod
from app.services.nutrition import NutritionResult, lookup_exa, lookup_nutrition, lookup_open_food_facts
from app.services.packaged_label import PackagedLabel, _parse_label, read_packaged_label


class TestPackagedLabelParse:
    def test_requires_readable_and_confidence(self):
        assert _parse_label({"readable": False, "confidence": 0.99}) is None
        assert (
            _parse_label(
                {
                    "readable": True,
                    "brand": "Amul",
                    "product_name": "Toned Milk",
                    "confidence": 0.5,
                }
            )
            is None
        )
        assert _parse_label({"readable": True, "confidence": "nope"}) is None
        assert (
            _parse_label(
                {
                    "readable": True,
                    "brand": "",
                    "product_name": "Milk",
                    "confidence": 0.9,
                }
            )
            is None
        )
        assert (
            _parse_label(
                {
                    "readable": True,
                    "brand": "Amul",
                    "product_name": 12,
                    "confidence": 0.9,
                }
            )
            is None
        )
        label = _parse_label(
            {
                "readable": True,
                "brand": "Amul",
                "product_name": "Toned Milk",
                "confidence": 0.9,
            }
        )
        assert label == PackagedLabel("Amul", "Toned Milk", 0.9)

    def test_skips_without_openai_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "app.services.packaged_label.settings.openai_api_key", ""
        )
        path = tmp_path / "photo.jpg"
        path.write_bytes(b"fake")
        assert read_packaged_label(path) is None

    def test_skips_unreadable_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "app.services.packaged_label.settings.openai_api_key", "sk-test"
        )
        missing = tmp_path / "missing.jpg"
        assert read_packaged_label(missing) is None

    def test_uses_cache_hit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "app.services.packaged_label.settings.openai_api_key", "sk-test"
        )
        monkeypatch.setattr(
            "app.services.packaged_label.settings.vision_model", "gpt-test"
        )
        path = tmp_path / "photo.jpg"
        path.write_bytes(b"abc")

        class FakeCache:
            def get(self, namespace, key):
                return {
                    "brand": "Cached",
                    "product_name": "Bar",
                    "confidence": 0.95,
                }

            def set(self, *args, **kwargs):
                raise AssertionError("should not write on hit")

        label = read_packaged_label(path, cache=FakeCache())
        assert label.brand == "Cached"
        assert label.product_name == "Bar"

    def test_cached_negative(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "app.services.packaged_label.settings.openai_api_key", "sk-test"
        )
        path = tmp_path / "photo.jpg"
        path.write_bytes(b"abc")

        class FakeCache:
            def get(self, namespace, key):
                return None

            def set(self, *args, **kwargs):
                raise AssertionError("no write")

        assert read_packaged_label(path, cache=FakeCache()) is None

    def test_model_success_and_failures(self, monkeypatch, tmp_path):
        from openai import OpenAIError

        monkeypatch.setattr(
            "app.services.packaged_label.settings.openai_api_key", "sk-test"
        )
        monkeypatch.setattr(
            "app.services.packaged_label.settings.vision_model", "gpt-test"
        )
        path = tmp_path / "photo.jpg"
        path.write_bytes(b"abc")

        class Store:
            def __init__(self):
                self.data = {}

            def get(self, namespace, key):
                from app.services.cache import MISS

                return self.data.get((namespace, key), MISS)

            def set(self, namespace, key, value):
                self.data[(namespace, key)] = value

        store = Store()

        def factory_ok():
            class Client:
                class chat:
                    class completions:
                        @staticmethod
                        def create(**kwargs):
                            return SimpleNamespace(
                                choices=[
                                    SimpleNamespace(
                                        message=SimpleNamespace(
                                            content='{"readable": true, "brand": "Amul", "product_name": "Milk", "confidence": 0.91}'
                                        )
                                    )
                                ]
                            )

            return Client()

        label = read_packaged_label(path, cache=store, client_factory=factory_ok)
        assert label.brand == "Amul"
        assert store.data  # cached

        store2 = Store()

        def factory_bad_json():
            class Client:
                class chat:
                    class completions:
                        @staticmethod
                        def create(**kwargs):
                            return SimpleNamespace(
                                choices=[
                                    SimpleNamespace(
                                        message=SimpleNamespace(content="not-json")
                                    )
                                ]
                            )

            return Client()

        assert (
            read_packaged_label(path, cache=store2, client_factory=factory_bad_json)
            is None
        )

        store3 = Store()

        def factory_empty():
            class Client:
                class chat:
                    class completions:
                        @staticmethod
                        def create(**kwargs):
                            return SimpleNamespace(
                                choices=[
                                    SimpleNamespace(
                                        message=SimpleNamespace(content="")
                                    )
                                ]
                            )

            return Client()

        assert (
            read_packaged_label(path, cache=store3, client_factory=factory_empty)
            is None
        )

        store4 = Store()

        def factory_list():
            class Client:
                class chat:
                    class completions:
                        @staticmethod
                        def create(**kwargs):
                            return SimpleNamespace(
                                choices=[
                                    SimpleNamespace(
                                        message=SimpleNamespace(content="[]")
                                    )
                                ]
                            )

            return Client()

        assert (
            read_packaged_label(path, cache=store4, client_factory=factory_list)
            is None
        )

        def factory_error():
            class Client:
                class chat:
                    class completions:
                        @staticmethod
                        def create(**kwargs):
                            raise OpenAIError("boom")

            return Client()

        assert (
            read_packaged_label(path, cache=Store(), client_factory=factory_error)
            is None
        )


class TestNutritionLookup:
    def test_parse_helpers(self):
        assert nutrition_mod._parse_float(True) is None
        assert nutrition_mod._parse_float("x") is None
        assert nutrition_mod._parse_float(-1) is None
        assert nutrition_mod._parse_float(12.34) == 12.3
        assert nutrition_mod._parse_kcal(True) is None
        assert nutrition_mod._parse_kcal("x") is None
        assert nutrition_mod._parse_kcal(5000) is None
        assert nutrition_mod._parse_kcal(120.6) == 121

    def test_off_hit_skips_exa(self):
        off = NutritionResult(120, 8.0, 10.0, 4.0, "open_food_facts", "Amul", "Milk")
        called = {"exa": False}

        def fake_off(query):
            return off

        def fake_exa(query):
            called["exa"] = True
            return NutritionResult(999, None, None, None, "exa")

        result = lookup_nutrition(
            brand="Amul",
            product_name="Milk",
            off_lookup=fake_off,
            exa_lookup=fake_exa,
        )
        assert result.source == "open_food_facts"
        assert result.calories_kcal == 120
        assert called["exa"] is False

    def test_off_miss_uses_exa(self):
        def fake_off(query):
            return None

        def fake_exa(query):
            return NutritionResult(200, 5.0, 20.0, 8.0, "exa")

        result = lookup_nutrition(
            brand="Brand",
            product_name="Bar",
            off_lookup=fake_off,
            exa_lookup=fake_exa,
        )
        assert result.source == "exa"
        assert result.calories_kcal == 200
        assert result.brand == "Brand"

    def test_both_miss_is_none(self):
        result = lookup_nutrition(
            brand="Brand",
            product_name="Bar",
            off_lookup=lambda q: None,
            exa_lookup=lambda q: None,
        )
        assert result.source == "none"

    def test_empty_query_is_none(self):
        result = lookup_nutrition(brand=None, product_name=None)
        assert result.source == "none"

    def test_off_parser_from_product(self):
        assert nutrition_mod._off_from_product({"nutriments": "bad"}) is None
        assert nutrition_mod._off_from_product({"nutriments": {}}) is None
        parsed = nutrition_mod._off_from_product(
            {
                "brands": "Amul, Other",
                "product_name": "Toned Milk",
                "nutriments": {
                    "energy-kcal_100g": 62,
                    "proteins_100g": 3.2,
                    "carbohydrates_100g": 4.8,
                    "fat_100g": 3.5,
                },
            }
        )
        assert parsed is not None
        assert parsed.source == "open_food_facts"
        assert parsed.brand == "Amul"
        assert parsed.calories_kcal == 62
        serving = nutrition_mod._off_from_product(
            {
                "brands": 12,
                "product_name": "   ",
                "nutriments": {"energy-kcal_serving": 90},
            }
        )
        assert serving.calories_kcal == 90
        assert serving.brand is None
        assert serving.product_name is None

    def test_exa_text_extraction(self):
        assert nutrition_mod._exa_macros_from_text("nothing useful") is None
        parsed = nutrition_mod._exa_macros_from_text(
            "Calories: 250 Protein 12g Carbs 30g Fat 8g"
        )
        assert parsed is not None
        assert parsed.source == "exa"
        assert parsed.calories_kcal == 250
        assert parsed.protein_g == 12.0

    def test_lookup_open_food_facts_http(self, monkeypatch):
        assert lookup_open_food_facts("") is None

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "products": [
                        {"nutriments": {}},
                        {
                            "brands": "X",
                            "product_name": "Y",
                            "nutriments": {"energy-kcal_100g": 100},
                        },
                    ]
                }

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        result = lookup_open_food_facts("milk", session=FakeSession())
        assert result.calories_kcal == 100

        class BoomSession:
            def get(self, *args, **kwargs):
                raise nutrition_mod.requests.RequestException("down")

        assert lookup_open_food_facts("milk", session=BoomSession()) is None

        class BadJson:
            def raise_for_status(self):
                return None

            def json(self):
                return []

        class BadSession:
            def get(self, *args, **kwargs):
                return BadJson()

        assert lookup_open_food_facts("milk", session=BadSession()) is None

    def test_lookup_exa_http(self, monkeypatch):
        monkeypatch.setattr(nutrition_mod.settings, "exa_api_key", None)
        assert lookup_exa("milk") is None
        monkeypatch.setattr(nutrition_mod.settings, "exa_api_key", "exa-key")
        assert lookup_exa("") is None

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": [
                        {"title": "no macros"},
                        {
                            "title": "Bar",
                            "text": "Calories 180 Protein 4g carbs 22g fat 7g",
                        },
                    ]
                }

        class FakeSession:
            def post(self, *args, **kwargs):
                return FakeResponse()

        result = lookup_exa("granola", session=FakeSession())
        assert result.source == "exa"
        assert result.calories_kcal == 180

        class Boom:
            def post(self, *args, **kwargs):
                raise nutrition_mod.requests.RequestException("down")

        assert lookup_exa("granola", session=Boom()) is None

        class Empty:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": "nope"}

        class EmptySession:
            def post(self, *args, **kwargs):
                return Empty()

        assert lookup_exa("granola", session=EmptySession()) is None


class TestScanNutritionWiring:
    def test_unreadable_label_skips_lookups(self, client, monkeypatch, tmp_path):
        from app.api.endpoints import inventory as inventory_ep
        from app.services.classifier import Detection

        monkeypatch.setattr(
            inventory_ep,
            "detect_items",
            lambda path: [Detection(label="milk", confidence=0.95)],
        )
        monkeypatch.setattr(inventory_ep, "read_packaged_label", lambda path: None)

        looked_up = {"called": False}

        def boom(**kwargs):
            looked_up["called"] = True
            raise AssertionError("lookup must not run")

        monkeypatch.setattr(inventory_ep, "lookup_nutrition", boom)

        image = tmp_path / "scan.jpg"
        image.write_bytes(b"\xff\xd8\xff\xd9")
        response = client.post(
            "/api/inventory/scan",
            files={"file": ("scan.jpg", image.read_bytes(), "image/jpeg")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "created"
        item = body["created_items"][0]
        assert item["nutrition_source"] in (None, "none")
        assert looked_up["called"] is False

    def test_readable_label_stores_off_nutrition(self, client, monkeypatch, tmp_path):
        from app.api.endpoints import inventory as inventory_ep
        from app.services.classifier import Detection
        from app.services.packaged_label import PackagedLabel

        monkeypatch.setattr(
            inventory_ep,
            "detect_items",
            lambda path: [Detection(label="milk", confidence=0.95)],
        )
        monkeypatch.setattr(
            inventory_ep,
            "read_packaged_label",
            lambda path: PackagedLabel("Amul", "Toned Milk", 0.92),
        )
        monkeypatch.setattr(
            inventory_ep,
            "lookup_nutrition",
            lambda **kwargs: NutritionResult(
                62, 3.2, 4.8, 3.5, "open_food_facts", "Amul", "Toned Milk"
            ),
        )

        image = tmp_path / "scan.jpg"
        image.write_bytes(b"\xff\xd8\xff\xd9")
        response = client.post(
            "/api/inventory/scan",
            files={"file": ("scan.jpg", image.read_bytes(), "image/jpeg")},
        )
        assert response.status_code == 200
        item = response.json()["created_items"][0]
        assert item["nutrition_source"] == "open_food_facts"
        assert item["calories_kcal"] == 62
        assert item["brand"] == "Amul"
        assert item["product_name"] == "Toned Milk"

    def test_readable_but_no_nutrition_keeps_brand(self, client, monkeypatch, tmp_path):
        from app.api.endpoints import inventory as inventory_ep
        from app.services.classifier import Detection
        from app.services.packaged_label import PackagedLabel

        monkeypatch.setattr(
            inventory_ep,
            "detect_items",
            lambda path: [Detection(label="bar", confidence=0.95)],
        )
        monkeypatch.setattr(
            inventory_ep,
            "read_packaged_label",
            lambda path: PackagedLabel("Kind", "Bar", 0.9),
        )
        monkeypatch.setattr(
            inventory_ep,
            "lookup_nutrition",
            lambda **kwargs: NutritionResult(None, None, None, None, "none"),
        )
        image = tmp_path / "scan.jpg"
        image.write_bytes(b"\xff\xd8\xff\xd9")
        item = client.post(
            "/api/inventory/scan",
            files={"file": ("scan.jpg", image.read_bytes(), "image/jpeg")},
        ).json()["created_items"][0]
        assert item["brand"] == "Kind"
        assert item["nutrition_source"] == "none"
