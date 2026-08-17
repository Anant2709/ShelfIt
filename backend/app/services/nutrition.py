"""Packaged-product nutrition lookup: Open Food Facts first, Exa fallback.

Vision may extract a brand and product name when a label is readable. That is the
only gate for network lookups. Unreadable labels skip this module entirely so we
never invent shelf identity from a calorie API.

Numbers are estimates or label scrapes, always stored with a source.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

import requests

from app.core.config import settings

NUTRITION_SOURCES = frozenset({"open_food_facts", "exa", "none"})
OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
EXA_SEARCH_URL = "https://api.exa.ai/search"


@dataclass(frozen=True)
class NutritionResult:
    calories_kcal: int | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    source: str
    brand: str | None = None
    product_name: str | None = None


def _parse_float(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 or value > 500:
        return None
    return round(value, 1)


def _parse_kcal(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    kcal = int(round(value))
    if 0 <= kcal <= 4000:
        return kcal
    return None


def _off_from_product(product: dict[str, Any]) -> NutritionResult | None:
    nutriments = product.get("nutriments") or {}
    if not isinstance(nutriments, dict):
        return None
    kcal = _parse_kcal(
        nutriments.get("energy-kcal_100g")
        or nutriments.get("energy-kcal")
        or nutriments.get("energy_100g")
    )
    if kcal is None and nutriments.get("energy-kcal_serving") is not None:
        kcal = _parse_kcal(nutriments.get("energy-kcal_serving"))
    protein = _parse_float(
        nutriments.get("proteins_100g") or nutriments.get("proteins")
    )
    carbs = _parse_float(
        nutriments.get("carbohydrates_100g") or nutriments.get("carbohydrates")
    )
    fat = _parse_float(nutriments.get("fat_100g") or nutriments.get("fat"))
    if kcal is None and protein is None and carbs is None and fat is None:
        return None
    brand = product.get("brands")
    if isinstance(brand, str):
        brand = brand.split(",")[0].strip() or None
    else:
        brand = None
    name = product.get("product_name")
    if not isinstance(name, str) or not name.strip():
        name = None
    else:
        name = name.strip()
    return NutritionResult(
        calories_kcal=kcal,
        protein_g=protein,
        carbs_g=carbs,
        fat_g=fat,
        source="open_food_facts",
        brand=brand,
        product_name=name,
    )


def lookup_open_food_facts(
    query: str, *, session: requests.Session | None = None
) -> NutritionResult | None:
    text = (query or "").strip()
    if not text:
        return None
    http = session or requests.Session()
    try:
        response = http.get(
            OFF_SEARCH_URL,
            params={
                "search_terms": text,
                "search_simple": 1,
                "action": "process",
                "json": 1,
                "page_size": 5,
            },
            timeout=8,
            headers={"User-Agent": "ShelfIt/1.0 (interview demo)"},
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError, json.JSONDecodeError):
        return None
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list):
        return None
    for product in products:
        if not isinstance(product, dict):
            continue
        parsed = _off_from_product(product)
        if parsed is not None:
            return parsed
    return None


def _exa_macros_from_text(text: str) -> NutritionResult | None:
    kcal_match = re.search(
        r"(?:calories?|kcal)\s*[:=]?\s*(\d{2,4})", text, re.I
    )
    protein_match = re.search(r"protein[s]?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*g", text, re.I)
    carbs_match = re.search(
        r"carb(?:ohydrate)?s?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*g", text, re.I
    )
    fat_match = re.search(r"fat[s]?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*g", text, re.I)
    kcal = _parse_kcal(kcal_match.group(1) if kcal_match else None)
    protein = _parse_float(protein_match.group(1) if protein_match else None)
    carbs = _parse_float(carbs_match.group(1) if carbs_match else None)
    fat = _parse_float(fat_match.group(1) if fat_match else None)
    if kcal is None and protein is None and carbs is None and fat is None:
        return None
    return NutritionResult(
        calories_kcal=kcal,
        protein_g=protein,
        carbs_g=carbs,
        fat_g=fat,
        source="exa",
    )


def lookup_exa(
    query: str, *, session: requests.Session | None = None
) -> NutritionResult | None:
    text = (query or "").strip()
    if not text or not settings.exa_api_key:
        return None
    http = session or requests.Session()
    try:
        response = http.post(
            EXA_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {settings.exa_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": f"{text} nutrition facts calories protein carbs fat per serving",
                "type": "auto",
                "numResults": 3,
                "contents": {"text": True},
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError, json.JSONDecodeError):
        return None
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return None
    for entry in results:
        if not isinstance(entry, dict):
            continue
        blob = " ".join(
            str(entry.get(key) or "") for key in ("title", "text", "snippet")
        )
        parsed = _exa_macros_from_text(blob)
        if parsed is not None:
            return parsed
    return None


def lookup_nutrition(
    *,
    brand: str | None,
    product_name: str | None,
    off_lookup: Callable[..., NutritionResult | None] | None = None,
    exa_lookup: Callable[..., NutritionResult | None] | None = None,
) -> NutritionResult:
    """OFF first, Exa second. Always returns a result with a source label."""
    parts = [part for part in (brand, product_name) if part and part.strip()]
    query = " ".join(parts).strip()
    if not query:
        return NutritionResult(None, None, None, None, "none")

    off = (off_lookup or lookup_open_food_facts)(query)
    if off is not None:
        return off
    exa = (exa_lookup or lookup_exa)(query)
    if exa is not None:
        return NutritionResult(
            calories_kcal=exa.calories_kcal,
            protein_g=exa.protein_g,
            carbs_g=exa.carbs_g,
            fat_g=exa.fat_g,
            source="exa",
            brand=brand,
            product_name=product_name,
        )
    return NutritionResult(
        None, None, None, None, "none", brand=brand, product_name=product_name
    )
