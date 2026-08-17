"""Tests for the category cascade.

The closed set is the design decision, so it is asserted directly: a reply the
prompt did not permit must be discarded rather than coerced to something nearby,
because a category that only nearly matches will silently split every grouping
that reads it.
"""

import json

import pytest

from app.core import config
from app.services import category as category_module
from app.services.category import (
    ASSIGNABLE,
    MAX_CANDIDATES,
    Category,
    _normalize_name,
    _retrieve_candidates,
    lookup_category,
)
from app.services.category_store import LearnedCategoryStore
from app.services.llm_categorizer import _parse, resolve_category

from app.core import clock


@pytest.fixture
def store(db):
    return LearnedCategoryStore(session_factory=lambda: db)


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    def _write(content: dict):
        path = tmp_path / "categories.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        monkeypatch.setattr(config.settings, "categories_path", str(path))
        category_module.reset_category_dataset_cache()
        return path

    return _write


@pytest.fixture
def no_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config.settings, "categories_path", str(tmp_path / "missing.json")
    )
    category_module.reset_category_dataset_cache()


@pytest.fixture
def model(monkeypatch):
    """Stub the resolver and record the candidates it was offered."""
    calls = []

    def _install(result):
        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")

        def fake_resolve(name, allowed, candidates=None, client_factory=None):
            calls.append({"name": name, "allowed": allowed, "candidates": candidates})
            return result

        monkeypatch.setattr(category_module, "resolve_category", fake_resolve)
        return calls

    return _install


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setattr(
        category_module, "resolve_category", lambda *args, **kwargs: None
    )


class TestClosedSet:
    def test_unknown_is_not_offered_to_the_model(self):
        """UNKNOWN is the absence of an answer, not one of the choices."""
        assert Category.UNKNOWN.value not in ASSIGNABLE
        assert len(ASSIGNABLE) == len(Category) - 1

    def test_every_assignable_value_is_a_category(self):
        for value in ASSIGNABLE:
            assert Category(value)

    def test_storage_state_is_not_a_category(self):
        """One axis only: what the food is, not where it is kept.

        Frozen chicken is both frozen and meat, so a field mixing the two cannot
        be grouped by either.
        """
        assert "frozen" not in ASSIGNABLE
        assert "fridge" not in ASSIGNABLE


class TestTier1Curated:
    def test_exact_curated_match_wins(self, dataset, no_model, store):
        dataset({"paneer": "dairy"})
        assert lookup_category("paneer", store=store) == (Category.DAIRY, "dataset")

    def test_lookup_is_case_and_whitespace_insensitive(self, dataset, no_model, store):
        dataset({"paneer": "dairy"})
        assert lookup_category("  Paneer ", store=store) == (
            Category.DAIRY,
            "dataset",
        )

    def test_curated_match_does_not_call_the_model(self, dataset, model, store):
        dataset({"paneer": "dairy"})
        calls = model("produce")
        lookup_category("paneer", store=store)
        assert calls == []

    def test_curated_entry_outside_the_closed_set_is_ignored(
        self, dataset, no_model, store
    ):
        """Curated does not mean correct. A hand-edited typo is still rejected."""
        dataset({"paneer": "dairy products"})
        assert lookup_category("paneer", store=store) == (None, "unknown")


class TestTier2Learned:
    def test_learned_value_is_reused(self, no_dataset, model, store):
        store.remember("paneer", category="dairy")
        calls = model("produce")
        assert lookup_category("paneer", store=store) == (Category.DAIRY, "learned")
        assert calls == [], "a learned value must not trigger another call"

    def test_learned_provenance_is_distinct_from_curated(
        self, no_dataset, no_model, store
    ):
        store.remember("paneer", category="dairy")
        _, source = lookup_category("paneer", store=store)
        assert source == "learned"


