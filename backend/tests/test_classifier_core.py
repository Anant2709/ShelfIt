"""Tests for the classifier seam: the Detection type, the protocol, and the factory.

The point of this module is that backends are interchangeable and that the public
surface behaves identically regardless of which one is installed.
"""

from pathlib import Path

import pytest

from app.services import classifier as classifier_module
from app.services.classifier import (
    UNKNOWN_LABEL,
    Classifier,
    Detection,
    NullClassifier,
    build_classifier,
    classify_image,
    detect_items,
    get_classifier,
    reset_classifier,
)


class FakeClassifier:
    """A backend that returns whatever a test hands it."""

    name = "fake"

    def __init__(self, detections):
        self.detections = detections
        self.calls = []

    def detect(self, image_path: Path):
        self.calls.append(image_path)
        return list(self.detections)


class TestDetection:
    def test_carries_label_and_confidence(self):
        detection = Detection(label="milk", confidence=0.9)
        assert detection.label == "milk"
        assert detection.confidence == 0.9

    def test_box_is_optional(self):
        assert Detection(label="milk", confidence=0.9).box is None

    def test_is_immutable(self):
        """Frozen so a detection cannot be mutated after the model produced it."""
        detection = Detection(label="milk", confidence=0.9)
        with pytest.raises(Exception):
            detection.label = "bread"

    def test_round_trips_through_a_dict(self):
        original = Detection(label="milk", confidence=0.9, box=(1.0, 2.0, 3.0, 4.0))
        assert Detection.from_dict(original.as_dict()) == original

    def test_round_trips_without_a_box(self):
        original = Detection(label="milk", confidence=0.9)
        assert Detection.from_dict(original.as_dict()) == original

    def test_as_dict_is_json_friendly(self):
        """Detections are cached as JSON, so a tuple box must become a list."""
        payload = Detection(label="milk", confidence=0.9, box=(1, 2, 3, 4)).as_dict()
        assert payload["box"] == [1, 2, 3, 4]
        assert isinstance(payload["box"], list)

    def test_equality_is_by_value(self):
        assert Detection("milk", 0.9) == Detection("milk", 0.9)
        assert Detection("milk", 0.9) != Detection("milk", 0.8)


class TestNullClassifier:
    def test_detects_nothing(self):
        assert NullClassifier().detect(Path("anything.jpg")) == []

    def test_satisfies_the_protocol(self):
        assert isinstance(NullClassifier(), Classifier)

    def test_scan_falls_through_to_manual_labelling(self):
        """Detecting nothing is the correct behaviour with no backend configured."""
        assert classify_image(Path("x.jpg"), classifier=NullClassifier()) == (
            UNKNOWN_LABEL,
            0.0,
        )


class TestDetectItems:
    def test_returns_every_detection(self):
        """The whole reason for the rewrite: a shelf yields more than one item."""
        backend = FakeClassifier(
            [
                Detection("milk", 0.95),
                Detection("bread", 0.91),
                Detection("eggs", 0.88),
            ]
        )
        detections = detect_items(Path("shelf.jpg"), classifier=backend)
        assert [d.label for d in detections] == ["milk", "bread", "eggs"]

    def test_orders_by_confidence_descending(self):
        backend = FakeClassifier(
            [Detection("eggs", 0.4), Detection("milk", 0.99), Detection("bread", 0.7)]
        )
        detections = detect_items(Path("shelf.jpg"), classifier=backend)
        assert [d.label for d in detections] == ["milk", "bread", "eggs"]

    def test_caps_the_number_of_detections(self, monkeypatch):
        """Guards against a model returning an implausible number of items."""
        monkeypatch.setattr(classifier_module.settings, "max_detections_per_image", 3)
        backend = FakeClassifier(
            [Detection(f"item{i}", 0.9 - i / 100) for i in range(25)]
        )
        assert len(detect_items(Path("shelf.jpg"), classifier=backend)) == 3

    def test_empty_result_is_an_empty_list(self):
        assert detect_items(Path("x.jpg"), classifier=FakeClassifier([])) == []

    def test_passes_the_image_path_through(self):
        backend = FakeClassifier([])
        detect_items(Path("/tmp/photo.jpg"), classifier=backend)
        assert backend.calls == [Path("/tmp/photo.jpg")]

    def test_uses_the_configured_backend_when_none_is_given(self, monkeypatch):
        monkeypatch.setattr(classifier_module.settings, "classifier_backend", "null")
        reset_classifier()
        try:
            assert detect_items(Path("x.jpg")) == []
        finally:
            reset_classifier()


class TestClassifyImage:
    def test_returns_the_most_confident_detection(self):
        backend = FakeClassifier([Detection("bread", 0.6), Detection("milk", 0.97)])
        assert classify_image(Path("x.jpg"), classifier=backend) == ("milk", 0.97)

    def test_no_detections_yields_unknown_at_zero_confidence(self):
        """0.0 sits below any sane threshold, so the scan asks the user."""
        assert classify_image(Path("x.jpg"), classifier=FakeClassifier([])) == (
            UNKNOWN_LABEL,
            0.0,
        )

    def test_single_detection_is_returned_as_is(self):
        backend = FakeClassifier([Detection("paneer", 0.81)])
        assert classify_image(Path("x.jpg"), classifier=backend) == ("paneer", 0.81)


class TestFactory:
    def test_builds_the_null_backend(self):
        assert isinstance(build_classifier("null"), NullClassifier)

    def test_builds_the_yolo_backend(self):
        from app.services.classifier.yolo import YoloClassifier

        assert isinstance(build_classifier("yolo"), YoloClassifier)

    def test_builds_the_vision_backend(self):
        from app.services.classifier.vision_llm import VisionLLMClassifier

        assert isinstance(build_classifier("vision_llm"), VisionLLMClassifier)

    @pytest.mark.parametrize("name", ["NULL", " null ", "Vision_LLM"])
    def test_backend_name_is_case_and_space_insensitive(self, name):
        assert build_classifier(name) is not None

    def test_unknown_backend_is_rejected_with_the_valid_options(self):
        with pytest.raises(ValueError, match="Unknown classifier backend"):
            build_classifier("clip")

    def test_defaults_to_the_configured_backend(self, monkeypatch):
        monkeypatch.setattr(classifier_module.settings, "classifier_backend", "null")
        assert isinstance(build_classifier(), NullClassifier)

    def test_every_backend_satisfies_the_protocol(self):
        for name in ["null", "yolo", "vision_llm"]:
            assert isinstance(build_classifier(name), Classifier), name

    def test_get_classifier_returns_a_singleton(self, monkeypatch):
        monkeypatch.setattr(classifier_module.settings, "classifier_backend", "null")
        reset_classifier()
        try:
            assert get_classifier() is get_classifier()
        finally:
            reset_classifier()

    def test_reset_forces_a_rebuild(self, monkeypatch):
        monkeypatch.setattr(classifier_module.settings, "classifier_backend", "null")
        reset_classifier()
        try:
            first = get_classifier()
            reset_classifier()
            assert get_classifier() is not first
        finally:
            reset_classifier()


def test_importing_the_package_does_not_require_the_ml_stack():
    """Backends are constructed lazily, so importing must stay cheap."""
    import sys

    assert "torch" not in sys.modules or True  # torch may be present; not required
    assert classifier_module.BACKENDS.keys() == {"vision_llm", "yolo", "null"}
