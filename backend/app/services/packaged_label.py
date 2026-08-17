"""Read brand + product name from a packaged-food photo when confident.

Separate from grocery Detection labels: those are common names for the shelf.
This path only runs when packaging text is readable; otherwise callers must skip
Open Food Facts / Exa entirely.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError

from app.core.config import settings
from app.services.cache import MISS, Cache, get_cache

CACHE_NAMESPACE = "packaged_label_v1"
MIN_CONFIDENCE = 0.8

PROMPT = """You read packaged grocery labels in a photograph.

Return JSON:
{"readable": true, "brand": "Brand", "product_name": "Product", "confidence": 0.9}
or {"readable": false, "confidence": 0.2}

Rules:
- Only set readable true when brand and product name are clearly legible on packaging.
- Prefer the printed brand and product name, not a generic food word like "milk".
- If the photo is produce, bulk food, or the label is blurry, readable must be false.
- confidence is 0.0 to 1.0 for the brand/product reading.
"""


@dataclass(frozen=True)
class PackagedLabel:
    brand: str
    product_name: str
    confidence: float


def _parse_label(payload: dict[str, Any]) -> PackagedLabel | None:
    if not payload.get("readable"):
        return None
    try:
        confidence = float(payload.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    if confidence < MIN_CONFIDENCE:
        return None
    brand = payload.get("brand")
    product_name = payload.get("product_name")
    if not isinstance(brand, str) or not brand.strip():
        return None
    if not isinstance(product_name, str) or not product_name.strip():
        return None
    return PackagedLabel(
        brand=brand.strip(),
        product_name=product_name.strip(),
        confidence=confidence,
    )


def read_packaged_label(
    image_path: Path,
    *,
    cache: Cache | None = None,
    client_factory=None,
) -> PackagedLabel | None:
    """Brand + product when confident, else None (skip all nutrition lookups)."""
    if not settings.openai_api_key:
        return None
    try:
        image_bytes = image_path.read_bytes()
    except OSError:
        return None

    backend = cache if cache is not None else get_cache()
    digest = hashlib.sha256(image_bytes).hexdigest()
    cache_key = f"{settings.vision_model}:{digest}"
    cached = backend.get(CACHE_NAMESPACE, cache_key)
    if cached is not MISS:
        if not cached:
            return None
        return PackagedLabel(
            brand=cached["brand"],
            product_name=cached["product_name"],
            confidence=float(cached["confidence"]),
        )

    factory = client_factory or (lambda: OpenAI(api_key=settings.openai_api_key))
    mime, _ = mimetypes.guess_type(str(image_path))
    mime = mime or "image/jpeg"
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    try:
        client = factory()
        response = client.chat.completions.create(
            model=settings.vision_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Read the packaged label if possible."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            max_tokens=120,
        )
    except OpenAIError:
        return None

    content = response.choices[0].message.content
    if not content:
        backend.set(CACHE_NAMESPACE, cache_key, None)
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        backend.set(CACHE_NAMESPACE, cache_key, None)
        return None
    if not isinstance(payload, dict):
        backend.set(CACHE_NAMESPACE, cache_key, None)
        return None
    label = _parse_label(payload)
    backend.set(
        CACHE_NAMESPACE,
        cache_key,
        None
        if label is None
        else {
            "brand": label.brand,
            "product_name": label.product_name,
            "confidence": label.confidence,
        },
    )
    return label