class TestTier3Model:
    def test_resolution_is_returned_and_persisted(self, no_dataset, model, store):
        model("dairy")
        assert lookup_category("paneer", store=store) == (Category.DAIRY, "llm")
        assert store.get("paneer").category == "dairy"

    def test_model_used_is_recorded(self, no_dataset, model, store, monkeypatch):
        monkeypatch.setattr(config.settings, "openai_model", "gpt-4o")
        model("dairy")
        lookup_category("paneer", store=store)
        assert store.get("paneer").model == "gpt-4o"

    def test_second_lookup_is_served_from_the_store(self, no_dataset, model, store):
        """Persisting the answer is what caps cost at one call per name."""
        calls = model("dairy")
        lookup_category("paneer", store=store)
        lookup_category("paneer", store=store)
        assert len(calls) == 1

    def test_only_assignable_values_are_offered(self, no_dataset, model, store):
        calls = model("dairy")
        lookup_category("paneer", store=store)
        assert calls[0]["allowed"] == ASSIGNABLE


class TestUnresolved:
    def test_nothing_is_invented(self, no_dataset, no_model, store):
        assert lookup_category("gulab jamun mix", store=store) == (None, "unknown")

    def test_failure_is_not_persisted(self, no_dataset, no_model, store):
        """An outage must not become a permanent answer."""
        lookup_category("gulab jamun mix", store=store)
        assert store.get("gulab jamun mix") is None

    def test_a_later_attempt_can_still_resolve(self, no_dataset, model, store):
        model("snacks_sweets")
        assert lookup_category("gulab jamun mix", store=store) == (
            Category.SNACKS_SWEETS,
            "llm",
        )


class TestCandidateRetrieval:
    def test_curated_and_learned_items_are_both_offered(self, dataset, model, store):
        dataset({"spinach": "produce"})
        store.remember("spinach leaves", category="produce")
        calls = model("produce")
        lookup_category("baby spinach", store=store)
        assert "spinach" in calls[0]["candidates"]
        assert "spinach leaves" in calls[0]["candidates"]

    def test_irrelevant_known_items_are_not_offered(self, dataset, model, store):
        """Retrieval narrows the reference material; it does not dump the table."""
        dataset({"spinach": "produce", "cement": "pantry"})
        calls = model("produce")
        lookup_category("baby spinach", store=store)
        assert "cement" not in calls[0]["candidates"]

    def test_curated_wins_where_both_know_an_item(self, dataset, store):
        dataset({"paneer": "dairy"})
        store.remember("paneer", category="produce")
        assert _retrieve_candidates("paneer", store)["paneer"] == "dairy"

    def test_candidates_are_capped(self, dataset, store):
        dataset({f"spinach {index}": "produce" for index in range(20)})
        assert len(_retrieve_candidates("spinach", store)) == MAX_CANDIDATES

    def test_no_known_items_yields_no_candidates(self, no_dataset, model, store):
        calls = model("produce")
        lookup_category("dragonfruit", store=store)
        assert calls[0]["candidates"] == {}


class TestParseReply:
    def test_valid_category_is_accepted(self):
        assert _parse('{"category": "dairy"}', ASSIGNABLE) == "dairy"

    def test_case_and_whitespace_are_normalised(self):
        assert _parse('{"category": "  Dairy "}', ASSIGNABLE) == "dairy"

    def test_explicit_null_is_no_answer(self):
        assert _parse('{"category": null}', ASSIGNABLE) is None

    @pytest.mark.parametrize(
        "reply",
        [
            '{"category": "dairy products"}',
            '{"category": "Dairy & Eggs"}',
            '{"category": "milk"}',
            '{"category": "unknown"}',
        ],
    )
    def test_values_outside_the_set_are_discarded_not_coerced(self, reply):
        """The nearest match is not the answer; a disallowed reply is no answer.

        "dairy products" and "Dairy & Eggs" both obviously mean dairy, and that
        is exactly the temptation. Accepting them means the set is no longer
        closed, and the grouping fragments the first time one slips through.
        """
        assert _parse(reply, ASSIGNABLE) is None

    @pytest.mark.parametrize(
        "reply",
        ["not json", "[]", '"dairy"', "null", '{"category": 5}', '{"category": true}'],
    )
    def test_malformed_replies_are_discarded(self, reply):
        assert _parse(reply, ASSIGNABLE) is None

    def test_missing_key_is_discarded(self):
        assert _parse("{}", ASSIGNABLE) is None

    def test_empty_content_is_discarded(self):
        assert _parse(None, ASSIGNABLE) is None
        assert _parse("", ASSIGNABLE) is None


