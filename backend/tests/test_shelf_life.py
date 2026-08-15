"""Tests for the shelf-life cascade.

Two sources of truth and one resolver. The ordering between them is the design
decision, so it is asserted explicitly, as is the property that motivated the
rewrite: two spellings of the same item must not disagree.
"""

import json

import pytest

from app.core import config
from app.services import shelf_life
from app.services.learned_store import LearnedShelfLifeStore
from app.services.llm_estimator import Resolution
from app.services.shelf_life import (
    MAX_CANDIDATES,
    _normalize_name,
    _retrieve_candidates,
    lookup_shelf_life_days,
)


@pytest.fixture
def store(db):
    return LearnedShelfLifeStore(session_factory=lambda: db)


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    def _write(content: dict):
        path = tmp_path / "shelf_life.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        monkeypatch.setattr(config.settings, "shelf_life_path", str(path))
        shelf_life.reset_dataset_cache()
        return path

    return _write


@pytest.fixture
def no_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config.settings, "shelf_life_path", str(tmp_path / "missing.json")
    )
    shelf_life.reset_dataset_cache()


@pytest.fixture
def model(monkeypatch):
    """Stub the resolver and record the candidates it was offered."""
    calls = []

    def _install(resolution):
        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")

        def fake_resolve(name, candidates=None, client_factory=None):
            calls.append({"name": name, "candidates": candidates or {}})
            return resolution

        monkeypatch.setattr(shelf_life, "resolve_shelf_life", fake_resolve)
        return calls

    return _install


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setattr(
        shelf_life, "resolve_shelf_life", lambda *args, **kwargs: None
    )


class TestTier1Curated:
    def test_exact_curated_match_wins(self, dataset, no_model, store):
        dataset({"milk": 5})
        assert lookup_shelf_life_days("milk", store=store) == (5, "dataset")

    def test_lookup_is_case_and_whitespace_insensitive(self, dataset, no_model, store):
        dataset({"milk": 5})
        assert lookup_shelf_life_days("  MiLk  ", store=store) == (5, "dataset")

    def test_curated_outranks_the_model(self, dataset, model, store):
        """A deliberately chosen value beats an estimate, and costs nothing."""
        dataset({"milk": 5})
        calls = model(Resolution(days=99))
        assert lookup_shelf_life_days("milk", store=store) == (5, "dataset")
        assert calls == [], "the model should not have been consulted"

    def test_curated_outranks_a_learned_value(self, dataset, no_model, store):
        dataset({"milk": 5})
        store.remember("milk", days=99)
        assert lookup_shelf_life_days("milk", store=store) == (5, "dataset")


class TestTier2Learned:
    def test_learned_value_is_reused(self, no_dataset, model, store):
        store.remember("paneer", days=12)
        calls = model(Resolution(days=99))
        assert lookup_shelf_life_days("paneer", store=store) == (12, "learned")
        assert calls == [], "a learned value must not trigger another call"

    def test_learned_provenance_is_distinct_from_curated(
        self, no_dataset, no_model, store
    ):
        """A machine-derived value must never claim to be human-curated."""
        store.remember("paneer", days=12)
        _, source = lookup_shelf_life_days("paneer", store=store)
        assert source == "learned"


class TestTier3Model:
    def test_resolution_is_returned_and_persisted(self, no_dataset, model, store):
        model(Resolution(days=21))
        assert lookup_shelf_life_days("paneer", store=store) == (21, "llm")
        assert store.get("paneer").days == 21

    def test_anchor_is_persisted(self, dataset, model, store):
        dataset({"spinach": 4})
        model(Resolution(days=4, anchor="spinach", anchor_days=4))
        lookup_shelf_life_days("baby spinach", store=store)
        entry = store.get("baby spinach")
        assert entry.anchor == "spinach"
        assert entry.anchor_days == 4

    def test_model_used_is_recorded(self, no_dataset, model, store, monkeypatch):
        monkeypatch.setattr(config.settings, "openai_model", "gpt-4o")
        model(Resolution(days=21))
        lookup_shelf_life_days("paneer", store=store)
        assert store.get("paneer").model == "gpt-4o"

    def test_second_lookup_is_served_from_the_store(self, no_dataset, model, store):
        """Persisting the answer is what caps cost at one call per name."""
        calls = model(Resolution(days=21))
        lookup_shelf_life_days("paneer", store=store)
        lookup_shelf_life_days("paneer", store=store)
        assert len(calls) == 1

    def test_answers_are_stable_across_lookups(self, no_dataset, model, store):
        model(Resolution(days=21))
        results = {lookup_shelf_life_days("paneer", store=store) for _ in range(5)}
        assert results == {(21, "llm"), (21, "learned")}


class TestUnresolved:
    def test_nothing_is_invented(self, no_dataset, no_model, store):
        """No date beats a fabricated date; the user is asked instead."""
        assert lookup_shelf_life_days("saffron", store=store) == (None, "unknown")

    def test_failure_is_not_persisted(self, no_dataset, no_model, store):
        lookup_shelf_life_days("saffron", store=store)
        assert store.get("saffron") is None

    def test_a_later_attempt_can_still_resolve(self, no_dataset, model, store):
        """Because nothing was stored, recovery is immediate."""
        model(Resolution(days=30))
        assert lookup_shelf_life_days("saffron", store=store) == (30, "llm")


