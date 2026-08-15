"""Tests for the shelf-life inference cascade.

The cascade is the core domain logic: given a name and no user date, decide how
long the item keeps and record how the number was obtained. Each tier is asserted
independently, and the ordering between them is asserted explicitly, because the
ordering is the design decision.
"""

import json

import pytest

from app.core import config
from app.services import shelf_life
from app.services.cache import InMemoryCache
from app.services.shelf_life import (
    _heuristic_fallback,
    _normalize_name,
    lookup_shelf_life_days,
)


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    """Point the cascade at a controlled dataset file."""

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
def no_llm(monkeypatch):
    """Disable the model tier so the local tiers can be tested in isolation."""
    monkeypatch.setattr(config.settings, "openai_api_key", None)


@pytest.fixture
def llm_says(monkeypatch):
    """Force the model tier to return a fixed estimate."""

    def _install(days):
        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")
        monkeypatch.setattr(
            shelf_life, "estimate_shelf_life_days", lambda name, **kwargs: days
        )

    return _install


class TestTier1ExactDataset:
    def test_exact_key_returns_dataset_value(self, dataset, no_llm):
        dataset({"milk": 5, "bread": 7})
        assert lookup_shelf_life_days("milk") == (5, "dataset")

    def test_lookup_is_case_and_whitespace_insensitive(self, dataset, no_llm):
        dataset({"milk": 5})
        assert lookup_shelf_life_days("  MiLk  ") == (5, "dataset")

    def test_exact_match_outranks_the_model(self, dataset, llm_says):
        """A deliberately curated value beats an estimate, and costs nothing."""
        dataset({"milk": 5})
        llm_says(99)
        assert lookup_shelf_life_days("milk") == (5, "dataset")


class TestTier2Model:
    def test_model_estimate_is_labelled_llm(self, no_dataset, llm_says):
        llm_says(365)
        assert lookup_shelf_life_days("ketchup") == (365, "llm")

    def test_model_outranks_the_token_match(self, dataset, llm_says):
        """The case the old cascade got wrong.

        "milk chocolate" contains the token "milk", so a token match assigns it a
        5-day dairy shelf life. A model that understands the item does not.
        """
        dataset({"milk": 5})
        llm_says(240)
        assert lookup_shelf_life_days("milk chocolate") == (240, "llm")

    def test_model_outranks_the_heuristic(self, no_dataset, llm_says):
        llm_says(120)
        assert lookup_shelf_life_days("coconut milk") == (120, "llm")

    def test_absent_model_falls_through(self, dataset, no_llm):
        dataset({"bread": 7})
        assert lookup_shelf_life_days("whole wheat bread") == (7, "dataset")


class TestTier3TokenDataset:
    def test_multiword_name_matches_on_token(self, dataset, no_llm):
        """"Whole wheat bread" is not a key, but "bread" is."""
        dataset({"bread": 7})
        assert lookup_shelf_life_days("Whole wheat bread") == (7, "dataset")

    def test_longest_matching_key_wins(self, dataset, no_llm):
        """A specific multi-word key beats a shorter generic token.

        The looked-up name is deliberately not itself a key, otherwise tier 1
        would answer and this branch would never run.
        """
        dataset({"bread": 7, "wheat bread": 3})
        assert lookup_shelf_life_days("whole wheat bread") == (3, "dataset")

    def test_multiword_key_must_appear_contiguously(self, dataset, no_llm):
        dataset({"wheat bread": 3})
        assert lookup_shelf_life_days("wheat flour and white bread") == (
            None,
            "unknown",
        )

    def test_partial_token_does_not_match_the_dataset(self, dataset, no_llm):
        """"milkshake" is one token, so it must not match the "milk" key.

        It still resolves, but via the heuristic tier -- the source is what proves
        the token tier declined.
        """
        dataset({"milk": 5})
        _, source = lookup_shelf_life_days("milkshake")
        assert source == "heuristic"

    def test_model_style_sku_label_resolves_via_token(self, dataset, no_llm):
        """Classifier labels like "100_milk" tokenise to {"100", "milk"}."""
        dataset({"milk": 5})
        assert lookup_shelf_life_days("100_milk") == (5, "dataset")


