"""Local YOLO backend.

Requires the optional ML stack (`requirements-ml.txt`) and trained weights at
`MODEL_PATH`. When either is missing it detects nothing rather than raising, so a
deployment without the ML stack still serves every endpoint.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.services.classifier import Detection, UNKNOWN_LABEL


class YoloClassifier:
    name = "yolo"

    def __init__(self) -> None:
        self._model = None
        self._load_attempted = False

    def _load_model(self):
        """Load weights once. Loading is expensive, so the result is memoised."""
        if self._load_attempted:
            return self._model
        self._load_attempted = True

        model_path = Path(settings.model_path)
        if not model_path.exists():
            return None
        try:
            from ultralytics import YOLO
        except Exception:
            # The optional ML stack is not installed.
            return None

        self._model = YOLO(str(model_path))
        return self._model

    def detect(self, image_path: Path) -> list[Detection]:
        model = self._load_model()
        if model is None:
            return []

        results = model.predict(source=str(image_path), imgsz=640, verbose=False)
        if not results:
            return []
        result = results[0]

        # A classification model exposes `probs` and describes the whole image, so
        # it can only ever yield one answer.
        if getattr(result, "probs", None) is not None:
            probs = result.probs
            index = int(probs.top1)
            label = result.names.get(index, UNKNOWN_LABEL)
            return [Detection(label=label, confidence=float(probs.top1conf))]

        # A detection model reports one box per object found. Every box is
        # returned; the previous implementation kept only the most confident one,
        # which silently discarded the rest of the shelf.
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []

        detections: list[Detection] = []
        for index in range(len(boxes)):
            class_id = int(boxes.cls[index].item())
            detections.append(
                Detection(
                    label=result.names.get(class_id, UNKNOWN_LABEL),
                    confidence=float(boxes.conf[index].item()),
                    box=self._extract_box(boxes, index),
                )
            )
        return detections

    @staticmethod
    def _extract_box(boxes, index: int) -> tuple[float, float, float, float] | None:
        raw = getattr(boxes, "xyxy", None)
        if raw is None:
            return None
        try:
            coordinates = [float(value) for value in raw[index]]
        except (TypeError, IndexError):
            return None
        if len(coordinates) != 4:
            return None
        return (coordinates[0], coordinates[1], coordinates[2], coordinates[3])
