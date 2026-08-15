"""Tests for model-based shelf-life resolution.

Two concerns: the reply is untrusted input and must be validated, and the anchor
must be real. An anchor naming something the model was never shown would look
like inherited human judgment while being pure invention.
"""

import json

import pytest

from app.services import llm_estimator as estimator_module
from app.services.llm_estimator import (
    MAX_DAYS,
    MIN_DAYS,
    Resolution,
    _format_candidates,
    resolve_shelf_life,
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
def resolver(monkeypatch):
    monkeypatch.setattr(estimator_module.settings, "openai_api_key", "test-key")

    def _build(reply, error=None):
        content = reply if isinstance(reply, str) or reply is None else json.dumps(reply)
        client = FakeClient(content=content, error=error)

        def call(name="paneer", candidates=None):
            return resolve_shelf_life(
                name, candidates=candidates, client_factory=lambda: client
            )

        return call, client

    return _build


class TestPlainEstimate:
    def test_returns_the_number(self, resolver):
        call, _ = resolver({"days": 21, "anchor": None})
        assert call() == Resolution(days=21)

    def test_long_shelf_life_is_accepted(self, resolver):
        """The case the removed Spoonacular tier got wrong."""
        call, _ = resolver({"days": 365, "anchor": None})
        assert call("ketchup").days == 365

    def test_null_days_is_treated_as_no_answer(self, resolver):
        call, _ = resolver({"days": None, "anchor": None})
        assert call() is None


class TestAnchoring:
    def test_anchor_is_captured_with_its_value(self, resolver):
        call, _ = resolver({"days": 4, "anchor": "spinach"})
        result = call("baby spinach", candidates={"spinach": 4})
        assert result == Resolution(days=4, anchor="spinach", anchor_days=4)
        assert result.is_anchored

    def test_anchoring_makes_variants_agree(self, resolver):
        """The whole point: "baby spinach" inherits the curated "spinach" value."""
        call, _ = resolver({"days": 4, "anchor": "spinach"})
        assert call("baby spinach", candidates={"spinach": 4}).days == 4

    def test_anchor_is_matched_case_insensitively(self, resolver):
        call, _ = resolver({"days": 4, "anchor": "Spinach"})
        assert call("baby spinach", candidates={"spinach": 4}).anchor == "spinach"

    def test_invented_anchor_is_discarded_but_the_number_kept(self, resolver):
        """A hallucinated reference must not be recorded as a real one."""
        call, _ = resolver({"days": 30, "anchor": "kale"})
        result = call("baby spinach", candidates={"spinach": 4})
        assert result == Resolution(days=30)
        assert result.anchor is None

    def test_non_string_anchor_is_discarded(self, resolver):
        call, _ = resolver({"days": 30, "anchor": 7})
        assert call("x", candidates={"spinach": 4}).anchor is None

    def test_missing_anchor_key_is_treated_as_unanchored(self, resolver):
        call, _ = resolver({"days": 30})
        assert call("x", candidates={"spinach": 4}).anchor is None

    def test_unrelated_item_is_not_forced_to_anchor(self, resolver):
        """The prompt's counter-example: milk chocolate is not a milk."""
        call, _ = resolver({"days": 365, "anchor": None})
        result = call("milk chocolate", candidates={"milk": 5})
        assert result.days == 365
        assert result.anchor is None


class TestCandidatePresentation:
    def test_candidates_are_listed_for_the_model(self):
        rendered = _format_candidates({"spinach": 4, "milk": 5})
        assert "- spinach: 4 days" in rendered
        assert "- milk: 5 days" in rendered

    def test_absent_candidates_are_stated_explicitly(self):
        assert "No similar items" in _format_candidates({})

    def test_candidates_are_sent_as_a_system_message(self, resolver):
        call, client = resolver({"days": 4, "anchor": "spinach"})
        call("baby spinach", candidates={"spinach": 4})
        messages = client.chat.completions.calls[0]["messages"]
        assert [message["role"] for message in messages] == [
            "system",
            "system",
            "user",
        ]
        assert "spinach: 4 days" in messages[1]["content"]
        assert "baby spinach" in messages[2]["content"]

    def test_structured_output_is_requested(self, resolver):
        call, client = resolver({"days": 7, "anchor": None})
        call()
        assert client.chat.completions.calls[0]["response_format"] == {
            "type": "json_object"
        }

    def test_configured_model_is_used(self, resolver, monkeypatch):
        monkeypatch.setattr(estimator_module.settings, "openai_model", "gpt-4o")
        call, client = resolver({"days": 7, "anchor": None})
        call()
        assert client.chat.completions.calls[0]["model"] == "gpt-4o"


class TestValidation:
    def test_invalid_json_yields_none(self, resolver):
        call, _ = resolver("about a week")
        assert call() is None

    def test_missing_days_key_yields_none(self, resolver):
        call, _ = resolver({"estimate": 7})
        assert call() is None

    def test_non_numeric_days_yields_none(self, resolver):
        call, _ = resolver({"days": "seven"})
        assert call() is None

    def test_boolean_is_rejected(self, resolver):
        """True would otherwise int() to 1 and look like a one-day shelf life."""
        call, _ = resolver({"days": True})
        assert call() is None

    def test_float_is_truncated_to_whole_days(self, resolver):
        call, _ = resolver({"days": 7.9})
        assert call().days == 7

    def test_top_level_list_yields_none(self, resolver):
        call, _ = resolver([{"days": 7}])
        assert call() is None

    def test_empty_reply_yields_none(self, resolver):
        call, _ = resolver(None)
        assert call() is None

    @pytest.mark.parametrize("days", [0, -5, MAX_DAYS + 1, 100000])
    def test_implausible_values_are_rejected(self, resolver, days):
        call, _ = resolver({"days": days})
        assert call() is None

    @pytest.mark.parametrize("days", [MIN_DAYS, 7, MAX_DAYS])
    def test_boundary_values_are_accepted(self, resolver, days):
        call, _ = resolver({"days": days})
        assert call().days == days


class TestUnavailable:
    def test_no_api_key_skips_the_call(self, monkeypatch):
        monkeypatch.setattr(estimator_module.settings, "openai_api_key", None)
        assert resolve_shelf_life("paneer") is None

    def test_provider_error_yields_none(self, resolver):
        import httpx
        from openai import APIConnectionError

        call, _ = resolver(
            {"days": 7},
            error=APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com")
            ),
        )
        assert call() is None
