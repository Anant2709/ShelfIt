"""Tests for the image classifier adapter.

The classifier is the seam between the app and whatever vision model backs it,
so these tests pin down the contract: always return a (label, confidence) pair,
and never raise when the model is absent or unhelpful.
"""

import sys
import types
from pathlib import Path

import pytest
import torch

from app.services import classifier
from app.services.classifier import classify_image


class FakeProbs:
    def __init__(self, top1: int, top1conf: float):
        self.top1 = top1
        self.top1conf = top1conf


class FakeBoxes:
    def __init__(self, conf: list[float], cls: list[int]):
        self.conf = torch.tensor(conf)
        self.cls = torch.tensor(cls, dtype=torch.int64)

    def __len__(self):
        return len(self.conf)


class FakeResult:
    def __init__(self, names: dict, probs=None, boxes=None):
        self.names = names
        self.probs = probs
        self.boxes = boxes


class FakeModel:
    def __init__(self, results):
        self._results = results
        self.calls = []

    def predict(self, source, imgsz=640, verbose=False):
        self.calls.append({"source": source, "imgsz": imgsz})
        return self._results


@pytest.fixture(autouse=True)
def reset_model_cache():
    """The adapter memoises the loaded model in a module global."""
    classifier._yolo_model = None
    yield
    classifier._yolo_model = None


class TestMissingModel:
    def test_absent_weights_yield_unknown_rather_than_raising(self, tmp_path, monkeypatch):
        """This is the current production state: no weights are shipped."""
        monkeypatch.setattr(
            classifier.settings, "model_path", str(tmp_path / "nope.pt")
        )
        assert classify_image(Path("any.jpg")) == ("unknown", 0.0)

    def test_unknown_never_clears_the_confidence_gate(self, tmp_path, monkeypatch):
        """0.0 is below any sane threshold, so scans fall to manual labelling."""
        monkeypatch.setattr(
            classifier.settings, "model_path", str(tmp_path / "nope.pt")
        )
        _, confidence = classify_image(Path("any.jpg"))
        assert confidence < classifier.settings.model_confidence_threshold


class TestClassificationModel:
    def test_probs_branch_returns_top1(self, monkeypatch):
        model = FakeModel(
            [FakeResult(names={0: "milk", 1: "bread"}, probs=FakeProbs(1, 0.93))]
        )
        monkeypatch.setattr(classifier, "_load_model", lambda: model)
        assert classify_image(Path("x.jpg")) == ("bread", 0.93)

    def test_unmapped_class_index_degrades_to_unknown(self, monkeypatch):
        model = FakeModel([FakeResult(names={0: "milk"}, probs=FakeProbs(7, 0.8))])
        monkeypatch.setattr(classifier, "_load_model", lambda: model)
        assert classify_image(Path("x.jpg")) == ("unknown", 0.8)


class TestDetectionModel:
    def test_boxes_branch_returns_highest_confidence_detection(self, monkeypatch):
        model = FakeModel(
            [
                FakeResult(
                    names={0: "milk", 1: "bread", 2: "eggs"},
                    probs=None,
                    boxes=FakeBoxes(conf=[0.4, 0.88, 0.6], cls=[0, 1, 2]),
                )
            ]
        )
        monkeypatch.setattr(classifier, "_load_model", lambda: model)
        label, confidence = classify_image(Path("x.jpg"))
        assert label == "bread"
        assert confidence == pytest.approx(0.88, abs=1e-6)

    def test_no_detections_yields_unknown(self, monkeypatch):
        model = FakeModel(
            [FakeResult(names={0: "milk"}, probs=None, boxes=FakeBoxes([], []))]
        )
        monkeypatch.setattr(classifier, "_load_model", lambda: model)
        assert classify_image(Path("x.jpg")) == ("unknown", 0.0)

    def test_missing_boxes_attribute_yields_unknown(self, monkeypatch):
        model = FakeModel([FakeResult(names={0: "milk"}, probs=None, boxes=None)])
        monkeypatch.setattr(classifier, "_load_model", lambda: model)
        assert classify_image(Path("x.jpg")) == ("unknown", 0.0)

    def test_empty_result_list_yields_unknown(self, monkeypatch):
        monkeypatch.setattr(classifier, "_load_model", lambda: FakeModel([]))
        assert classify_image(Path("x.jpg")) == ("unknown", 0.0)

    def test_only_one_of_several_detections_is_surfaced(self, monkeypatch):
        """Documents a real limitation, not a defect.

        A detection model reports every object in the frame, but the adapter's
        (label, confidence) return type can only carry one. Photographing a
        shelf of five items therefore logs exactly one. Widening this contract
        to return all detections is tracked as multi-item scanning.
        """
        model = FakeModel(
            [
                FakeResult(
                    names={0: "milk", 1: "bread", 2: "eggs"},
                    probs=None,
                    boxes=FakeBoxes(conf=[0.91, 0.89, 0.87], cls=[0, 1, 2]),
                )
            ]
        )
        monkeypatch.setattr(classifier, "_load_model", lambda: model)
        result = classify_image(Path("x.jpg"))
        assert result[0] == "milk"
        assert isinstance(result, tuple) and len(result) == 2


class TestModelLoading:
    @staticmethod
    def install_fake_ultralytics(monkeypatch, constructed: list):
        module = types.ModuleType("ultralytics")

        class FakeYOLO:
            def __init__(self, path):
                self.path = path
                constructed.append(path)

        module.YOLO = FakeYOLO
        monkeypatch.setitem(sys.modules, "ultralytics", module)
        return FakeYOLO

    def test_weights_present_loads_the_model(self, tmp_path, monkeypatch):
        weights = tmp_path / "model.pt"
        weights.write_bytes(b"stand-in for weights")
        monkeypatch.setattr(classifier.settings, "model_path", str(weights))
        constructed = []
        fake_cls = self.install_fake_ultralytics(monkeypatch, constructed)

        model = classifier._load_model()
        assert isinstance(model, fake_cls)
        assert constructed == [str(weights)]

    def test_model_is_loaded_only_once(self, tmp_path, monkeypatch):
        """Loading weights is expensive, so the adapter memoises the model."""
        weights = tmp_path / "model.pt"
        weights.write_bytes(b"stand-in for weights")
        monkeypatch.setattr(classifier.settings, "model_path", str(weights))
        constructed = []
        self.install_fake_ultralytics(monkeypatch, constructed)

        first = classifier._load_model()
        second = classifier._load_model()
        assert first is second
        assert len(constructed) == 1, "weights were re-loaded on the second call"

    def test_missing_weights_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            classifier.settings, "model_path", str(tmp_path / "absent.pt")
        )
        assert classifier._load_model() is None

    def test_unavailable_ultralytics_dependency_returns_none(
        self, tmp_path, monkeypatch
    ):
        """A missing optional dependency degrades instead of crashing the API."""
        weights = tmp_path / "model.pt"
        weights.write_bytes(b"stand-in for weights")
        monkeypatch.setattr(classifier.settings, "model_path", str(weights))
        monkeypatch.setitem(sys.modules, "ultralytics", None)
        assert classifier._load_model() is None


def test_predict_is_called_with_the_image_path(monkeypatch):
    model = FakeModel([FakeResult(names={0: "milk"}, probs=FakeProbs(0, 0.99))])
    monkeypatch.setattr(classifier, "_load_model", lambda: model)
    classify_image(Path("/tmp/some-image.jpg"))
    assert model.calls[0]["source"] == "/tmp/some-image.jpg"
    assert model.calls[0]["imgsz"] == 640
