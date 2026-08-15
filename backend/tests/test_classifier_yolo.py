"""Tests for the local YOLO backend.

The behaviour that matters most here is that a detection model's full set of boxes
is returned. The previous implementation kept only the most confident one, which
meant a photograph of a shelf logged a single item.
"""

import sys
import types
from pathlib import Path

import pytest

from app.services.classifier import Classifier, Detection
from app.services.classifier import yolo as yolo_module
from app.services.classifier.yolo import YoloClassifier


class FakeScalar:
    """Mimics a 0-d tensor, which the adapter reads through .item()."""

    def __init__(self, value):
        self._value = value

    def item(self):
        return self._value


class FakeTensor:
    """Stand-in for the tensor API the adapter touches.

    Faked rather than importing torch so this suite runs without the optional ML
    stack, which is the same property that lets the API boot without it.
    """

    def __init__(self, values):
        self._values = list(values)

    def __len__(self):
        return len(self._values)

    def __getitem__(self, index):
        value = self._values[index]
        return FakeScalar(value) if not isinstance(value, list) else value

    def argmax(self):
        return FakeScalar(max(range(len(self._values)), key=lambda i: self._values[i]))


class FakeProbs:
    def __init__(self, top1, top1conf):
        self.top1 = top1
        self.top1conf = top1conf


class FakeBoxes:
    def __init__(self, conf, cls, xyxy=None):
        self.conf = FakeTensor(conf)
        self.cls = FakeTensor(cls)
        self.xyxy = FakeTensor(xyxy) if xyxy is not None else None

    def __len__(self):
        return len(self.conf)


class FakeResult:
    def __init__(self, names, probs=None, boxes=None):
        self.names = names
        self.probs = probs
        self.boxes = boxes


class FakeModel:
    def __init__(self, results):
        self._results = results
        self.calls = []

    def predict(self, source, imgsz=640, verbose=False):
        self.calls.append({"source": source, "imgsz": imgsz, "verbose": verbose})
        return self._results


@pytest.fixture
def backend_with(monkeypatch):
    """A YoloClassifier whose model is replaced by a fake."""

    def _build(results):
        backend = YoloClassifier()
        monkeypatch.setattr(backend, "_load_model", lambda: FakeModel(results))
        return backend

    return _build


class TestWithoutWeights:
    def test_missing_weights_detect_nothing(self, tmp_path, monkeypatch):
        """The current production state: no weights are shipped."""
        monkeypatch.setattr(
            yolo_module.settings, "model_path", str(tmp_path / "absent.pt")
        )
        assert YoloClassifier().detect(Path("x.jpg")) == []

    def test_missing_weights_do_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            yolo_module.settings, "model_path", str(tmp_path / "absent.pt")
        )
        assert YoloClassifier()._load_model() is None

    def test_satisfies_the_protocol(self):
        assert isinstance(YoloClassifier(), Classifier)


class TestModelLoading:
    @staticmethod
    def install_fake_ultralytics(monkeypatch, constructed):
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
        monkeypatch.setattr(yolo_module.settings, "model_path", str(weights))
        constructed = []
        fake_cls = self.install_fake_ultralytics(monkeypatch, constructed)

        model = YoloClassifier()._load_model()
        assert isinstance(model, fake_cls)
        assert constructed == [str(weights)]

    def test_weights_are_loaded_only_once(self, tmp_path, monkeypatch):
        """Loading is expensive, so the result is memoised per instance."""
        weights = tmp_path / "model.pt"
        weights.write_bytes(b"stand-in for weights")
        monkeypatch.setattr(yolo_module.settings, "model_path", str(weights))
        constructed = []
        self.install_fake_ultralytics(monkeypatch, constructed)

        backend = YoloClassifier()
        first = backend._load_model()
        second = backend._load_model()
        assert first is second
        assert len(constructed) == 1

    def test_a_failed_load_is_not_retried(self, tmp_path, monkeypatch):
        """Re-attempting a known-missing file on every scan would be wasteful."""
        monkeypatch.setattr(
            yolo_module.settings, "model_path", str(tmp_path / "absent.pt")
        )
        backend = YoloClassifier()
        assert backend._load_model() is None
        assert backend._load_attempted is True

    def test_unavailable_ml_stack_degrades_to_none(self, tmp_path, monkeypatch):
        """A missing optional dependency must not crash the API."""
        weights = tmp_path / "model.pt"
        weights.write_bytes(b"stand-in for weights")
        monkeypatch.setattr(yolo_module.settings, "model_path", str(weights))
        monkeypatch.setitem(sys.modules, "ultralytics", None)
        assert YoloClassifier()._load_model() is None


