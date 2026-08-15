"""Tests for the four-tier shelf-life inference cascade.

The cascade is the core domain logic of the app: given an item name and no
user-supplied date, decide how many days it keeps and record how confident we
are in that number. Each tier is asserted independently, along with the
ordering guarantees between them.
"""

import json

import pytest

from app.core import config
from app.services import shelf_life
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
        return path

    return _write


@pytest.fixture
def no_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config.settings, "shelf_life_path", str(tmp_path / "missing.json")
    )


class TestTier1ExactMatch:
    def test_exact_key_returns_dataset_value(self, dataset):
        dataset({"milk": 5, "bread": 7})
        assert lookup_shelf_life_days("milk") == (5, "dataset")

    def test_lookup_is_case_and_whitespace_insensitive(self, dataset):
        dataset({"milk": 5})
        assert lookup_shelf_life_days("  MiLk  ") == (5, "dataset")


class TestTier2TokenMatch:
    def test_multiword_name_matches_on_token(self, dataset):
        """'Whole wheat bread' is not a key, but 'bread' is."""
        dataset({"bread": 7})
        assert lookup_shelf_life_days("Whole wheat bread") == (7, "dataset")

    def test_longest_matching_key_wins(self, dataset):
        """A more specific multi-word key must beat a shorter generic token.

        The looked-up name is deliberately not itself a key, otherwise tier 1
        would answer and this branch would never execute.
        """
        dataset({"bread": 7, "wheat bread": 3})
        assert lookup_shelf_life_days("whole wheat bread") == (3, "dataset")

    def test_multiword_key_must_appear_contiguously(self, dataset):
        """'wheat bread' should not match a name where the words are separated."""
        dataset({"wheat bread": 3})
        assert lookup_shelf_life_days("wheat flour and white bread") == (
            None,
            "unknown",
        )

    def test_partial_token_does_not_match_the_dataset(self, dataset):
        """'milkshake' is a single token, so it must not match the 'milk' key.

        It still resolves, but via the heuristic tier rather than the dataset --
        the source is what proves tier 2 declined to match.
        """
        dataset({"milk": 5})
        _, source = lookup_shelf_life_days("milkshake")
        assert source == "heuristic"

    def test_model_style_sku_label_resolves_via_token(self, dataset):
        """The classifier emits labels like '100_milk'; tokenizing bridges them."""
        dataset({"milk": 5})
        assert lookup_shelf_life_days("100_milk") == (5, "dataset")


class TestTier3ExternalApi:
    def test_api_hit_returns_api_source(self, no_dataset, monkeypatch):
        monkeypatch.setattr(config.settings, "shelf_life_api_key", "test-key")

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": [{"name": "paneer"}]}

        monkeypatch.setattr(
            shelf_life.requests, "get", lambda *a, **k: FakeResponse()
        )
        assert lookup_shelf_life_days("paneer") == (5, "api")

    def test_api_is_skipped_when_no_key_configured(self, no_dataset):
        """Without a key the cascade must not attempt a network call.

        The autouse block_outbound_http fixture turns any real request into a
        failure, so reaching 'unknown' proves no call was made.
        """
        assert lookup_shelf_life_days("dragonfruit") == (None, "unknown")

    def test_network_failure_falls_through_to_heuristic(
        self, no_dataset, monkeypatch
    ):
        import requests

        monkeypatch.setattr(config.settings, "shelf_life_api_key", "test-key")

        def _boom(*args, **kwargs):
            raise requests.RequestException("network down")

        monkeypatch.setattr(shelf_life.requests, "get", _boom)
        # 'chicken' is covered by the heuristic tier.
        assert lookup_shelf_life_days("chicken thighs") == (3, "heuristic")

    def test_api_returning_no_results_falls_through(self, no_dataset, monkeypatch):
        monkeypatch.setattr(config.settings, "shelf_life_api_key", "test-key")

        class EmptyResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": []}

        monkeypatch.setattr(
            shelf_life.requests, "get", lambda *a, **k: EmptyResponse()
        )
        assert lookup_shelf_life_days("dragonfruit") == (None, "unknown")


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
    def test_keyword_families(self, no_dataset, name, expected_days):
        assert lookup_shelf_life_days(name) == (expected_days, "heuristic")

    def test_unrecognised_name_returns_unknown(self, no_dataset):
        """Failing open is deliberate: no date beats a fabricated date."""
        assert lookup_shelf_life_days("saffron") == (None, "unknown")

    def test_heuristic_is_a_pure_function(self):
        assert _heuristic_fallback("MILK") == 5
        assert _heuristic_fallback("saffron") is None

    @pytest.mark.xfail(
        reason=(
            "Known defect: the heuristic matches substrings, so any name "
            "containing a keyword inherits that keyword's shelf life. "
            "'milk chocolate' keeps for months, not 5 days."
        ),
        strict=True,
    )
    def test_substring_matching_misclassifies_shelf_stable_items(self, no_dataset):
        days, _ = lookup_shelf_life_days("milk chocolate")
        assert days is None or days > 30


class TestTierOrdering:
    def test_dataset_wins_over_heuristic(self, dataset):
        """A curated value must beat a keyword guess for the same item."""
        dataset({"milk": 99})
        assert lookup_shelf_life_days("milk") == (99, "dataset")

    def test_dataset_wins_over_api(self, dataset, monkeypatch):
        dataset({"paneer": 12})
        monkeypatch.setattr(config.settings, "shelf_life_api_key", "test-key")
        # The autouse HTTP guard proves the API tier was never reached.
        assert lookup_shelf_life_days("paneer") == (12, "dataset")

    def test_api_wins_over_heuristic(self, no_dataset, monkeypatch):
        monkeypatch.setattr(config.settings, "shelf_life_api_key", "test-key")

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": [{"name": "milk"}]}

        monkeypatch.setattr(
            shelf_life.requests, "get", lambda *a, **k: FakeResponse()
        )
        _, source = lookup_shelf_life_days("whole milk")
        assert source == "api"


def test_normalize_name():
    assert _normalize_name("  Whole Milk ") == "whole milk"
