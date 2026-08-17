"""Closed diet options, and a curated recipe fallback.

The product generates a week with the language model (pantry-grounded or
ideal). This file is what runs when there is no API key: a small human-authored
set, the same read-only tier as shelf_life.json. It is not the menu the signed-in
user is choosing from when the model is available.

Entries that fall outside the closed sets are dropped rather than trusted for
being curated, the same rule categories use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings

GOALS: frozenset[str] = frozenset(
    {"lose_weight", "maintain", "gain_weight", "eat_healthier"}
)
EATING_PATTERNS: frozenset[str] = frozenset(
    {"omnivore", "vegetarian", "eggetarian", "vegan"}
)
ALLERGENS: frozenset[str] = frozenset(
    {"dairy", "gluten", "nuts", "shellfish", "eggs", "soy"}
)
SLOTS: frozenset[str] = frozenset({"breakfast", "lunch", "snack", "dinner"})
SLOT_ORDER: tuple[str, ...] = ("breakfast", "lunch", "snack", "dinner")
MEALS_PER_DAY: frozenset[int] = frozenset({2, 3, 4})
SLOTS_FOR_COUNT: dict[int, tuple[str, ...]] = {
    2: ("lunch", "dinner"),
    3: ("breakfast", "lunch", "dinner"),
    4: ("breakfast", "lunch", "snack", "dinner"),
}
LOG_OUTCOMES: frozenset[str] = frozenset({"eaten", "skipped"})
DEFAULT_CALORIES: dict[str, int] = {
    "lose_weight": 1600,
    "maintain": 2000,
    "gain_weight": 2400,
    "eat_healthier": 2000,
}
PLAN_DAYS = 7
PLAN_MODES: frozenset[str] = frozenset({"pantry", "ideal"})
SEXES: frozenset[str] = frozenset({"female", "male", "prefer_not"})
ACTIVITIES: frozenset[str] = frozenset(
    {"sedentary", "light", "moderate", "active", "very_active"}
)
ACTIVITY_FACTOR: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}
COOKING_TIMES: frozenset[str] = frozenset({"under_20", "about_30", "an_hour_plus"})
PREFERENCES: frozenset[str] = frozenset(
    {"high_protein", "high_fiber", "low_carb", "budget", "spicy", "simple"}
)
MIN_AGE, MAX_AGE = 16, 90
MIN_HEIGHT_CM, MAX_HEIGHT_CM = 120.0, 220.0
MIN_WEIGHT_KG, MAX_WEIGHT_KG = 35.0, 250.0
SLOT_KCAL: dict[str, int] = {
    "breakfast": 350,
    "lunch": 500,
    "snack": 200,
    "dinner": 600,
}
MIN_MEAL_KCAL, MAX_MEAL_KCAL = 50, 1500
CALORIES_SOURCES: frozenset[str] = frozenset({"planned", "user", "llm", "none"})
MIN_CALORIES = 1200
MAX_CALORIES = 4000
PLACEHOLDER_TITLE = "No matching recipe"

# (path, mtime, parsed) -- keyed on mtime so editing the file during development
# takes effect without a restart.
_dataset_cache: tuple[str, float, list[dict[str, Any]]] | None = None


def reset_recipe_cache() -> None:
    """Force the next load to re-read from disk."""
    global _dataset_cache
    _dataset_cache = None


def _valid_ingredient(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    aliases = raw.get("aliases", [])
    if not isinstance(aliases, list):
        return None
    cleaned = [name.strip()]
    for alias in aliases:
        if isinstance(alias, str) and alias.strip():
            cleaned.append(alias.strip())
    amount = raw.get("amount")
    clean_amount = None
    if isinstance(amount, str) and amount.strip():
        clean_amount = amount.strip()
    return {
        "name": name.strip(),
        "aliases": cleaned,
        "amount": clean_amount or "as needed",
    }


def _parse_positive_int(raw: Any, *, default: int, lo: int, hi: int) -> int:
    if raw is None or isinstance(raw, bool):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if lo <= value <= hi:
        return value
    return default


def _parse_macro(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if 0 <= value <= 500:
        return round(value, 1)
    return None


def _parse_steps(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return ["Prepare ingredients.", "Cook until done.", "Serve."]
    steps = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            steps.append(entry.strip())
    return steps or ["Prepare ingredients.", "Cook until done.", "Serve."]


def recipe_card_from_recipe(recipe: dict[str, Any] | None, *, slot: str, title: str, kcal: int | None) -> dict[str, Any]:
    """Normalize a curated or LLM meal into the API recipe card shape."""
    if recipe is None:
        return {
            "servings": 1,
            "prep_min": 10,
            "cook_min": 15,
            "ingredients": [],
            "steps": ["No matching recipe in the fallback set."],
            "kcal": kcal,
            "protein_g": None,
            "carbs_g": None,
            "fat_g": None,
        }
    ingredients = []
    for entry in recipe.get("ingredients") or []:
        if isinstance(entry, dict) and entry.get("name"):
            ingredients.append(
                {
                    "name": entry["name"],
                    "amount": entry.get("amount") or "as needed",
                }
            )
        elif isinstance(entry, str) and entry.strip():
            ingredients.append({"name": entry.strip(), "amount": "as needed"})
    return {
        "servings": _parse_positive_int(
            recipe.get("servings"), default=2, lo=1, hi=12
        ),
        "prep_min": _parse_positive_int(
            recipe.get("prep_min"), default=10, lo=0, hi=180
        ),
        "cook_min": _parse_positive_int(
            recipe.get("cook_min"), default=20, lo=0, hi=240
        ),
        "ingredients": ingredients,
        "steps": _parse_steps(recipe.get("steps")),
        "kcal": kcal if kcal is not None else recipe.get("kcal"),
        "protein_g": _parse_macro(recipe.get("protein_g")),
        "carbs_g": _parse_macro(recipe.get("carbs_g")),
        "fat_g": _parse_macro(recipe.get("fat_g")),
        "title": title,
        "slot": slot,
    }


def _valid_recipe(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    recipe_id = raw.get("id")
    title = raw.get("title")
    if not isinstance(recipe_id, str) or not recipe_id.strip():
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    slots = raw.get("slots")
    patterns = raw.get("patterns")
    if not isinstance(slots, list) or not isinstance(patterns, list):
        return None
    clean_slots = [slot for slot in slots if slot in SLOTS]
    clean_patterns = [pattern for pattern in patterns if pattern in EATING_PATTERNS]
    if not clean_slots or not clean_patterns:
        return None
    allergens = raw.get("allergens", [])
    if not isinstance(allergens, list):
        return None
    clean_allergens = [item for item in allergens if item in ALLERGENS]
    ingredients = raw.get("ingredients")
    if not isinstance(ingredients, list) or not ingredients:
        return None
    clean_ingredients = []
    for entry in ingredients:
        parsed = _valid_ingredient(entry)
        if parsed is not None:
            clean_ingredients.append(parsed)
    if not clean_ingredients:
        return None
    kcal = raw.get("kcal")
    clean_kcal = None
    if kcal is not None and not isinstance(kcal, bool):
        try:
            value = int(kcal)
        except (TypeError, ValueError):
            value = None
        else:
            if MIN_MEAL_KCAL <= value <= MAX_MEAL_KCAL:
                clean_kcal = value
    return {
        "id": recipe_id.strip(),
        "title": title.strip(),
        "slots": clean_slots,
        "patterns": clean_patterns,
        "allergens": clean_allergens,
        "ingredients": clean_ingredients,
        "kcal": clean_kcal,
        "servings": _parse_positive_int(raw.get("servings"), default=2, lo=1, hi=12),
        "prep_min": _parse_positive_int(raw.get("prep_min"), default=10, lo=0, hi=180),
        "cook_min": _parse_positive_int(raw.get("cook_min"), default=20, lo=0, hi=240),
        "steps": _parse_steps(raw.get("steps")),
        "protein_g": _parse_macro(raw.get("protein_g")),
        "carbs_g": _parse_macro(raw.get("carbs_g")),
        "fat_g": _parse_macro(raw.get("fat_g")),
    }


def load_recipes() -> list[dict[str, Any]]:
    global _dataset_cache
    path = Path(settings.recipes_path)
    if not path.exists():
        _dataset_cache = None
        return []

    mtime = path.stat().st_mtime
    if (
        _dataset_cache is not None
        and _dataset_cache[0] == str(path)
        and _dataset_cache[1] == mtime
    ):
        return _dataset_cache[2]

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    recipes: list[dict[str, Any]] = []
    if isinstance(payload, list):
        seen: set[str] = set()
        for entry in payload:
            parsed = _valid_recipe(entry)
            if parsed is None or parsed["id"] in seen:
                continue
            seen.add(parsed["id"])
            recipes.append(parsed)
    _dataset_cache = (str(path), mtime, recipes)
    return recipes


def recipe_by_id(recipe_id: str | None) -> dict[str, Any] | None:
    if not recipe_id:
        return None
    for recipe in load_recipes():
        if recipe["id"] == recipe_id:
            return recipe
    return None


def questionnaire() -> dict[str, Any]:
    """The closed options the UI must not invent."""
    return {
        "goals": sorted(GOALS),
        "eating_patterns": sorted(EATING_PATTERNS),
        "allergens": sorted(ALLERGENS),
        "meals_per_day": sorted(MEALS_PER_DAY),
        "slots": list(SLOT_ORDER),
        "slots_for_meals_per_day": {
            str(count): list(slots) for count, slots in SLOTS_FOR_COUNT.items()
        },
        "log_outcomes": sorted(LOG_OUTCOMES),
        "default_calories": dict(DEFAULT_CALORIES),
        "plan_days": PLAN_DAYS,
        "plan_modes": sorted(PLAN_MODES),
        "sexes": sorted(SEXES),
        "activities": sorted(ACTIVITIES),
        "cooking_times": sorted(COOKING_TIMES),
        "preferences": sorted(PREFERENCES),
        "age_range": [MIN_AGE, MAX_AGE],
        "height_cm_range": [MIN_HEIGHT_CM, MAX_HEIGHT_CM],
        "weight_kg_range": [MIN_WEIGHT_KG, MAX_WEIGHT_KG],
        "calorie_disclaimer": (
            "Calorie targets are a Mifflin-St Jeor estimate, not medical advice."
        ),
    }