class TestResolveCategoryCall:
    def test_no_api_key_means_no_call(self, monkeypatch):
        monkeypatch.setattr(config.settings, "openai_api_key", None)
        called = []

        def _factory():
            called.append(1)
            raise AssertionError("must not construct a client without a key")

        assert resolve_category("paneer", ASSIGNABLE, client_factory=_factory) is None
        assert called == []

    def test_provider_error_returns_none(self, monkeypatch):
        from openai import OpenAIError

        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")

        class FailingClient:
            def __init__(self):
                self.chat = type("Chat", (), {})()
                self.chat.completions = self

            def create(self, **kwargs):
                raise OpenAIError("boom")

        assert (
            resolve_category("paneer", ASSIGNABLE, client_factory=FailingClient)
            is None
        )

    def test_candidates_are_included_in_the_prompt(self, monkeypatch):
        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")
        captured = {}

        class Client:
            def __init__(self):
                self.chat = type("Chat", (), {})()
                self.chat.completions = self

            def create(self, **kwargs):
                captured.update(kwargs)
                return type(
                    "Response",
                    (),
                    {
                        "choices": [
                            type(
                                "Choice",
                                (),
                                {
                                    "message": type(
                                        "Message", (), {"content": '{"category": "dairy"}'}
                                    )()
                                },
                            )()
                        ]
                    },
                )()

        result = resolve_category(
            "paneer",
            ASSIGNABLE,
            candidates={"milk": "dairy"},
            client_factory=Client,
        )
        assert result == "dairy"
        prompt = "\n".join(message["content"] for message in captured["messages"])
        assert "milk: dairy" in prompt
        assert "paneer" in prompt

    def test_absent_candidates_are_stated_explicitly(self, monkeypatch):
        """The model should know the list is empty, not be handed a blank."""
        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")
        captured = {}

        class Client:
            def __init__(self):
                self.chat = type("Chat", (), {})()
                self.chat.completions = self

            def create(self, **kwargs):
                captured.update(kwargs)
                return type(
                    "Response",
                    (),
                    {
                        "choices": [
                            type(
                                "Choice",
                                (),
                                {
                                    "message": type(
                                        "Message", (), {"content": '{"category": null}'}
                                    )()
                                },
                            )()
                        ]
                    },
                )()

        resolve_category("paneer", ASSIGNABLE, client_factory=Client)
        prompt = "\n".join(message["content"] for message in captured["messages"])
        assert "No similar items are known yet." in prompt


class TestDatasetFile:
    def test_file_is_parsed_once_for_repeated_lookups(
        self, dataset, no_model, store, monkeypatch
    ):
        dataset({"milk": "dairy"})
        loads = []
        original = json.load

        def counting_load(handle):
            loads.append(1)
            return original(handle)

        monkeypatch.setattr(category_module.json, "load", counting_load)
        for _ in range(5):
            lookup_category("milk", store=store)
        assert len(loads) == 1

    def test_editing_the_file_invalidates_the_memoised_copy(
        self, dataset, no_model, store
    ):
        path = dataset({"milk": "dairy"})
        assert lookup_category("milk", store=store) == (Category.DAIRY, "dataset")

        import os
        import time

        path.write_text(json.dumps({"milk": "beverages"}), encoding="utf-8")
        future = time.time() + 10
        os.utime(path, (future, future))

        assert lookup_category("milk", store=store) == (Category.BEVERAGES, "dataset")

    def test_missing_file_yields_an_empty_dataset(self, no_dataset):
        assert category_module._load_dataset() == {}


