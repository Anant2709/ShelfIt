"""Language-model recipe proposals for a diet week.

The model invents titles and ingredient *names*. It does not get to say what is
on the shelf: matching those names against the inventory is done afterwards, by
the same deterministic matcher the fallback recipes use. A hallucinated
"chicken" on a vegetarian week still has to survive the eating-pattern prompt,
and a pantry-mode "cumin" that is not in the fridge shows up as missing rather
than as something the user already has.

Two prompts, two jobs:

- pantry: cook from what is here, prefer what expires soonest, do not use
  expired items. Urgency is written in words so the model is not subtracting
  dates.
- ideal: ignore the fridge. Recommend the week the profile describes, in
  ordinary grocery names. The server then diffs that against the shelf and
  builds the shopping list.

`kcal` on each meal is an estimate the model is asked to include. It is stored
with a source later, never treated as a lab measurement.

`None` means the call could not be made or the reply was unusable, so the
caller can fall back to the curated file. That file is not the product; it is
what runs when there is no key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openai import OpenAI, OpenAIError

from app.core.config import settings
from app.services.recipes import (
    MAX_MEAL_KCAL,
    MIN_MEAL_KCAL,
    PLAN_DAYS,
    PLAN_MODES,
    SLOTS,
    SLOTS_FOR_COUNT,
)
from app.services.urgency import days_until

PANTRY_PROMPT = """You write a one-week meal plan that uses only the groceries
listed. Prefer items that expire soonest. Do not use anything marked EXPIRED.
Do not invent ingredients that are not on the list. Copy item names as given.

Respect the eating pattern, allergens, cooking time, and preferences.
Do not include forbidden foods.

Return JSON: {"meals": [{"day_offset": 0, "slot": "lunch", "title": "...",
"servings": 2, "prep_min": 10, "cook_min": 20,
"ingredients": [{"name": "Name", "amount": "1 cup"}, "..."],
"steps": ["Step one.", "Step two."],
"kcal": 450, "protein_g": 20, "carbs_g": 50, "fat_g": 12}]}

day_offset is 0..6 from today. Include every requested slot on every day.
Titles are short. Ingredient names must match the grocery list; amounts are
estimates. Steps are short cooking instructions. kcal and macros are estimates,
not lab measurements.
"""

IDEAL_PROMPT = """You write a one-week meal plan for the diet described.
Ignore whatever the person currently has at home. Recommend the meals they
should be eating, using ordinary grocery names (not brands).

Respect the eating pattern, allergens, cooking time, and preferences.
Do not include forbidden foods. Aim at the daily calorie target.

Return JSON: {"meals": [{"day_offset": 0, "slot": "lunch", "title": "...",
"servings": 2, "prep_min": 10, "cook_min": 20,
"ingredients": [{"name": "Name", "amount": "1 cup"}, "..."],
"steps": ["Step one.", "Step two."],
"kcal": 450, "protein_g": 20, "carbs_g": 50, "fat_g": 12}]}

day_offset is 0..6 from today. Include every requested slot on every day.
Titles are short. kcal and macros are estimates for that dish.
"""

ESTIMATE_PROMPT = """You estimate nutrition for one meal or snack from a short
description of what someone ate.

