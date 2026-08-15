"""Shelf-life inference.

Given an item name and no user-supplied date, decide how many days it keeps and
record where the number came from. Two sources of truth, one resolver:

    1. dataset  exact match in the curated file -- human-authored, read-only
    2. learned  exact match in the learned table -- previously resolved
    3. llm      the model, shown the closest known items, anchoring where it can;
                the answer is written to the learned table
       unknown  nothing resolved, and no date is invented -- the user is asked

Two earlier tiers were deleted rather than reordered. A whole-word match against
the curated file and a keyword heuristic were both pattern guesses: they found
*some* word from the item's name in a table and assumed the whole item behaved
like that word, which is how "milk chocolate" acquired a five-day dairy shelf
life. They also created an inconsistency, because the curated file outranked the
model on an exact match but lost to it on a partial one -- so "spinach" and
"fresh spinach" could disagree.

Token similarity is still used, but only to *retrieve* which known items to show
the model. Choosing what to put in front of a resolver is a different act from
making the decision, and the failure modes differ: a poor retrieval means the
model reasons without a useful reference, while a poor match previously became
the answer outright.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from app.core.config import settings
from app.services.learned_store import LearnedShelfLifeStore, get_learned_store
from app.services.llm_estimator import resolve_shelf_life
from app.services.retrieval import top_matches

# How many known items to offer the model as reference material.
MAX_CANDIDATES = 8

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


def _retrieve_candidates(
    normalized: str, store: LearnedShelfLifeStore
) -> dict[str, int]:
    """The known items most worth showing the model, curated first."""
    known: dict[str, int] = {}
    # Learned values are added first so curated ones overwrite them on conflict:
    # where both know an item, the human-authored number is the one to show.
    for entry in store.all():
        known[entry.name] = entry.days
    known.update(_load_dataset())
    return top_matches(normalized, known, MAX_CANDIDATES)


def lookup_shelf_life_days(
    name: str,
    store: LearnedShelfLifeStore | None = None,
    client_factory=None,
) -> Tuple[int | None, str]:
    """Days the item keeps, paired with where the number came from."""
    normalized = _normalize_name(name)

    curated = _load_dataset().get(normalized)
    if curated is not None:
        return curated, "dataset"

    active_store = store if store is not None else get_learned_store()

    learned = active_store.get(normalized)
    if learned is not None:
        return learned.days, "learned"

    resolution = resolve_shelf_life(
        normalized,
        candidates=_retrieve_candidates(normalized, active_store),
        client_factory=client_factory,
    )
    if resolution is None:
        # Nothing could be established, so nothing is invented. The item is
        # flagged as needing a date and the user is asked.
        return None, "unknown"

    active_store.remember(
        normalized,
        days=resolution.days,
        anchor=resolution.anchor,
        anchor_days=resolution.anchor_days,
        model=settings.openai_model,
    )
    return resolution.days, "llm"
