"""Tests for the vision-LLM backend.

Two concerns dominate: a model's reply is untrusted input and must be parsed
defensively, and every call costs money so identical images must be served from
cache.
"""

import json
from pathlib import Path

import pytest

from app.services.cache import InMemoryCache
from app.services.classifier import Classifier, Detection
from app.services.classifier import vision_llm as vision_module
from app.services.classifier.vision_llm import (
    CACHE_NAMESPACE,
    VisionClassificationError,
    VisionLLMClassifier,
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
def image(tmp_path):
    path = tmp_path / "fridge.jpg"
    path.write_bytes(b"pretend-jpeg-bytes")
    return path


@pytest.fixture
def backend(monkeypatch):
    """Builds a classifier wired to a fake client and an isolated cache."""
    monkeypatch.setattr(vision_module.settings, "openai_api_key", "test-key")

    def _build(reply, error=None, cache=None):
        content = reply if isinstance(reply, str) else json.dumps(reply)
        client = FakeClient(content=content, error=error)
        classifier = VisionLLMClassifier(
            cache=cache if cache is not None else InMemoryCache(),
            client_factory=lambda: client,
        )
        return classifier, client

    return _build


class TestHappyPath:
    def test_single_item_is_parsed(self, backend, image):
        classifier, _ = backend({"items": [{"label": "milk", "confidence": 0.93}]})
        assert classifier.detect(image) == [Detection("milk", 0.93)]

    def test_multiple_items_are_all_returned(self, backend, image):
        """The reason for choosing this backend: a whole shelf in one photo."""
        classifier, _ = backend(
            {
                "items": [
                    {"label": "milk", "confidence": 0.95},
                    {"label": "paneer", "confidence": 0.88},
                    {"label": "tomato", "confidence": 0.79},
                ]
            }
        )
        detections = classifier.detect(image)
        assert [d.label for d in detections] == ["milk", "paneer", "tomato"]

    def test_empty_items_list_is_respected(self, backend, image):
        classifier, _ = backend({"items": []})
        assert classifier.detect(image) == []

    def test_labels_are_normalised(self, backend, image):
        """Names must match the shelf-life table, which is lowercase."""
        classifier, _ = backend({"items": [{"label": "  Whole MILK ", "confidence": 0.9}]})
        assert classifier.detect(image)[0].label == "whole milk"

    def test_satisfies_the_protocol(self):
        assert isinstance(VisionLLMClassifier(), Classifier)


class TestRequestShape:
    def test_image_is_sent_as_a_base64_data_url(self, backend, image):
        classifier, client = backend({"items": []})
        classifier.detect(image)
        content = client.chat.completions.calls[0]["messages"][1]["content"]
        image_part = next(part for part in content if part["type"] == "image_url")
        assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_structured_output_is_requested(self, backend, image):
        """Forcing JSON removes the need to salvage prose around the payload."""
        classifier, client = backend({"items": []})
        classifier.detect(image)
        assert client.chat.completions.calls[0]["response_format"] == {
            "type": "json_object"
        }

    def test_configured_vision_model_is_used(self, backend, image, monkeypatch):
        monkeypatch.setattr(vision_module.settings, "vision_model", "gpt-4o")
        classifier, client = backend({"items": []})
        classifier.detect(image)
        assert client.chat.completions.calls[0]["model"] == "gpt-4o"

    def test_png_gets_the_right_mime_type(self, backend, tmp_path):
        path = tmp_path / "shelf.png"
        path.write_bytes(b"pretend-png")
        classifier, client = backend({"items": []})
        classifier.detect(path)
        content = client.chat.completions.calls[0]["messages"][1]["content"]
        image_part = next(part for part in content if part["type"] == "image_url")
        assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


class TestDefensiveParsing:
    """A model's output is untrusted input."""

    def test_invalid_json_yields_nothing(self, backend, image):
        classifier, _ = backend("this is not json")
        assert classifier.detect(image) == []

    def test_missing_items_key_yields_nothing(self, backend, image):
        classifier, _ = backend({"results": [{"label": "milk", "confidence": 0.9}]})
        assert classifier.detect(image) == []

    def test_items_of_the_wrong_type_yield_nothing(self, backend, image):
        classifier, _ = backend({"items": "milk"})
        assert classifier.detect(image) == []

    def test_empty_reply_yields_nothing(self, backend, image):
        classifier, _ = backend("")
        assert classifier.detect(image) == []

    def test_entries_missing_a_label_are_skipped(self, backend, image):
        classifier, _ = backend(
            {"items": [{"confidence": 0.9}, {"label": "milk", "confidence": 0.8}]}
        )
        assert classifier.detect(image) == [Detection("milk", 0.8)]

    def test_blank_labels_are_skipped(self, backend, image):
        classifier, _ = backend({"items": [{"label": "   ", "confidence": 0.9}]})
        assert classifier.detect(image) == []

    def test_non_numeric_confidence_is_skipped(self, backend, image):
        classifier, _ = backend({"items": [{"label": "milk", "confidence": "high"}]})
        assert classifier.detect(image) == []

    def test_missing_confidence_defaults_to_zero(self, backend, image):
        """Zero keeps it below the gate, so the user is asked to confirm."""
        classifier, _ = backend({"items": [{"label": "milk"}]})
        assert classifier.detect(image) == [Detection("milk", 0.0)]

    @pytest.mark.parametrize(
        "raw,expected", [(1.4, 1.0), (-0.3, 0.0), (0.55, 0.55), (1.0, 1.0)]
    )
    def test_confidence_is_clamped_to_a_probability(
        self, backend, image, raw, expected
    ):
        """Models occasionally return values outside 0..1."""
        classifier, _ = backend({"items": [{"label": "milk", "confidence": raw}]})
        assert classifier.detect(image)[0].confidence == pytest.approx(expected)

    def test_non_dict_entries_are_skipped(self, backend, image):
        classifier, _ = backend({"items": ["milk", {"label": "bread", "confidence": 0.9}]})
        assert classifier.detect(image) == [Detection("bread", 0.9)]

    def test_a_json_list_at_the_top_level_yields_nothing(self, backend, image):
        classifier, _ = backend([{"label": "milk", "confidence": 0.9}])
        assert classifier.detect(image) == []


class TestCaching:
    def test_identical_image_is_only_sent_once(self, backend, image):
        """Every avoided call is money not spent."""
        cache = InMemoryCache()
        classifier, client = backend(
            {"items": [{"label": "milk", "confidence": 0.9}]}, cache=cache
        )
        first = classifier.detect(image)
        second = classifier.detect(image)
        assert first == second == [Detection("milk", 0.9)]
        assert len(client.chat.completions.calls) == 1
        assert cache.stats.hits == 1

    def test_empty_result_is_also_cached(self, backend, image):
        """A photo of nothing costs the same to analyse as a photo of a shelf."""
        cache = InMemoryCache()
        classifier, client = backend({"items": []}, cache=cache)
        classifier.detect(image)
        classifier.detect(image)
        assert len(client.chat.completions.calls) == 1

    def test_different_images_are_sent_separately(self, backend, tmp_path):
        cache = InMemoryCache()
        classifier, client = backend({"items": []}, cache=cache)
        for name, content in [("a.jpg", b"first"), ("b.jpg", b"second")]:
            path = tmp_path / name
            path.write_bytes(content)
            classifier.detect(path)
        assert len(client.chat.completions.calls) == 2

    def test_cache_key_is_content_based_not_name_based(self, backend, tmp_path):
        """The same photo saved under two names must share one cache entry."""
        cache = InMemoryCache()
        classifier, client = backend({"items": []}, cache=cache)
        for name in ["first.jpg", "second.jpg"]:
            path = tmp_path / name
            path.write_bytes(b"identical-bytes")
            classifier.detect(path)
        assert len(client.chat.completions.calls) == 1

    def test_changing_the_model_invalidates_the_entry(
        self, backend, image, monkeypatch
    ):
        """A different model may legitimately give a different answer."""
        cache = InMemoryCache()
        classifier, client = backend({"items": []}, cache=cache)
        classifier.detect(image)
        monkeypatch.setattr(vision_module.settings, "vision_model", "gpt-4o")
        classifier.detect(image)
        assert len(client.chat.completions.calls) == 2

    def test_cached_detections_round_trip_through_json(self, backend, image):
        cache = InMemoryCache()
        classifier, _ = backend(
            {"items": [{"label": "milk", "confidence": 0.9}]}, cache=cache
        )
        classifier.detect(image)
        stored = cache.get(CACHE_NAMESPACE, classifier._cache_key(image.read_bytes()))
        assert stored == [{"label": "milk", "confidence": 0.9, "box": None}]


class TestFailureModes:
    def test_missing_api_key_detects_nothing(self, monkeypatch, image):
        """Falls through to manual labelling instead of failing the request."""
        monkeypatch.setattr(vision_module.settings, "openai_api_key", None)
        assert VisionLLMClassifier(cache=InMemoryCache()).detect(image) == []

    def test_unreadable_file_detects_nothing(self, backend, tmp_path):
        classifier, _ = backend({"items": []})
        assert classifier.detect(tmp_path / "does-not-exist.jpg") == []

    def test_provider_error_is_translated(self, backend, image):
        import httpx
        from openai import APIConnectionError

        classifier, _ = backend(
            {"items": []},
            error=APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com")
            ),
        )
        with pytest.raises(VisionClassificationError):
            classifier.detect(image)

    def test_failed_call_is_not_cached(self, backend, image):
        import httpx
        from openai import APIConnectionError

        cache = InMemoryCache()
        classifier, _ = backend(
            {"items": []},
            error=APIConnectionError(
                request=httpx.Request("POST", "https://api.openai.com")
            ),
            cache=cache,
        )
        with pytest.raises(VisionClassificationError):
            classifier.detect(image)
        assert cache.stats.writes == 0, "a failure must not poison the cache"