Return JSON: {"kcal": N, "protein_g": P, "carbs_g": C, "fat_g": F}
where kcal is an integer between 50 and 1500, and protein_g, carbs_g, fat_g
are non-negative numbers (grams). This is an estimate, not a lab measurement.
"""


@dataclass(frozen=True)
class NutritionEstimate:
    kcal: int
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None


def _expiry_phrase(expiration_date, today) -> str:
    remaining = days_until(expiration_date, today)
    if remaining is None:
        return "no expiry date recorded"
    if remaining < 0:
        gone = abs(remaining)
        return "EXPIRED yesterday" if gone == 1 else f"EXPIRED {gone} days ago"
    if remaining == 0:
        return "expires TODAY"
    if remaining == 1:
        return "1 day left"
    return f"{remaining} days left"


def format_pantry(items: list[dict[str, Any]], today) -> str:
    if not items:
        return "The pantry is empty."
    lines = []
    for item in items:
        name = item.get("name") or "unknown"
        expiry = _expiry_phrase(item.get("expiration_date"), today)
        category = item.get("category") or "uncategorised"
        lines.append(f"- {name} ({category}, {expiry})")
    return "On the shelf now:\n" + "\n".join(lines)


def _parse_kcal(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if MIN_MEAL_KCAL <= value <= MAX_MEAL_KCAL:
        return value
    return None


def _parse_macro_g(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 or value > 500:
        return None
    return round(value, 1)


def _profile_blurb(
    *,
    goal: str,
    eating_pattern: str,
    allergens: list[str],
    meals_per_day: int,
    calorie_target: int,
    slots: tuple[str, ...],
    sex: str | None = None,
    age: int | None = None,
    height_cm: float | None = None,
    weight_kg: float | None = None,
    target_weight_kg: float | None = None,
    activity: str | None = None,
    cooking_time: str | None = None,
    preferences: list[str] | None = None,
    recent_intake: str | None = None,
) -> str:
    allergen_text = ", ".join(allergens) if allergens else "none"
    pref_text = ", ".join(preferences) if preferences else "none"
    lines = [
        f"Goal: {goal}. Eating pattern: {eating_pattern}.",
        f"Allergens to exclude: {allergen_text}.",
        f"About {calorie_target} kcal/day, {meals_per_day} meals: {', '.join(slots)}.",
        f"Preferences: {pref_text}. Cooking time: {cooking_time or 'about_30'}.",
    ]
    body = []
    if sex:
        body.append(f"sex {sex}")
    if age is not None:
        body.append(f"age {age}")
    if height_cm is not None:
        body.append(f"height {height_cm} cm")
    if weight_kg is not None:
        body.append(f"weight {weight_kg} kg")
    if target_weight_kg is not None:
        body.append(f"target weight {target_weight_kg} kg")
    if activity:
        body.append(f"activity {activity}")
    if body:
        lines.append("Body and lifestyle: " + ", ".join(body) + ".")
    if recent_intake:
        lines.append(recent_intake)
    return " ".join(lines)


def _parse_ingredient_list(raw: Any) -> tuple[list[str], list[dict[str, str]]] | None:
    if not isinstance(raw, list):
        return None
    names: list[str] = []
    detailed: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        name = None
        amount = "as needed"
        if isinstance(item, str) and item.strip():
            name = item.strip()
        elif isinstance(item, dict):
            candidate = item.get("name")
            if isinstance(candidate, str) and candidate.strip():
                name = candidate.strip()
            amt = item.get("amount")
            if isinstance(amt, str) and amt.strip():
                amount = amt.strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
        detailed.append({"name": name, "amount": amount})
    if not names:
        return None
    return names, detailed


def _parse_steps_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    steps = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            steps.append(entry.strip())
    return steps


def _parse_meals(content: str | None, slots: tuple[str, ...]) -> list[dict[str, Any]] | None:
    if not content:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    raw_meals = payload.get("meals")
    if not isinstance(raw_meals, list):
        return None

    wanted = {(day, slot) for day in range(PLAN_DAYS) for slot in slots}
    by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in raw_meals:
        if not isinstance(entry, dict):
            continue
        try:
            day = int(entry.get("day_offset"))
        except (TypeError, ValueError):
            continue
        slot = entry.get("slot")
        title = entry.get("title")
        parsed_ingredients = _parse_ingredient_list(entry.get("ingredients"))
        if slot not in SLOTS or (day, slot) not in wanted:
            continue
        if not isinstance(title, str) or not title.strip():
            continue
        if parsed_ingredients is None:
            continue
        names, detailed = parsed_ingredients
        meal: dict[str, Any] = {
            "day_offset": day,
            "slot": slot,
            "title": title.strip(),
            "ingredients": names,
            "ingredient_details": detailed,
            "steps": _parse_steps_list(entry.get("steps")),
        }
        kcal = _parse_kcal(entry.get("kcal"))
        if kcal is not None:
            meal["kcal"] = kcal
        for key in ("servings", "prep_min", "cook_min"):
            raw = entry.get(key)
            if raw is None or isinstance(raw, bool):
                continue
            try:
                meal[key] = int(raw)
            except (TypeError, ValueError):
                continue
        for key in ("protein_g", "carbs_g", "fat_g"):
            value = _parse_macro_g(entry.get(key))
            if value is not None:
                meal[key] = value
        by_key[(day, slot)] = meal

    if not by_key:
        return None
    ordered = []
    for day in range(PLAN_DAYS):
        for slot in slots:
            meal = by_key.get((day, slot))
            if meal is None:
                continue
            ordered.append(meal)
    return ordered


def propose_week(
    *,
    mode: str,
    goal: str,
    eating_pattern: str,
    allergens: list[str],
    meals_per_day: int,
    calorie_target: int,
    pantry: list[dict[str, Any]] | None,
    today,
    client_factory=None,
    sex: str | None = None,
    age: int | None = None,
    height_cm: float | None = None,
    weight_kg: float | None = None,
    target_weight_kg: float | None = None,
    activity: str | None = None,
    cooking_time: str | None = None,
    preferences: list[str] | None = None,
    recent_intake: str | None = None,
) -> list[dict[str, Any]] | None:
    """A structured week, or None if the model could not be used."""
    if mode not in PLAN_MODES:
        return None
    if not settings.openai_api_key:
        return None
    slots = SLOTS_FOR_COUNT[meals_per_day]
    blurb = _profile_blurb(
        goal=goal,
        eating_pattern=eating_pattern,
        allergens=allergens,
        meals_per_day=meals_per_day,
        calorie_target=calorie_target,
        slots=slots,
        sex=sex,
        age=age,
        height_cm=height_cm,
        weight_kg=weight_kg,
        target_weight_kg=target_weight_kg,
        activity=activity,
        cooking_time=cooking_time,
        preferences=preferences,
        recent_intake=recent_intake,
    )
    if mode == "pantry":
        system = PANTRY_PROMPT
        user = blurb + "\n\n" + format_pantry(pantry or [], today)
    else:
        system = IDEAL_PROMPT
        user = blurb

    factory = client_factory or (lambda: OpenAI(api_key=settings.openai_api_key))
    client = factory()
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=4000,
        )
    except OpenAIError:
        return None

    content = response.choices[0].message.content
    return _parse_meals(content, slots)


def estimate_meal_nutrition(
    description: str, client_factory=None
) -> NutritionEstimate | None:
    """Kcal (required) plus optional macros, or None if the model could not be used."""
    text = (description or "").strip()
    if not text or not settings.openai_api_key:
        return None
    factory = client_factory or (lambda: OpenAI(api_key=settings.openai_api_key))
    client = factory()
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": ESTIMATE_PROMPT},
                {"role": "user", "content": text},
            ],
            max_tokens=80,
        )
    except OpenAIError:
        return None
    content = response.choices[0].message.content
    if not content:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    kcal = _parse_kcal(payload.get("kcal"))
    if kcal is None:
        return None
    return NutritionEstimate(
        kcal=kcal,
        protein_g=_parse_macro_g(payload.get("protein_g")),
        carbs_g=_parse_macro_g(payload.get("carbs_g")),
        fat_g=_parse_macro_g(payload.get("fat_g")),
    )


def estimate_meal_calories(description: str, client_factory=None) -> int | None:
    """An integer kcal estimate, or None if the model could not be used."""
    estimated = estimate_meal_nutrition(description, client_factory=client_factory)
    return estimated.kcal if estimated is not None else None