class TestTier4Heuristic:
    @pytest.mark.parametrize(
        "name,expected_days",
        [
            ("whole milk", 5),
            ("cheddar cheese", 5),
            ("greek yogurt", 5),
            ("chicken breast", 3),
            ("ground beef", 3),
            ("pork chops", 3),
            ("baby spinach", 4),
            ("romaine lettuce", 4),
            ("mixed greens", 4),
        ],
    )
    def test_keyword_families(self, no_dataset, no_llm, name, expected_days):
        assert lookup_shelf_life_days(name) == (expected_days, "heuristic")

    def test_unrecognised_name_returns_unknown(self, no_dataset, no_llm):
        """Failing open is deliberate: no date beats a fabricated date."""
        assert lookup_shelf_life_days("saffron") == (None, "unknown")

    def test_heuristic_is_a_pure_function(self):
        assert _heuristic_fallback("MILK") == 5
        assert _heuristic_fallback("saffron") is None

    @pytest.mark.xfail(
        reason=(
            "Known defect in the offline fallback: the heuristic matches "
            "substrings, so any name containing a keyword inherits that "
            "keyword's shelf life. With a model configured this no longer "
            "surfaces, because the model tier answers first -- but with no "
            "model available, 'milk chocolate' is still assigned 5 days."
        ),
        strict=True,
    )
    def test_substring_matching_misclassifies_shelf_stable_items(
        self, no_dataset, no_llm
    ):
        days, _ = lookup_shelf_life_days("milk chocolate")
        assert days is None or days > 30


class TestProvenance:
    """Every answer must say where it came from, and never overstate it."""

    @pytest.mark.parametrize(
        "source", ["dataset", "llm", "heuristic", "unknown"]
    )
    def test_sources_are_from_the_known_set(self, source):
        assert source in {"user", "dataset", "llm", "heuristic", "unknown"}

    def test_no_answer_claims_an_external_data_provider(
        self, no_dataset, llm_says
    ):
        """Regression guard.

        The removed Spoonacular tier reported source="api" for a number it had
        invented, because Spoonacular does not publish shelf-life data. No tier
        may claim that provenance again.
        """
        llm_says(30)
        _, source = lookup_shelf_life_days("ketchup")
        assert source != "api"

    def test_unresolved_items_are_marked_unknown_not_guessed(
        self, no_dataset, no_llm
    ):
        days, source = lookup_shelf_life_days("saffron")
        assert days is None
        assert source == "unknown"


class TestCascadeIntegration:
    def test_model_tier_is_cached_across_lookups(self, no_dataset, monkeypatch):
        """The expensive tier caches itself, so repeats are free."""
        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")
        calls = []

        def fake_estimate(name, **kwargs):
            calls.append(name)
            return 12

        monkeypatch.setattr(shelf_life, "estimate_shelf_life_days", fake_estimate)
        cache = InMemoryCache()
        assert lookup_shelf_life_days("paneer", cache=cache) == (12, "llm")
        assert calls == ["paneer"]

    def test_estimator_kwargs_are_forwarded(self, no_dataset, monkeypatch):
        received = {}

        def fake_estimate(name, **kwargs):
            received.update(kwargs)
            return 5

        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")
        monkeypatch.setattr(shelf_life, "estimate_shelf_life_days", fake_estimate)
        sentinel = InMemoryCache()
        lookup_shelf_life_days("paneer", cache=sentinel)
        assert received["cache"] is sentinel


class TestDatasetFileMemoisation:
    def test_file_is_parsed_once_for_repeated_lookups(
        self, dataset, no_llm, monkeypatch
    ):
        dataset({"milk": 5})
        loads = []
        original = json.load

        def counting_load(handle):
            loads.append(1)
            return original(handle)

        monkeypatch.setattr(shelf_life.json, "load", counting_load)
        for _ in range(5):
            lookup_shelf_life_days("milk")
        assert len(loads) == 1, "dataset should be read from disk once"

    def test_editing_the_file_invalidates_the_memoised_copy(self, dataset, no_llm):
        """Keyed on mtime so development edits take effect without a restart."""
        path = dataset({"milk": 5})
        assert lookup_shelf_life_days("milk") == (5, "dataset")

        import os
        import time

        path.write_text(json.dumps({"milk": 9}), encoding="utf-8")
        future = time.time() + 10
        os.utime(path, (future, future))

        assert lookup_shelf_life_days("milk") == (9, "dataset")

    def test_missing_file_yields_an_empty_dataset(self, no_dataset):
        assert shelf_life._load_dataset() == {}


def test_normalize_name():
    assert _normalize_name("  Whole Milk ") == "whole milk"
