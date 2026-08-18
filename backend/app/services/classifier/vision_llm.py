"""Vision-LLM backend.

Sends the image to a vision-capable model and asks for a structured list of
grocery items. Chosen over locally trained weights because the available training
data was 200 specific retail SKUs -- a model that recognises `100_milk` but not an
arbitrary cucumber -- whereas a general vision model handles anything a user is
likely to photograph and returns names that already match the shelf-life table.

Results are cached on the image's content hash, so re-scanning an identical photo
costs nothing and always yields the same answer.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from pathlib import Path

from openai import OpenAI, OpenAIError

from app.core.config import settings
from app.services.cache import MISS, Cache, get_cache
from app.services.classifier import Detection

# Bumping the version invalidates every cached detection, which is required
# whenever the prompt or the parsing changes.
CACHE_NAMESPACE = "vision_detect_v2"

PROMPT = """You identify grocery items in a photograph.

Return JSON of the form:
{"items": [{"label": "milk", "confidence": 0.93}]}

Rules:
- If several identical items appear, return a single entry, not one per copy of the label.
- If the photo is a single packaged product, return exactly one item.
- "label" must be the common name of the food, lowercase, singular, with no brand
  and no packaging words. Prefer "milk" over "Amul Toned Milk 1L".
- "confidence" is your certainty from 0.0 to 1.0 that the item is present and
  correctly named.
- Include only food and drink. Ignore people, furniture, and utensils.
- If you cannot identify any grocery item, return {"items": []}.
"""


class VisionClassificationError(RuntimeError):
    """The vision backend could not be reached or returned something unusable."""


class VisionLLMClassifier:
    name = "vision_llm"

    def __init__(
        self,
        cache: Cache | None = None,
        client_factory=None,
    ) -> None:
        self._cache = cache
        self._client_factory = client_factory or (
            lambda: OpenAI(api_key=settings.openai_api_key)
        )

    def _cache_backend(self) -> Cache:
        return self._cache if self._cache is not None else get_cache()

    def detect(self, image_path: Path) -> list[Detection]:
        if not settings.openai_api_key:
            # No credentials: detect nothing so the scan falls through to manual
            # labelling, rather than failing the request outright.
            return []

        try:
            image_bytes = image_path.read_bytes()
        except OSError:
            return []

        cache = self._cache_backend()
        cache_key = self._cache_key(image_bytes)
        cached = cache.get(CACHE_NAMESPACE, cache_key)
        if cached is not MISS:
            return [Detection.from_dict(entry) for entry in cached]

        detections = self._ask_model(image_bytes, image_path)
        cache.set(
            CACHE_NAMESPACE,
            cache_key,
            [detection.as_dict() for detection in detections],
        )
        return detections

    @staticmethod
    def _cache_key(image_bytes: bytes) -> str:
        """Content hash, so the same photo maps to the same entry.

        The model name is included because a different model may legitimately
        return a different answer for the same picture.
        """
        digest = hashlib.sha256(image_bytes).hexdigest()
        return f"{settings.vision_model}:{digest}"

    def _ask_model(self, image_bytes: bytes, image_path: Path) -> list[Detection]:
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        data_url = (
            f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        )

        client = self._client_factory()
        try:
            response = client.chat.completions.create(
                model=settings.vision_model,
                # Forces syntactically valid JSON, removing the need to salvage
                # prose wrapped around the payload.
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Identify the grocery items in this image.",
                            },
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
                max_tokens=500,
            )
        except OpenAIError as exc:
            raise VisionClassificationError(
                "Image recognition is temporarily unavailable."
            ) from exc

        return self._parse(response.choices[0].message.content)

    @staticmethod
    def _parse(content: str | None) -> list[Detection]:
        """Turn the model's reply into detections, discarding anything malformed.

        A model can always return something unexpected, so every entry is
        validated individually and bad entries are skipped rather than failing
        the whole scan.
        """
        if not content:
            return []
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return []

        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return []

        detections: list[Detection] = []
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label")
            if not isinstance(label, str) or not label.strip():
                continue
            try:
                confidence = float(entry.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            detections.append(
                Detection(
                    label=label.strip().lower(),
                    # Clamped because a model will occasionally return 1.2 or -0.1.
                    confidence=max(0.0, min(1.0, confidence)),
                )
            )
        return detections
