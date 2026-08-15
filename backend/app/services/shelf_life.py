"""Shelf-life inference.

Given an item name and no user-supplied date, decide how many days it keeps and
record how the number was obtained. The tier that answered is returned alongside
the value, so provenance is never lost and a guess is never mistaken for a fact.

    1. dataset    exact match in the curated table -- reliable and free
    2. llm        a model that can actually answer -- accurate, cached, costs money
    3. dataset    whole-word match in the curated table -- free, but crude
    4. heuristic  keyword family (dairy / meat / greens) -- free, cruder still
       unknown    nothing matched; no date is fabricated

The ordering is by expected accuracy, not by cost, with one exception: an exact
curated entry outranks the model because it was chosen deliberately. Tiers 3 and 4
sit below the model because they are pattern guesses; they remain as the offline
path when no model is configured.

Caching lives in the estimator rather than here, so the local tiers stay free to
re-evaluate and edits to the curated file take effect immediately.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Tuple

from app.core.config import settings
from app.services.llm_estimator import estimate_shelf_life_days

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


def _lookup_exact(normalized: str) -> int | None:
    return _load_dataset().get(normalized)


def _lookup_by_token(normalized: str) -> int | None:
    """Whole-word match, so "Whole wheat bread" can find the "bread" entry.

    Also bridges classifier labels like "100_milk", which tokenise to
    {"100", "milk"} and would otherwise match nothing.
    """
    dataset = _load_dataset()
    if not dataset:
        return None

    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    candidates = []
    for key, days in dataset.items():
        if " " in key and key in normalized:
            candidates.append((len(key), days))
        elif key in tokens:
            candidates.append((len(key), days))
    if not candidates:
        return None
    # Longest matching key wins, so a specific entry beats a generic one.
    candidates.sort(reverse=True)
    return candidates[0][1]


def _heuristic_fallback(name: str) -> int | None:
    name = _normalize_name(name)
    if any(token in name for token in ["milk", "cheese", "yogurt"]):
        return 5
    if any(token in name for token in ["chicken", "beef", "pork"]):
        return 3
    if any(token in name for token in ["lettuce", "spinach", "greens"]):
        return 4
    return None


def lookup_shelf_life_days(name: str, **estimator_kwargs) -> Tuple[int | None, str]:
    """Days the item keeps, paired with the tier that produced the number."""
    normalized = _normalize_name(name)

    exact = _lookup_exact(normalized)
    if exact is not None:
        return exact, "dataset"

    estimated = estimate_shelf_life_days(normalized, **estimator_kwargs)
    if estimated is not None:
        return estimated, "llm"

    by_token = _lookup_by_token(normalized)
    if by_token is not None:
        return by_token, "dataset"

    heuristic = _heuristic_fallback(normalized)
    if heuristic is not None:
        return heuristic, "heuristic"

    return None, "unknown"