class TestLearnedStore:
    def test_remember_then_get(self, store):
        store.remember("paneer", category="dairy", model="gpt-4o")
        entry = store.get("paneer")
        assert entry.category == "dairy"
        assert entry.model == "gpt-4o"
        assert entry.confirmed is False

    def test_remember_replaces_rather_than_duplicating(self, store):
        store.remember("paneer", category="produce")
        store.remember("paneer", category="dairy")
        assert store.get("paneer").category == "dairy"
        assert len(store.all()) == 1

    def test_get_unknown_name_returns_none(self, store):
        assert store.get("nope") is None

    def test_all_is_ordered_by_name(self, store):
        for name in ["milk", "atta", "paneer"]:
            store.remember(name, category="dairy")
        assert [entry.name for entry in store.all()] == ["atta", "milk", "paneer"]

    def test_forget_removes_an_entry(self, store):
        store.remember("paneer", category="dairy")
        assert store.forget("paneer") is True
        assert store.get("paneer") is None

    def test_forget_unknown_name_reports_failure(self, store):
        assert store.forget("nope") is False

    def test_clear_empties_the_table(self, store):
        store.remember("paneer", category="dairy")
        store.remember("milk", category="dairy")
        store.clear()
        assert store.all() == []

    def test_default_store_is_a_singleton(self):
        from app.services.category_store import (
            get_category_store,
            reset_category_store,
        )

        reset_category_store()
        first = get_category_store()
        assert get_category_store() is first
        reset_category_store()
        assert get_category_store() is not first

    def test_default_store_uses_the_configured_session_factory(self, monkeypatch):
        """Exercises the lazily imported production factory."""
        from app.db import session as session_module
        from app.services.category_store import LearnedCategoryStore

        opened = []

        class FakeSession:
            def get(self, *args):
                opened.append(1)
                return None

            def close(self):
                pass

        monkeypatch.setattr(session_module, "SessionLocal", lambda: FakeSession())
        assert LearnedCategoryStore().get("paneer") is None
        assert opened == [1]


