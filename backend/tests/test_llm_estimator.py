"""Tests for LLM-based shelf-life estimation.

Two concerns: a model's answer is untrusted and must be validated, and each call
costs money so every answer -- including "I don't know" -- must be cached.
"""

import json

import pytest

from app.services import llm_estimator as estimator_module
from app.services.cache import InMemoryCache
from app.services.llm_estimator import (
    CACHE_NAMESPACE,
    MAX_DAYS,
    MIN_DAYS,
    estimate_shelf_life_days,
)


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeCompletion:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, content, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return FakeCompletion(self.content)


class FakeClient:
    def __init__(self, content=None, error=None):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(content, error)


@pytest.fixture
def estimator(monkeypatch):
    monkeypatch.setattr(estimator_module.settings, "openai_api_key", "test-key")

    def _build(reply, error=None, cache=None):
        content = reply if isinstance(reply, str) or reply is None else json.dumps(reply)
        client = FakeClient(content=content, error=error)
        active_cache = cache if cache is not None else InMemoryCache()

        def call(name="paneer"):
            return estimate_shelf_life_days(
                name, cache=active_cache, client_factory=lambda: client
            )

        return call, client, active_cache

    return _build


class TestHappyPath:
    def test_returns_the_estimated_days(self, estimator):
        call, _, _ = estimator({"days": 21})
        assert call() == 21

    def test_accepts_a_long_shelf_life(self, estimator):
        """The case the old Spoonacular tier got wrong: ketchup is not 5 days."""
        call, _, _ = estimator({"days": 365})
        assert call("ketchup") == 365

    def test_item_name_is_sent_to_the_model(self, estimator):
        call, client, _ = estimator({"days": 7})
        call("whole wheat bread")
        user_message = client.chat.completions.calls[0]["messages"][1]["content"]
        assert "whole wheat bread" in user_message

    def test_structured_output_is_requested(self, estimator):
        call, client, _ = estimator({"days": 7})
        call()
        assert client.chat.completions.calls[0]["response_format"] == {
            "type": "json_object"
        }

    def test_configured_model_is_used(self, estimator, monkeypatch):
        monkeypatch.setattr(estimator_module.settings, "openai_model", "gpt-4o")
        call, client, _ = estimator({"days": 7})
        call()
        assert client.chat.completions.calls[0]["model"] == "gpt-4o"


class TestValidation:
    def test_explicit_null_is_accepted_as_unknown(self, estimator):
        call, _, _ = estimator({"days": None})
        assert call() is None

    def test_invalid_json_yields_none(self, estimator):
        call, _, _ = estimator("about a week")
        assert call() is None

    def test_missing_days_key_yields_none(self, estimator):
        call, _, _ = estimator({"estimate": 7})
        assert call() is None

    def test_non_numeric_days_yields_none(self, estimator):
        call, _, _ = estimator({"days": "seven"})
        assert call() is None

    def test_boolean_is_rejected(self, estimator):
        """True would otherwise int() to 1 and look like a one-day shelf life."""
        call, _, _ = estimator({"days": True})
        assert call() is None

    def test_float_is_truncated_to_whole_days(self, estimator):
        call, _, _ = estimator({"days": 7.9})
        assert call() == 7

    def test_top_level_list_yields_none(self, estimator):
        call, _, _ = estimator([{"days": 7}])
        assert call() is None

    def test_empty_reply_yields_none(self, estimator):
        call, _, _ = estimator(None)
        assert call() is None

    @pytest.mark.parametrize("days", [0, -5, MAX_DAYS + 1, 100000])
    def test_implausible_values_are_rejected(self, estimator, days):
        call, _, _ = estimator({"days": days})
        assert call() is None

    @pytest.mark.parametrize("days", [MIN_DAYS, 7, MAX_DAYS])
    def test_boundary_values_are_accepted(self, estimator, days):
        call, _, _ = estimator({"days": days})
        assert call() == days


