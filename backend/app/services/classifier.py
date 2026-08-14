from __future__ import annotations

from pathlib import Path
from typing import Tuple

from app.core.config import settings

_yolo_model = None


def _load_model():
    global _yolo_model
    if _yolo_model is not None:
        return _yolo_model
    model_path = Path(settings.model_path)
    if not model_path.exists():
        return None
    try:
        from ultralytics import YOLO
    except Exception:
        return None
    _yolo_model = YOLO(str(model_path))
    return _yolo_model


def classify_image(image_path: Path) -> Tuple[str, float]:
    model = _load_model()
    if model is None:
        return "unknown", 0.0

    results = model.predict(source=str(image_path), imgsz=640, verbose=False)
    if not results:
        return "unknown", 0.0
    result = results[0]

    if getattr(result, "probs", None) is not None:
        probs = result.probs
        idx = int(probs.top1)
        confidence = float(probs.top1conf)
        label = result.names.get(idx, "unknown")
        return label, confidence

    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return "unknown", 0.0

    top_idx = int(boxes.conf.argmax().item())
    confidence = float(boxes.conf[top_idx].item())
    cls_id = int(boxes.cls[top_idx].item())
    label = result.names.get(cls_id, "unknown")
    return label, confidence