class TestCategoryOnItems:
    """How categories reach an item, and when they may be overwritten."""

    def test_created_item_is_categorised_from_the_curated_file(
        self, client, dataset, no_model
    ):
        dataset({"paneer": "dairy"})
        body = client.post("/api/inventory/", json={"name": "Paneer"}).json()
        assert body["category"] == "dairy"
        assert body["category_source"] == "dataset"

    def test_unresolvable_item_is_left_uncategorised(
        self, client, no_dataset, no_model
    ):
        """No category is better than a wrong one, and it stays selectable."""
        body = client.post("/api/inventory/", json={"name": "Leftover Curry"}).json()
        assert body["category"] is None
        assert body["category_source"] == "unknown"

    def test_user_supplied_category_is_recorded_as_such(
        self, client, dataset, no_model
    ):
        dataset({"paneer": "produce"})
        body = client.post(
            "/api/inventory/", json={"name": "Paneer", "category": "dairy"}
        ).json()
        assert body["category"] == "dairy"
        assert body["category_source"] == "user"

    def test_user_supplied_category_skips_inference(self, client, dataset, model):
        dataset({})
        calls = model("produce")
        client.post("/api/inventory/", json={"name": "Paneer", "category": "dairy"})
        assert calls == [], "the user already answered; do not pay to ask again"

    def test_user_can_state_that_the_category_is_unknown(
        self, client, dataset, no_model
    ):
        """Stored as NULL like any other unknown, but sourced to the user.

        Otherwise a deliberate "I don't know" would be indistinguishable from a
        failed lookup, and inference would keep overwriting it.
        """
        dataset({"paneer": "dairy"})
        body = client.post(
            "/api/inventory/", json={"name": "Paneer", "category": "unknown"}
        ).json()
        assert body["category"] is None
        assert body["category_source"] == "user"

    def test_invalid_category_on_create_is_rejected(self, client):
        response = client.post(
            "/api/inventory/", json={"name": "Paneer", "category": "dairy products"}
        )
        assert response.status_code == 422

    def test_patch_can_set_the_category(self, client, dataset, no_model):
        dataset({"paneer": "produce"})
        created = client.post("/api/inventory/", json={"name": "Paneer"}).json()
        body = client.patch(
            f"/api/inventory/{created['id']}", json={"category": "dairy"}
        ).json()
        assert body["category"] == "dairy"
        assert body["category_source"] == "user"

    def test_patch_can_clear_the_category(self, client, dataset, no_model):
        dataset({"paneer": "dairy"})
        created = client.post("/api/inventory/", json={"name": "Paneer"}).json()
        body = client.patch(
            f"/api/inventory/{created['id']}", json={"category": None}
        ).json()
        assert body["category"] is None
        assert body["category_source"] == "user"

    def test_patch_applies_category_alongside_other_fields(
        self, client, dataset, no_model
    ):
        dataset({"paneer": "produce"})
        created = client.post("/api/inventory/", json={"name": "Paneer"}).json()
        body = client.patch(
            f"/api/inventory/{created['id']}",
            json={"category": "dairy", "quantity": 3.0},
        ).json()
        assert body["category"] == "dairy"
        assert body["quantity"] == 3.0

    def test_rename_reinfers_an_inferred_category(self, client, dataset, no_model):
        """The old category described the old name."""
        dataset({"paneer": "dairy", "tomatoes": "produce"})
        created = client.post("/api/inventory/", json={"name": "Paneer"}).json()
        assert created["category"] == "dairy"
        body = client.patch(
            f"/api/inventory/{created['id']}", json={"name": "Tomatoes"}
        ).json()
        assert body["category"] == "produce"
        assert body["category_source"] == "dataset"

    def test_rename_does_not_overwrite_a_user_category(
        self, client, dataset, no_model
    ):
        """Renaming is not permission to discard an answer the user gave."""
        dataset({"tomatoes": "produce"})
        created = client.post(
            "/api/inventory/", json={"name": "Paneer", "category": "dairy"}
        ).json()
        body = client.patch(
            f"/api/inventory/{created['id']}", json={"name": "Tomatoes"}
        ).json()
        assert body["category"] == "dairy"
        assert body["category_source"] == "user"

    def test_changing_only_quantity_does_not_reinfer(self, client, dataset, model):
        dataset({"paneer": "dairy"})
        created = client.post("/api/inventory/", json={"name": "Paneer"}).json()
        calls = model("produce")
        client.patch(f"/api/inventory/{created['id']}", json={"quantity": 2.0})
        assert calls == []

    def test_scanned_items_are_categorised(
        self, client, dataset, no_model, monkeypatch, uploads_dir, sample_image_bytes
    ):
        from app.api.endpoints import inventory as endpoint
        from app.services.classifier import Detection

        dataset({"milk": "dairy"})
        monkeypatch.setattr(
            endpoint, "detect_items", lambda path: [Detection("milk", 0.99)]
        )
        response = client.post(
            "/api/inventory/scan",
            files={"file": ("milk.png", sample_image_bytes, "image/png")},
        )
        assert response.json()["created_items"][0]["category"] == "dairy"

    def test_labelled_items_are_categorised(
        self, client, dataset, no_model, uploads_dir, sample_image_bytes
    ):
        dataset({"paneer": "dairy"})
        (uploads_dir / "shot.png").write_bytes(sample_image_bytes)
        body = client.post(
            "/api/inventory/label",
            json={"image_id": "shot.png", "label": "Paneer"},
        ).json()
        assert body["category"] == "dairy"
        assert body["category_source"] == "dataset"

    def test_reminders_carry_the_category(self, client, dataset, no_model):
        from datetime import date, timedelta

        dataset({"paneer": "dairy"})
        client.post(
            "/api/inventory/",
            json={
                "name": "Paneer",
                "expiration_date": str(clock.today() + timedelta(days=2)),
            },
        )
        entry = client.get("/api/inventory/reminders").json()["items"][0]
        assert entry["category"] == "dairy"


def test_normalize_name():
    assert _normalize_name("  Baby Spinach ") == "baby spinach"
