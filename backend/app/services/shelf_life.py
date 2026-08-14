import json
import re
from pathlib import Path
from typing import Tuple

import requests

from app.core.config import settings


def _load_dataset() -> dict:
    path = Path(settings.shelf_life_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _heuristic_fallback(name: str) -> int | None:
    name = _normalize_name(name)
    if any(token in name for token in ["milk", "cheese", "yogurt"]):
        return 5
    if any(token in name for token in ["chicken", "beef", "pork"]):
        return 3
    if any(token in name for token in ["lettuce", "spinach", "greens"]):
        return 4
    return None


def _fetch_from_web(name: str) -> int | None:
    if not settings.shelf_life_api_key:
        return None
    try:
        response = requests.get(
            settings.shelf_life_api_url,
            params={"query": name, "number": 1, "apiKey": settings.shelf_life_api_key},
            timeout=6,
        )
        response.raise_for_status()
        payload = response.json()
        # Spoonacular doesn't provide shelf-life directly; we use a conservative default.
        if payload.get("results"):
            return _heuristic_fallback(name) or 5
    except requests.RequestException:
        return None
    return None


def lookup_shelf_life_days(name: str) -> Tuple[int | None, str]:
    dataset = _load_dataset()
    normalized = _normalize_name(name)
    if normalized in dataset:
        return dataset[normalized], "dataset"

    if dataset:
        tokens = set(re.findall(r"[a-z0-9]+", normalized))
        candidates = []
        for key, days in dataset.items():
            if " " in key and key in normalized:
                candidates.append((len(key), days))
            elif key in tokens:
                candidates.append((len(key), days))
        if candidates:
            candidates.sort(reverse=True)
            return candidates[0][1], "dataset"

    web_value = _fetch_from_web(normalized)
    if web_value is not None:
        return web_value, "api"

    heuristic = _heuristic_fallback(normalized)
    if heuristic is not None:
        return heuristic, "heuristic"

    return None, "unknown"
