"""Category assignment.

Same three-part shape as shelf life, applied to a second kind of uncertainty:

    1. dataset  exact match in the curated file -- human-authored, read-only
    2. learned  exact match in the learned table -- previously resolved
    3. llm      the model, shown similar known items, picking from the closed set;
                the answer is written to the learned table
       unknown  nothing resolved, and no category is invented -- stored as NULL

The categories are a closed set, and that is the load-bearing decision. Waste
analytics group by category, so free-text values would fragment the grouping the
moment the model said "dairy products" instead of "dairy" -- and every individual
answer would still look correct. A closed set makes that failure impossible
rather than unlikely, which is also why these entries need no anchor: the anchor
existed in shelf life to stop unbounded numbers from disagreeing, and there is
nothing here for it to constrain.

The set is one axis on purpose: what the food *is*. "Frozen" was left out despite
being an obvious shelf label, because frozen chicken is both frozen and meat, and
a field that mixes food type with storage state cannot be grouped by either.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Tuple

from app.core.config import settings
from app.services.category_store import LearnedCategoryStore, get_category_store
from app.services.llm_categorizer import resolve_category
from app.services.retrieval import top_matches

# How many known items to offer the model as reference material.
MAX_CANDIDATES = 8


class Category(StrEnum):
    PRODUCE = "produce"
    DAIRY = "dairy"
    MEAT_SEAFOOD = "meat_seafood"
    BAKERY = "bakery"
    GRAINS_PULSES = "grains_pulses"
    SPICES_CONDIMENTS = "spices_condiments"
    SNACKS_SWEETS = "snacks_sweets"
    BEVERAGES = "beverages"
    PANTRY = "pantry"
    # Not a shelf. Stored as NULL and surfaced as this value so "we could not
    # tell" is selectable in a filter instead of being an invisible gap.
    UNKNOWN = "unknown"


# The values the model is allowed to choose from: every real category, and not
# UNKNOWN, which is the absence of an answer rather than one of the options.
ASSIGNABLE: frozenset[str] = frozenset(
    category.value for category in Category if category is not Category.UNKNOWN
)

# (path, mtime, parsed) -- keyed on mtime so editing the file during development
# takes effect without a restart.
_dataset_cache: tuple[str, float, dict] | None = None


def _load_dataset() -> dict:
    global _dataset_cache
    path = Path(settings.categories_path)
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
    # A curated file is still a file someone edits by hand, so entries outside
    # the closed set are dropped rather than trusted for being curated.
    dataset = {
        name: category
        for name, category in dataset.items()
        if category in ASSIGNABLE
    }
    _dataset_cache = (str(path), mtime, dataset)
    return dataset


def reset_category_dataset_cache() -> None:
    """Force the next load to re-read from disk."""
    global _dataset_cache
    _dataset_cache = None


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _retrieve_candidates(
    normalized: str, store: LearnedCategoryStore
) -> dict[str, str]:
    """The known items most worth showing the model, curated first."""
    known: dict[str, str] = {}
    # Learned values first so curated ones overwrite them on conflict: where both
    # know an item, the human-authored answer is the one to show.
    for entry in store.all():
        known[entry.name] = entry.category
    known.update(_load_dataset())
    return top_matches(normalized, known, MAX_CANDIDATES)


def lookup_category(
    name: str,
    store: LearnedCategoryStore | None = None,
    client_factory=None,
) -> Tuple[Category | None, str]:
    """The item's category, paired with where the answer came from.

    Returns `None` for the category when nothing could be established, so the
    caller stores NULL rather than a guess.
    """
    normalized = _normalize_name(name)

    curated = _load_dataset().get(normalized)
    if curated is not None:
        return Category(curated), "dataset"

    active_store = store if store is not None else get_category_store()

    learned = active_store.get(normalized)
    if learned is not None:
        return Category(learned.category), "learned"

    resolved = resolve_category(
        normalized,
        allowed=ASSIGNABLE,
        candidates=_retrieve_candidates(normalized, active_store),
        client_factory=client_factory,
    )
    if resolved is None:
        return None, "unknown"

    active_store.remember(
        normalized,
        category=resolved,
        model=settings.openai_model,
    )
    return Category(resolved), "llm"
