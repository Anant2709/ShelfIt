"""Image classification, behind a swappable backend.

Why this is a package rather than a function
--------------------------------------------
The original implementation called YOLO directly and returned a single
`(label, confidence)` pair. Two problems followed from that shape:

1. A detection model reports *every* object it finds in a frame, so collapsing
   the result to the single highest-confidence box meant photographing a shelf of
   five items logged exactly one of them.
2. Swapping the model meant editing the call site.

`Classifier` fixes both. Backends return a list of `Detection`s, and which
backend runs is a configuration choice. The public surface -- `detect_items()`
and `classify_image()` -- is unchanged for callers.

Backends
--------
vision_llm  A vision-capable LLM. Recognises arbitrary groceries and returns
            clean names, at a small per-call cost. Results are cached by image
            content, so re-scanning the same photo is free.
yolo        Locally trained weights. Free and offline, but limited to the classes
            it was trained on.
null        Detects nothing. The honest default when nothing is configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, Tuple, runtime_checkable

from app.core.config import settings

UNKNOWN_LABEL = "unknown"


@dataclass(frozen=True)
class Detection:
    """One recognised object.

    `box` is optional because not every backend localises what it finds: YOLO
    returns coordinates, a vision LLM may not. Callers must not depend on it.
    """

    label: str
    confidence: float
    box: tuple[float, float, float, float] | None = None

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "box": list(self.box) if self.box else None,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Detection":
        box = payload.get("box")
        return cls(
            label=payload["label"],
            confidence=float(payload["confidence"]),
            box=tuple(box) if box else None,
        )


@runtime_checkable
class Classifier(Protocol):
    name: str

    def detect(self, image_path: Path) -> list[Detection]: ...


class NullClassifier:
    """Detects nothing.

    Used when no backend is configured. Returning an empty list means every scan
    falls through to manual labelling, which is the correct behaviour: the API
    should never invent an item it cannot actually recognise.
    """

    name = "null"

    def detect(self, image_path: Path) -> list[Detection]:
        return []


def _build_yolo() -> Classifier:
    from app.services.classifier.yolo import YoloClassifier

    return YoloClassifier()


def _build_vision_llm() -> Classifier:
    from app.services.classifier.vision_llm import VisionLLMClassifier

    return VisionLLMClassifier()


# Backends are constructed lazily so that importing this package never pulls in
# torch or the OpenAI client unless the configured backend actually needs them.
BACKENDS: dict[str, Callable[[], Classifier]] = {
    "vision_llm": _build_vision_llm,
    "yolo": _build_yolo,
    "null": NullClassifier,
}


def build_classifier(backend: str | None = None) -> Classifier:
    name = (backend or settings.classifier_backend).strip().lower()
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown classifier backend {name!r}. Choose one of {sorted(BACKENDS)}."
        ) from None


_classifier: Classifier | None = None


def get_classifier() -> Classifier:
    """The process-wide classifier, created on first use."""
    global _classifier
    if _classifier is None:
        _classifier = build_classifier()
    return _classifier


def reset_classifier() -> None:
    """Drop the process-wide instance so the next call rebuilds it."""
    global _classifier
    _classifier = None


def collapse_duplicate_labels(detections: list[Detection]) -> list[Detection]:
    """One row per name. The vision prompt already asks for that; models still
    sometimes emit `dosa batter` twice for one pack, which used to create two
    fridge rows. Different names (milk and bread) are kept.
    """
    best: dict[str, Detection] = {}
    order: list[str] = []
    for detection in detections:
        key = detection.label.strip().lower()
        if not key:
            continue
        if key not in best:
            order.append(key)
            best[key] = detection
            continue
        if detection.confidence > best[key].confidence:
            best[key] = detection
    return [best[key] for key in order]


def detect_items(
    image_path: Path, classifier: Classifier | None = None
) -> list[Detection]:
    """Every object recognised in the image, most confident first."""
    active = classifier if classifier is not None else get_classifier()
    detections = active.detect(image_path)
    ranked = sorted(detections, key=lambda d: d.confidence, reverse=True)
    return collapse_duplicate_labels(ranked)[: settings.max_detections_per_image]


def classify_image(
    image_path: Path, classifier: Classifier | None = None
) -> Tuple[str, float]:
    """The single best guess, for callers that can only act on one item.

    Retained for paths like attaching an image to an existing item, where more
    than one answer would be meaningless.
    """
    detections = detect_items(image_path, classifier=classifier)
    if not detections:
        return UNKNOWN_LABEL, 0.0
    best = detections[0]
    return best.label, best.confidence
