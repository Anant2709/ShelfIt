"""Shelf-life inference.

Given an item name and no user-supplied date, decide how many days it keeps and
record how much to trust that number. Tiers are ordered most-trustworthy first,
and the tier that answered is returned alongside the value so provenance is never
lost:

    1. dataset   exact match against the curated table
    2. dataset   whole-word match against the same table
    3. api       external food service
    4. heuristic keyword family (dairy / meat / greens)
       unknown   nothing matched; no date is fabricated

Only tiers 3 and 4 go through the cache. Tiers 1 and 2 read a local file, so
caching them would buy nothing and would mask edits to that file.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Tuple

import requests

from app.core.config import settings
from app.services.cache import MISS, Cache, get_cache

# Namespace for cached external resolutions. Bumping the suffix invalidates every
# previously cached answer, which is the escape hatch if the resolution logic
# changes in a way that makes old values wrong.
EXTERNAL_NAMESPACE = "shelf_life_external_v1"

# (path, mtime, parsed) -- keyed on mtime so editing the file during development
# takes effect without a restart.
_dataset_cache: tuple[str, float, dict] | None = None


def _load_dataset() -> dict:
    global _dataset_cache
    path = Path(settings.shelf_life_path)
    if not path.exists():
        _dataset_cache = None
        return {}

    mtime = path.stat().st_mtime
    if (
        _dataset_cache is not None
        and _dataset_cache[0] == str(path)
        and _dataset_cache[1] == mtime
    ):
        return _dataset_cache[2]

    with path.open("r", encoding="utf-8") as handle:
        dataset = json.load(handle)
    _dataset_cache = (str(path), mtime, dataset)
    return dataset


def reset_dataset_cache() -> None:
    """Force the next load to re-read from disk."""
    global _dataset_cache
    _dataset_cache = None


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


def _lookup_dataset(normalized: str) -> int | None:
    dataset = _load_dataset()
    if normalized in dataset:
        return dataset[normalized]

    if dataset:
        tokens = set(re.findall(r"[a-z0-9]+", normalized))
        candidates = []
        for key, days in dataset.items():
            if " " in key and key in normalized:
                candidates.append((len(key), days))
            elif key in tokens:
                candidates.append((len(key), days))
        if candidates:
            # Longest matching key wins, so a specific entry beats a generic one.
            candidates.sort(reverse=True)
            return candidates[0][1]
    return None


def _resolve_external(normalized: str) -> Tuple[int | None, str]:
    """Tiers 3 and 4. Deliberately cacheable, including the negative result."""
    web_value = _fetch_from_web(normalized)
    if web_value is not None:
        return web_value, "api"

    heuristic = _heuristic_fallback(normalized)
    if heuristic is not None:
        return heuristic, "heuristic"

    return None, "unknown"


def lookup_shelf_life_days(
    name: str, cache: Cache | None = None
) -> Tuple[int | None, str]:
    normalized = _normalize_name(name)

    dataset_value = _lookup_dataset(normalized)
    if dataset_value is not None:
        return dataset_value, "dataset"

    cache = cache if cache is not None else get_cache()
    cached = cache.get(EXTERNAL_NAMESPACE, normalized)
    if cached is not MISS:
        return cached.get("days"), cached.get("source", "unknown")

    days, source = _resolve_external(normalized)
    cache.set(
        EXTERNAL_NAMESPACE,
        normalized,
        {"days": days, "source": source},
        ttl=timedelta(days=settings.cache_ttl_days),
    )
    return days, source