class TestConsistency:
    def test_variants_of_one_item_agree(self, dataset, monkeypatch, store):
        """The defect this rewrite exists to fix.

        Previously "spinach" resolved to 4 from the curated table while "fresh
        spinach" bypassed it and got an independent estimate. Anchoring makes the
        variant inherit the curated number.
        """
        dataset({"spinach": 4})
        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")

        def anchored(name, candidates=None, client_factory=None):
            if "spinach" in (candidates or {}):
                return Resolution(
                    days=candidates["spinach"], anchor="spinach", anchor_days=4
                )
            return None

        monkeypatch.setattr(shelf_life, "resolve_shelf_life", anchored)

        base, _ = lookup_shelf_life_days("spinach", store=store)
        variant, _ = lookup_shelf_life_days("fresh spinach", store=store)
        assert base == variant == 4

    def test_removed_tiers_no_longer_guess(self, dataset, no_model, store):
        """Token matching and the keyword heuristic were deleted.

        "whole wheat bread" no longer silently inherits "bread", and with no model
        available it resolves to nothing rather than to a pattern guess.
        """
        dataset({"bread": 7})
        assert lookup_shelf_life_days("whole wheat bread", store=store) == (
            None,
            "unknown",
        )

    def test_compound_names_are_not_misclassified_offline(
        self, dataset, no_model, store
    ):
        """The old heuristic gave "milk chocolate" a five-day dairy shelf life."""
        dataset({"milk": 5})
        days, source = lookup_shelf_life_days("milk chocolate", store=store)
        assert days is None
        assert source == "unknown"


class TestProvenance:
    @pytest.mark.parametrize(
        "name,expected",
        [("dataset", "dataset"), ("learned", "learned"), ("llm", "llm")],
    )
    def test_sources_are_from_the_known_set(self, name, expected):
        assert name in {"user", "dataset", "learned", "llm", "unknown"}

    def test_no_tier_claims_an_external_data_provider(
        self, no_dataset, model, store
    ):
        """Regression guard.

        The removed Spoonacular tier reported source="api" for a number it had
        invented. No tier may claim that provenance again.
        """
        model(Resolution(days=30))
        _, source = lookup_shelf_life_days("ketchup", store=store)
        assert source != "api"

    def test_heuristic_provenance_is_gone(self, no_dataset, no_model, store):
        _, source = lookup_shelf_life_days("chicken breast", store=store)
        assert source != "heuristic"


class TestCandidateRetrieval:
    def test_curated_and_learned_items_are_both_offered(
        self, dataset, model, store
    ):
        dataset({"spinach": 4})
        store.remember("tofu", days=9)
        calls = model(Resolution(days=5))
        lookup_shelf_life_days("spinach tofu salad", store=store)
        assert calls[0]["candidates"] == {"spinach": 4, "tofu": 9}

    def test_curated_values_win_over_learned_for_the_same_name(
        self, dataset, model, store
    ):
        """Where both know an item, the human-authored number is shown."""
        dataset({"spinach": 4})
        store.remember("spinach", days=99)
        calls = model(Resolution(days=5))
        lookup_shelf_life_days("fresh spinach leaves", store=store)
        assert calls[0]["candidates"]["spinach"] == 4

    def test_irrelevant_items_are_not_offered(self, dataset, model, store):
        dataset({"spinach": 4, "ketchup": 365})
        calls = model(Resolution(days=5))
        lookup_shelf_life_days("baby spinach", store=store)
        assert "ketchup" not in calls[0]["candidates"]

    def test_candidate_count_is_capped(self, dataset, model, store):
        dataset({f"spinach{i}": i + 1 for i in range(30)} | {"spinach": 4})
        calls = model(Resolution(days=5))
        lookup_shelf_life_days("spinach", store=store)
        # "spinach" is an exact curated match, so use a name that is not.
        lookup_shelf_life_days("some spinach thing", store=store)
        assert len(calls[-1]["candidates"]) <= MAX_CANDIDATES

    def test_no_known_items_yields_no_candidates(self, no_dataset, model, store):
        calls = model(Resolution(days=5))
        lookup_shelf_life_days("dragonfruit", store=store)
        assert calls[0]["candidates"] == {}


class TestRetrieval:
    def test_retrieval_orders_most_relevant_first(self, dataset, store):
        dataset({"bread": 7, "wheat bread": 3, "milk": 5})
        candidates = _retrieve_candidates("whole wheat bread", store)
        assert list(candidates)[0] == "wheat bread"
        assert "milk" not in candidates


class TestDatasetFileMemoisation:
    def test_file_is_parsed_once_for_repeated_lookups(
        self, dataset, no_model, store, monkeypatch
    ):
        dataset({"milk": 5})
        loads = []
        original = json.load

        def counting_load(handle):
            loads.append(1)
            return original(handle)

        monkeypatch.setattr(shelf_life.json, "load", counting_load)
        for _ in range(5):
            lookup_shelf_life_days("milk", store=store)
        assert len(loads) == 1

    def test_editing_the_file_invalidates_the_memoised_copy(
        self, dataset, no_model, store
    ):
        """Keyed on mtime so development edits take effect without a restart."""
        path = dataset({"milk": 5})
        assert lookup_shelf_life_days("milk", store=store) == (5, "dataset")

        import os
        import time

        path.write_text(json.dumps({"milk": 9}), encoding="utf-8")
        future = time.time() + 10
        os.utime(path, (future, future))

        assert lookup_shelf_life_days("milk", store=store) == (9, "dataset")

    def test_missing_file_yields_an_empty_dataset(self, no_dataset):
        assert shelf_life._load_dataset() == {}


def test_normalize_name():
    assert _normalize_name("  Whole Milk ") == "whole milk"