class TestCaching:
    def test_repeated_estimate_calls_the_model_once(self, estimator):
        cache = InMemoryCache()
        call, client, _ = estimator({"days": 21}, cache=cache)
        assert call("paneer") == 21
        assert call("paneer") == 21
        assert len(client.chat.completions.calls) == 1
        assert cache.stats.hits == 1

    def test_unknown_answers_are_also_cached(self, estimator):
        """Otherwise every unanswerable item would be re-asked on every add."""
        cache = InMemoryCache()
        call, client, _ = estimator({"days": None}, cache=cache)
        assert call("mystery") is None
        assert call("mystery") is None
        assert len(client.chat.completions.calls) == 1

    def test_estimates_are_stable_across_calls(self, estimator):
        """A cached answer removes the nondeterminism of asking twice."""
        cache = InMemoryCache()
        call, _, _ = estimator({"days": 21}, cache=cache)
        assert len({call("paneer") for _ in range(5)}) == 1

    def test_different_items_are_asked_separately(self, estimator):
        cache = InMemoryCache()
        call, client, _ = estimator({"days": 21}, cache=cache)
        call("paneer")
        call("tofu")
        assert len(client.chat.completions.calls) == 2

    def test_changing_the_model_invalidates_the_entry(self, estimator, monkeypatch):
        cache = InMemoryCache()
        call, client, _ = estimator({"days": 21}, cache=cache)
        call("paneer")
        monkeypatch.setattr(estimator_module.settings, "openai_model", "gpt-4o")
        call("paneer")
        assert len(client.chat.completions.calls) == 2

    def test_cached_shape_is_a_dict(self, estimator):
        cache = InMemoryCache()
        call, _, _ = estimator({"days": 21}, cache=cache)
        call("paneer")
        stored = cache.get(
            CACHE_NAMESPACE, f"{estimator_module.settings.openai_model}:paneer"
        )
        assert stored == {"days": 21}

    def test_malformed_cache_entry_is_tolerated(self, estimator):
        cache = InMemoryCache()
        cache.set(
            CACHE_NAMESPACE,
            f"{estimator_module.settings.openai_model}:paneer",
            "not-a-dict",
        )
        call, client, _ = estimator({"days": 21}, cache=cache)
        assert call("paneer") is None
        assert client.chat.completions.calls == []


class TestUnavailable:
    def test_no_api_key_skips_the_call_entirely(self, monkeypatch):
        """The cascade has cheaper fallbacks, so this is not an error."""
        monkeypatch.setattr(estimator_module.settings, "openai_api_key", None)
        assert estimate_shelf_life_days("paneer", cache=InMemoryCache()) is None

    def test_provider_error_yields_none(self, estimator):
        import httpx
        from openai import APIConnectionError

        call, _, _ = estimator(
            {"days": 7},
            error=APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com")
            ),
        )
        assert call() is None

    def test_provider_error_is_not_cached(self, estimator):
        """A transient outage must not persist as "unknown" for weeks.

        The distinction between "the model cannot say" and "the call failed" only
        matters because answers are long-lived; caching an outage would outlast it.
        """
        import httpx
        from openai import APIConnectionError

        cache = InMemoryCache()
        call, _, _ = estimator(
            {"days": 7},
            error=APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com")
            ),
            cache=cache,
        )
        call("paneer")
        assert cache.stats.writes == 0

    def test_recovery_after_an_outage_is_immediate(self, estimator, monkeypatch):
        """Because the failure was not cached, the next call asks again."""
        import httpx
        from openai import APIConnectionError

        cache = InMemoryCache()
        failing, _, _ = estimator(
            {"days": 7},
            error=APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com")
            ),
            cache=cache,
        )
        assert failing("paneer") is None

        recovered, _, _ = estimator({"days": 21}, cache=cache)
        assert recovered("paneer") == 21

    def test_model_saying_it_cannot_tell_is_cached(self, estimator):
        """The opposite case: a real answer of "unknown" is worth remembering."""
        cache = InMemoryCache()
        call, client, _ = estimator({"days": None}, cache=cache)
        call("mystery")
        call("mystery")
        assert cache.stats.writes == 1
        assert len(client.chat.completions.calls) == 1