class TestClassificationModel:
    def test_probs_branch_returns_one_detection(self, backend_with):
        """A classification model describes the whole image, so there is one answer."""
        backend = backend_with(
            [FakeResult(names={0: "milk", 1: "bread"}, probs=FakeProbs(1, 0.93))]
        )
        assert backend.detect(Path("x.jpg")) == [Detection("bread", 0.93)]

    def test_unmapped_class_index_degrades_to_unknown(self, backend_with):
        backend = backend_with(
            [FakeResult(names={0: "milk"}, probs=FakeProbs(7, 0.8))]
        )
        assert backend.detect(Path("x.jpg")) == [Detection("unknown", 0.8)]


class TestDetectionModel:
    def test_every_box_is_returned(self, backend_with):
        """The core fix: all detections survive, not just the top one."""
        backend = backend_with(
            [
                FakeResult(
                    names={0: "milk", 1: "bread", 2: "eggs"},
                    boxes=FakeBoxes(conf=[0.91, 0.89, 0.87], cls=[0, 1, 2]),
                )
            ]
        )
        detections = backend.detect(Path("shelf.jpg"))
        assert len(detections) == 3
        assert [d.label for d in detections] == ["milk", "bread", "eggs"]

    def test_confidences_are_preserved_per_detection(self, backend_with):
        backend = backend_with(
            [
                FakeResult(
                    names={0: "milk", 1: "bread"},
                    boxes=FakeBoxes(conf=[0.42, 0.95], cls=[0, 1]),
                )
            ]
        )
        detections = backend.detect(Path("shelf.jpg"))
        assert detections[0] == Detection("milk", pytest.approx(0.42))
        assert detections[1] == Detection("bread", pytest.approx(0.95))

    def test_bounding_boxes_are_captured_when_present(self, backend_with):
        backend = backend_with(
            [
                FakeResult(
                    names={0: "milk"},
                    boxes=FakeBoxes(
                        conf=[0.9], cls=[0], xyxy=[[10.0, 20.0, 30.0, 40.0]]
                    ),
                )
            ]
        )
        assert backend.detect(Path("x.jpg"))[0].box == (10.0, 20.0, 30.0, 40.0)

    def test_absent_coordinates_leave_the_box_unset(self, backend_with):
        backend = backend_with(
            [FakeResult(names={0: "milk"}, boxes=FakeBoxes(conf=[0.9], cls=[0]))]
        )
        assert backend.detect(Path("x.jpg"))[0].box is None

    def test_malformed_coordinates_leave_the_box_unset(self, backend_with):
        backend = backend_with(
            [
                FakeResult(
                    names={0: "milk"},
                    boxes=FakeBoxes(conf=[0.9], cls=[0], xyxy=[[1.0, 2.0]]),
                )
            ]
        )
        assert backend.detect(Path("x.jpg"))[0].box is None

    def test_fewer_coordinate_rows_than_boxes_leaves_the_box_unset(
        self, backend_with
    ):
        """Defensive: the two arrays are assumed aligned, but not trusted to be."""
        backend = backend_with(
            [
                FakeResult(
                    names={0: "milk", 1: "bread"},
                    boxes=FakeBoxes(
                        conf=[0.9, 0.8], cls=[0, 1], xyxy=[[1.0, 2.0, 3.0, 4.0]]
                    ),
                )
            ]
        )
        detections = backend.detect(Path("x.jpg"))
        assert detections[0].box == (1.0, 2.0, 3.0, 4.0)
        assert detections[1].box is None

    def test_non_iterable_coordinate_row_leaves_the_box_unset(self, backend_with):
        backend = backend_with(
            [
                FakeResult(
                    names={0: "milk"},
                    boxes=FakeBoxes(conf=[0.9], cls=[0], xyxy=[None]),
                )
            ]
        )
        assert backend.detect(Path("x.jpg"))[0].box is None

    def test_duplicate_items_are_reported_separately(self, backend_with):
        """Two cartons of milk are two detections, and both must survive."""
        backend = backend_with(
            [
                FakeResult(
                    names={0: "milk"},
                    boxes=FakeBoxes(conf=[0.9, 0.9], cls=[0, 0]),
                )
            ]
        )
        assert len(backend.detect(Path("x.jpg"))) == 2

    def test_no_boxes_yields_nothing(self, backend_with):
        backend = backend_with(
            [FakeResult(names={0: "milk"}, boxes=FakeBoxes(conf=[], cls=[]))]
        )
        assert backend.detect(Path("x.jpg")) == []

    def test_missing_boxes_attribute_yields_nothing(self, backend_with):
        backend = backend_with([FakeResult(names={0: "milk"}, boxes=None)])
        assert backend.detect(Path("x.jpg")) == []

    def test_empty_result_list_yields_nothing(self, backend_with):
        assert backend_with([]).detect(Path("x.jpg")) == []


def test_predict_receives_the_image_path(backend_with):
    backend = backend_with(
        [FakeResult(names={0: "milk"}, probs=FakeProbs(0, 0.99))]
    )
    backend.detect(Path("/tmp/some-image.jpg"))
    model = backend._load_model()
    assert isinstance(model, FakeModel)
