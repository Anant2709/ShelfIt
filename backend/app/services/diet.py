"""Diet profile, inventory-aware plans, and meal-count adherence.

Closed sets, not free text: the same reason categories are a list rather than
whatever the model names. A "high protein vegetarian" typed in a box would
fragment every later grouping the way "dairy products" would have.

Recipes are proposed by the language model when a key is configured, then
*matched* against this user's unresolved inventory. Matching is name equality
with a few conservative variants, not urgency-as-identity -- the measured chat
failure was the model recording bread when asked about paneer because bread
was expiring sooner. Urgency only boosts a fallback recipe that already uses
an item the fridge actually holds, and in pantry-mode prompts it is written in
words so the model is not subtracting dates. Expired items are on the shelf
but are not treated as usable.

Two plan modes: pantry cooks from what is here; ideal ignores the fridge and
the missing names become the shopping list. `data/recipes.json` is only the
no-key fallback, the same role as the curated shelf-life file.

Calorie targets come from Mifflin-St Jeor using the profile's weight, height,
age, sex, and activity, then a modest surplus or deficit for the goal. That is
a formula, labeled as an estimate, not medical advice. A typed calorie_target
still wins. Planned meal kcal and skip-substitutes (user-typed or model
estimated) feed progress; they are not lab measurements. Weigh-ins are the
weight history; the profile holds the current weight used for the next target.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core import clock
from app.core.clock import utcnow
from app.models.diet import (
    DietExtraIntake,
    DietLog,
    DietPlan,
    DietPlanMeal,
    DietProfile,
    DietWeighIn,
)
from app.models.inventory import InventoryItem
from app.models.user import User
from app.services.llm_recipes import estimate_meal_nutrition, propose_week
from app.services.recipes import (
    ACTIVITY_FACTOR,
    ACTIVITIES,
    ALLERGENS,
    COOKING_TIMES,
    EATING_PATTERNS,
    GOALS,
    LOG_OUTCOMES,
    MAX_AGE,
    MAX_CALORIES,
    MAX_HEIGHT_CM,
    MAX_MEAL_KCAL,
    MAX_WEIGHT_KG,
    MEALS_PER_DAY,
    MIN_AGE,
    MIN_CALORIES,
    MIN_HEIGHT_CM,
    MIN_MEAL_KCAL,
    MIN_WEIGHT_KG,
    PLACEHOLDER_TITLE,
    PLAN_DAYS,
    PLAN_MODES,
    PREFERENCES,
    SEXES,
    SLOT_KCAL,
    SLOT_ORDER,
    SLOTS,
    SLOTS_FOR_COUNT,
    load_recipes,
    recipe_by_id,
    recipe_card_from_recipe,
)
from app.services.urgency import Urgency, classify


class DietError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def parse_allergens(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if item in ALLERGENS]


def parse_preferences(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if item in PREFERENCES]


def _dump(values: list[str]) -> str:
    return json.dumps(values)


def _dump_obj(value) -> str:
    return json.dumps(value)


def load_recipe_card(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _load_recipe_card(raw: str | None) -> dict | None:
    return load_recipe_card(raw)


def _load_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]


def mifflin_bmr(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    """Mifflin-St Jeor resting energy, in kcal/day.

    `prefer_not` uses the midpoint of the male and female constants so we do
    not invent a third equation.
    """
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    if sex == "male":
        return base + 5
    if sex == "female":
        return base - 161
    return base - 78


def tdee_kcal(profile: DietProfile) -> int:
    bmr = mifflin_bmr(
        profile.weight_kg, profile.height_cm, profile.age, profile.sex
    )
    factor = ACTIVITY_FACTOR[profile.activity]
    return int(round(bmr * factor))


def goal_calorie_target(tdee: int, goal: str) -> int:
    if goal == "lose_weight":
        return max(MIN_CALORIES, tdee - 500)
    if goal == "gain_weight":
        return min(MAX_CALORIES, tdee + 300)
    return min(MAX_CALORIES, max(MIN_CALORIES, tdee))


def resolved_calories(profile: DietProfile) -> int:
    if profile.calorie_target is not None:
        return profile.calorie_target
    return goal_calorie_target(tdee_kcal(profile), profile.goal)


def meal_kcal_for(slot: str, recipe: dict | None = None, proposed: int | None = None) -> int:
    if proposed is not None and MIN_MEAL_KCAL <= proposed <= MAX_MEAL_KCAL:
        return proposed
    if recipe and recipe.get("kcal"):
        return recipe["kcal"]
    return SLOT_KCAL.get(slot, 400)


def get_profile(db: Session, user_id: str) -> DietProfile | None:
    return db.get(DietProfile, user_id)


def _clamp_body(*, age, height_cm, weight_kg, target_weight_kg, sex, activity, cooking_time, preferences):
    if sex not in SEXES:
        raise DietError("Unknown sex")
    if activity not in ACTIVITIES:
        raise DietError("Unknown activity level")
    if cooking_time not in COOKING_TIMES:
        raise DietError("Unknown cooking time")
    if not (MIN_AGE <= age <= MAX_AGE):
        raise DietError(f"age must be between {MIN_AGE} and {MAX_AGE}")
    if not (MIN_HEIGHT_CM <= height_cm <= MAX_HEIGHT_CM):
        raise DietError("height_cm is out of range")
    if not (MIN_WEIGHT_KG <= weight_kg <= MAX_WEIGHT_KG):
        raise DietError("weight_kg is out of range")
    if not (MIN_WEIGHT_KG <= target_weight_kg <= MAX_WEIGHT_KG):
        raise DietError("target_weight_kg is out of range")
    unknown = [item for item in preferences if item not in PREFERENCES]
    if unknown:
        raise DietError("Unknown preference")
    return sorted(set(preferences))


def upsert_weigh_in(
    db: Session,
    user: User,
    *,
    weight_kg: float,
    logged_date: date | None = None,
    update_profile: bool = True,
) -> DietWeighIn:
    if not (MIN_WEIGHT_KG <= weight_kg <= MAX_WEIGHT_KG):
        raise DietError("weight_kg is out of range")
    today = clock.today(user.timezone)
    day = logged_date or today
    if day > today:
        raise DietError("Cannot log a future weigh-in")
    row = (
        db.query(DietWeighIn)
        .filter(DietWeighIn.user_id == user.id, DietWeighIn.logged_date == day)
        .one_or_none()
    )
    if row is None:
        row = DietWeighIn(user_id=user.id, logged_date=day)
        db.add(row)
    row.weight_kg = float(weight_kg)
    db.flush()
    if update_profile:
        profile = get_profile(db, user.id)
        if profile is not None:
            latest = (
                db.query(DietWeighIn)
                .filter(DietWeighIn.user_id == user.id)
                .order_by(DietWeighIn.logged_date.desc(), DietWeighIn.id.desc())
                .first()
            )
            profile.weight_kg = latest.weight_kg
            profile.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def upsert_profile(
    db: Session,
    user: User,
    *,
    goal: str,
    eating_pattern: str,
    allergens: list[str],
    meals_per_day: int,
    calorie_target: int | None,
    sex: str,
    age: int,
    height_cm: float,
    weight_kg: float,
    target_weight_kg: float,
    activity: str,
    cooking_time: str = "about_30",
    preferences: list[str] | None = None,
) -> DietProfile:
    if goal not in GOALS:
        raise DietError("Unknown goal")
    if eating_pattern not in EATING_PATTERNS:
        raise DietError("Unknown eating pattern")
    if meals_per_day not in MEALS_PER_DAY:
        raise DietError("meals_per_day must be 2, 3, or 4")
    unknown = [item for item in allergens if item not in ALLERGENS]
    if unknown:
        raise DietError("Unknown allergen")
    unique_allergens = sorted(set(allergens))
    if calorie_target is not None and not (
        MIN_CALORIES <= calorie_target <= MAX_CALORIES
    ):
        raise DietError(
            f"calorie_target must be between {MIN_CALORIES} and {MAX_CALORIES}"
        )
    unique_prefs = _clamp_body(
        age=age,
        height_cm=float(height_cm),
        weight_kg=float(weight_kg),
        target_weight_kg=float(target_weight_kg),
        sex=sex,
        activity=activity,
        cooking_time=cooking_time,
        preferences=preferences or [],
    )

    profile = get_profile(db, user.id)
    if profile is None:
        profile = DietProfile(user_id=user.id)
        db.add(profile)
    profile.goal = goal
    profile.eating_pattern = eating_pattern
    profile.allergens = _dump(unique_allergens)
    profile.meals_per_day = meals_per_day
    profile.calorie_target = calorie_target
    profile.sex = sex
    profile.age = int(age)
    profile.height_cm = float(height_cm)
    profile.weight_kg = float(weight_kg)
    profile.target_weight_kg = float(target_weight_kg)
    profile.activity = activity
    profile.cooking_time = cooking_time
    profile.preferences = _dump(unique_prefs)
    profile.updated_at = utcnow()
    db.flush()
    upsert_weigh_in(
        db, user, weight_kg=float(weight_kg), update_profile=False
    )
    db.refresh(profile)
    return profile


def current_plan(db: Session, user_id: str) -> DietPlan | None:
    return (
        db.query(DietPlan)
        .filter(DietPlan.user_id == user_id)
        .order_by(DietPlan.created_at.desc(), DietPlan.id.desc())
        .first()
    )


def open_inventory(db: Session, user_id: str) -> list[InventoryItem]:
    return (
        db.query(InventoryItem)
        .filter(
            InventoryItem.user_id == user_id,
            InventoryItem.resolved_at.is_(None),
        )
        .all()
    )


AMBIGUOUS_BASES = frozenset(
    {"milk", "oil", "juice", "water", "sauce", "butter", "cheese"}
)


def names_match(ingredient: str, item_name: str) -> bool:
    """Whether an inventory item satisfies an ingredient name.

    Exact equality first. Then a prefix so "chicken" matches "Chicken Breast"
    without "milk" matching "Coconut Milk". Last-word match is allowed only for
    names that are not those ambiguous bases, so "rice" can match "Basmati Rice".
    """
    left = ingredient.strip().lower()
    right = item_name.strip().lower()
    if not left or not right:
        return False
    if left == right:
        return True
    if right.startswith(left + " ") or left.startswith(right + " "):
        return True
    if left + "s" == right or right + "s" == left:
        return True
    if left + "es" == right or right + "es" == left:
        return True
    last = right.split()[-1]
    if last == left and left not in AMBIGUOUS_BASES:
        return True
    return False


def _ingredient_names(ingredient: dict) -> set[str]:
    return {alias.strip().lower() for alias in ingredient["aliases"] if alias.strip()}


def _usable_match(
    ingredient: dict, items: list[InventoryItem], today: date
) -> InventoryItem | None:
    """The on-hand item that satisfies this ingredient, if any.

    Expired matches are ignored: they are on the shelf, but they are not
    something to cook with. When two items share a name, the more urgent
    non-expired one is used -- urgency breaks a tie; it does not pick a
    different ingredient.
    """
    wanted = _ingredient_names(ingredient)
    usable: list[InventoryItem] = []
    for item in items:
        if not any(names_match(alias, item.name) for alias in wanted):
            continue
        expiration = item.expiration.expiration_date if item.expiration else None
        if classify(expiration, today) is Urgency.EXPIRED:
            continue
        usable.append(item)
    if not usable:
        return None

    def urgency_key(item: InventoryItem) -> tuple:
        expiration = item.expiration.expiration_date if item.expiration else None
        remaining = None if expiration is None else (expiration - today).days
        if remaining is None:
            return (1, 0, item.name)
        return (0, remaining, item.name)

    return min(usable, key=urgency_key)


def match_recipe(
    recipe: dict, items: list[InventoryItem], today: date
) -> tuple[list[str], list[str], int]:
    uses: list[str] = []
    missing: list[str] = []
    urgent = 0
    for ingredient in recipe["ingredients"]:
        match = _usable_match(ingredient, items, today)
        if match is None:
            missing.append(ingredient["name"])
            continue
        uses.append(match.name)
        expiration = match.expiration.expiration_date if match.expiration else None
        if classify(expiration, today) in {Urgency.TODAY, Urgency.SOON}:
            urgent += 1
    score = 10 * len(uses) + 5 * urgent - 2 * len(missing)
    return uses, missing, score


def match_ingredient_names(
    names: list[str], items: list[InventoryItem], today: date
) -> tuple[list[str], list[str], int]:
    ingredients = [{"name": name, "aliases": [name]} for name in names]
    return match_recipe(
        {"id": "", "title": "", "ingredients": ingredients}, items, today
    )


def eligible_recipes(
    *,
    eating_pattern: str,
    allergens: list[str],
    slot: str,
) -> list[dict]:
    blocked = set(allergens)
    chosen: list[dict] = []
    for recipe in load_recipes():
        if eating_pattern not in recipe["patterns"]:
            continue
        if slot not in recipe["slots"]:
            continue
        if blocked.intersection(recipe["allergens"]):
            continue
        chosen.append(recipe)
    return chosen


def _pick_recipe(
    *,
    slot: str,
    eating_pattern: str,
    allergens: list[str],
    items: list[InventoryItem],
    today: date,
    recently_used: set[str],
    mode: str,
) -> tuple[dict | None, list[str], list[str]]:
    candidates = eligible_recipes(
        eating_pattern=eating_pattern, allergens=allergens, slot=slot
    )
    if not candidates:
        return None, [], []

    pool = [recipe for recipe in candidates if recipe["id"] not in recently_used]
    if not pool:
        recently_used.clear()
        pool = candidates

    def rank(recipe: dict) -> tuple:
        if mode == "ideal":
            return (recipe["id"],)
        uses, _missing, score = match_recipe(recipe, items, today)
        return (-score, -len(uses), recipe["id"])

    ranked = sorted(pool, key=rank)
    winner = ranked[0]
    uses, missing, _ = match_recipe(winner, items, today)
    return winner, uses, missing


def _meal_from_recipe(
    *,
    day_offset: int,
    slot: str,
    recipe: dict | None,
    uses: list[str],
    missing: list[str],
) -> DietPlanMeal:
    if recipe is None:
        kcal = meal_kcal_for(slot)
        card = recipe_card_from_recipe(
            None, slot=slot, title=PLACEHOLDER_TITLE, kcal=kcal
        )
        return DietPlanMeal(
            day_offset=day_offset,
            slot=slot,
            recipe_id=None,
            title=PLACEHOLDER_TITLE,
            uses_json="[]",
            missing_json="[]",
            ingredients_json="[]",
            kcal=kcal,
            recipe_json=_dump_obj(card),
        )
    ingredient_names = [entry["name"] for entry in recipe["ingredients"]]
    kcal = meal_kcal_for(slot, recipe=recipe)
    card = recipe_card_from_recipe(
        recipe, slot=slot, title=recipe["title"], kcal=kcal
    )
    return DietPlanMeal(
        day_offset=day_offset,
        slot=slot,
        recipe_id=recipe["id"],
        title=recipe["title"],
        uses_json=_dump(uses),
        missing_json=_dump(missing),
        ingredients_json=_dump(ingredient_names),
        kcal=kcal,
        recipe_json=_dump_obj(card),
    )


def _meal_from_proposal(
    proposal: dict, items: list[InventoryItem], today: date
) -> DietPlanMeal:
    uses, missing, _ = match_ingredient_names(
        proposal["ingredients"], items, today
    )
    kcal = meal_kcal_for(proposal["slot"], proposed=proposal.get("kcal"))
    card_source = {
        "servings": proposal.get("servings", 2),
        "prep_min": proposal.get("prep_min", 10),
        "cook_min": proposal.get("cook_min", 20),
        "ingredients": proposal.get("ingredient_details")
        or [
            {"name": name, "amount": "as needed"}
            for name in proposal["ingredients"]
        ],
        "steps": proposal.get("steps")
        or ["Prepare ingredients.", "Cook until done.", "Serve."],
        "protein_g": proposal.get("protein_g"),
        "carbs_g": proposal.get("carbs_g"),
        "fat_g": proposal.get("fat_g"),
        "kcal": kcal,
    }
    card = recipe_card_from_recipe(
        card_source,
        slot=proposal["slot"],
        title=proposal["title"],
        kcal=kcal,
    )
    return DietPlanMeal(
        day_offset=proposal["day_offset"],
        slot=proposal["slot"],
        recipe_id=None,
        title=proposal["title"],
        uses_json=_dump(uses),
        missing_json=_dump(missing),
        ingredients_json=_dump(proposal["ingredients"]),
        kcal=kcal,
        recipe_json=_dump_obj(card),
    )


def _pattern_allows_item(item: InventoryItem, eating_pattern: str, allergens: list[str]) -> bool:
    category = item.category
    name = item.name.lower()
    if eating_pattern in {"vegetarian", "eggetarian", "vegan"} and category == "meat_seafood":
        return False
    if eating_pattern == "vegan" and category == "dairy":
        return False
    if "dairy" in allergens and category == "dairy":
        return False
    if "gluten" in allergens and category == "bakery":
        return False
    if "eggs" in allergens and "egg" in name:
        return False
    if "nuts" in allergens and "nut" in name:
        return False
    if "soy" in allergens and "soy" in name:
        return False
    if "shellfish" in allergens and category == "meat_seafood":
        return False
    return True


def pantry_for_prompt(
    items: list[InventoryItem],
    today: date,
    *,
    eating_pattern: str,
    allergens: list[str],
) -> list[dict]:
    """What the pantry-mode model is allowed to see.

    Expired items and foods the profile forbids are omitted so the prompt cannot
    recommend them. The matcher still refuses expired matches as a backstop.
    """
    visible = []
    for item in items:
        expiration = item.expiration.expiration_date if item.expiration else None
        if classify(expiration, today) is Urgency.EXPIRED:
            continue
        if not _pattern_allows_item(item, eating_pattern, allergens):
            continue
        visible.append(
            {
                "name": item.name,
                "category": item.category,
                "expiration_date": expiration,
            }
        )
    return visible


def recent_intake_blurb(db: Session, user: User, window_days: int = 7) -> str | None:
    """A short prompt hint from recent logged intake and macros."""
    try:
        report = progress(db, user, window_days=window_days)
    except DietError:
        return None
    if report.planned == 0 and report.extras == 0 and report.eaten == 0:
        return None
    return (
        f"Recent {report.window_days}-day intake context: "
        f"about {report.protein_g}g protein, {report.carbs_g}g carbs, "
        f"{report.fat_g}g fat across logged meals and extras. "
        "Prefer variety and stay near the daily calorie target."
    )


def generate_plan(
    db: Session,
    user: User,
    mode: str = "pantry",
    client_factory=None,
) -> DietPlan:
    if mode not in PLAN_MODES:
        raise DietError("Unknown plan mode")
    profile = get_profile(db, user.id)
    if profile is None:
        raise DietError("Save a diet profile first", status_code=400)

    today = clock.today(user.timezone)
    items = open_inventory(db, user.id)
    allergens = parse_allergens(profile.allergens)
    slots = SLOTS_FOR_COUNT[profile.meals_per_day]
    calories = resolved_calories(profile)
    pantry = pantry_for_prompt(
        items,
        today,
        eating_pattern=profile.eating_pattern,
        allergens=allergens,
    )

    proposals = propose_week(
        mode=mode,
        goal=profile.goal,
        eating_pattern=profile.eating_pattern,
        allergens=allergens,
        meals_per_day=profile.meals_per_day,
        calorie_target=calories,
        pantry=pantry if mode == "pantry" else None,
        today=today,
        client_factory=client_factory,
        sex=profile.sex,
        age=profile.age,
        height_cm=profile.height_cm,
        weight_kg=profile.weight_kg,
        target_weight_kg=profile.target_weight_kg,
        activity=profile.activity,
        cooking_time=profile.cooking_time,
        preferences=parse_preferences(profile.preferences),
        recent_intake=recent_intake_blurb(db, user),
    )

    meals: list[DietPlanMeal] = []
    if proposals:
        by_key = {(row["day_offset"], row["slot"]): row for row in proposals}
        for day_offset in range(PLAN_DAYS):
            for slot in slots:
                proposal = by_key.get((day_offset, slot))
                if proposal is None:
                    meals.append(
                        _meal_from_recipe(
                            day_offset=day_offset,
                            slot=slot,
                            recipe=None,
                            uses=[],
                            missing=[],
                        )
                    )
                    continue
                meals.append(_meal_from_proposal(proposal, items, today))
    else:
        recently_used: set[str] = set()
        for day_offset in range(PLAN_DAYS):
            for slot in slots:
                recipe, uses, missing = _pick_recipe(
                    slot=slot,
                    eating_pattern=profile.eating_pattern,
                    allergens=allergens,
                    items=items,
                    today=today,
                    recently_used=recently_used,
                    mode=mode,
                )
                if recipe is not None:
                    recently_used.add(recipe["id"])
                meals.append(
                    _meal_from_recipe(
                        day_offset=day_offset,
                        slot=slot,
                        recipe=recipe,
                        uses=uses,
                        missing=missing,
                    )
                )

    plan = DietPlan(
        user_id=user.id,
        window_start=today,
        window_days=PLAN_DAYS,
        calorie_target=calories,
        goal=profile.goal,
        eating_pattern=profile.eating_pattern,
        meals_per_day=profile.meals_per_day,
        mode=mode,
        meals=meals,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def meal_date(plan: DietPlan, meal: DietPlanMeal) -> date:
    return plan.window_start + timedelta(days=meal.day_offset)


def decorate_meal(
    plan: DietPlan,
    meal: DietPlanMeal,
    items: list[InventoryItem],
    today: date,
) -> tuple[list[str], list[str]]:
    names = _load_names(meal.ingredients_json)
    if names:
        uses, missing, _ = match_ingredient_names(names, items, today)
        return uses, missing
    recipe = recipe_by_id(meal.recipe_id)
    if recipe is None:
        return _load_names(meal.uses_json), _load_names(meal.missing_json)
    uses, missing, _ = match_recipe(recipe, items, today)
    return uses, missing


def unique_names(groups: list[list[str]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for name in group:
            key = name.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(name.strip())
    return ordered


def sorted_meals(plan: DietPlan) -> list[DietPlanMeal]:
    slot_index = {slot: index for index, slot in enumerate(SLOT_ORDER)}
    return sorted(
        plan.meals, key=lambda meal: (meal.day_offset, slot_index.get(meal.slot, 99))
    )


def get_log(db: Session, user_id: str, logged_date: date, slot: str) -> DietLog | None:
    return (
        db.query(DietLog)
        .filter(
            DietLog.user_id == user_id,
            DietLog.logged_date == logged_date,
            DietLog.slot == slot,
        )
        .one_or_none()
    )


def planned_meal_for(
    db: Session, user_id: str, day: date, slot: str
) -> DietPlanMeal | None:
    plan = current_plan(db, user_id)
    if plan is None:
        return None
    for meal in plan.meals:
        if meal_date(plan, meal) == day and meal.slot == slot:
            return meal
    return None


def _calories_for_log(
    *,
    outcome: str,
    planned: DietPlanMeal | None,
    slot: str,
    substitute_text: str | None,
    calories_kcal: int | None,
    protein_g: float | None = None,
    carbs_g: float | None = None,
    fat_g: float | None = None,
    client_factory=None,
) -> tuple[int, str, str | None, float | None, float | None, float | None, str | None]:
    user_macros = any(value is not None for value in (protein_g, carbs_g, fat_g))
    if user_macros:
        for label, value in (
            ("protein_g", protein_g),
            ("carbs_g", carbs_g),
            ("fat_g", fat_g),
        ):
            if value is not None and not (0 <= value <= 500):
                raise DietError(f"{label} must be between 0 and 500")

    if outcome == "eaten":
        planned_kcal = planned.kcal if planned is not None else None
        card = load_recipe_card(planned.recipe_json) if planned is not None else None
        planned_protein = card.get("protein_g") if card else None
        planned_carbs = card.get("carbs_g") if card else None
        planned_fat = card.get("fat_g") if card else None
        if user_macros:
            return (
                meal_kcal_for(slot, proposed=planned_kcal),
                "planned",
                None,
                protein_g,
                carbs_g,
                fat_g,
                "user",
            )
        has_planned = any(
            value is not None
            for value in (planned_protein, planned_carbs, planned_fat)
        )
        return (
            meal_kcal_for(slot, proposed=planned_kcal),
            "planned",
            None,
            planned_protein,
            planned_carbs,
            planned_fat,
            "planned" if has_planned else "none",
        )

    text = (substitute_text or "").strip() or None
    if calories_kcal is not None:
        if not (0 <= calories_kcal <= MAX_CALORIES):
            raise DietError(
                f"calories_kcal must be between 0 and {MAX_CALORIES}"
            )
        return (
            calories_kcal,
            "user",
            text,
            protein_g if user_macros else None,
            carbs_g if user_macros else None,
            fat_g if user_macros else None,
            "user" if user_macros else "none",
        )
    if text:
        estimated = estimate_meal_nutrition(text, client_factory=client_factory)
        if estimated is None:
            raise DietError(
                "Enter the calories for what you ate instead"
            )
        if user_macros:
            return (
                estimated.kcal,
                "llm",
                text,
                protein_g,
                carbs_g,
                fat_g,
                "user",
            )
        has_llm = any(
            value is not None
            for value in (estimated.protein_g, estimated.carbs_g, estimated.fat_g)
        )
        return (
            estimated.kcal,
            "llm",
            text,
            estimated.protein_g,
            estimated.carbs_g,
            estimated.fat_g,
            "llm" if has_llm else "none",
        )
    return 0, "none", None, None, None, None, "none"


def upsert_log(
    db: Session,
    user: User,
    *,
    logged_date: date | None,
    slot: str,
    outcome: str,
    recipe_id: str | None,
    title: str | None,
    substitute_text: str | None = None,
    calories_kcal: int | None = None,
    protein_g: float | None = None,
    carbs_g: float | None = None,
    fat_g: float | None = None,
    client_factory=None,
) -> DietLog:
    if slot not in SLOTS:
        raise DietError("Unknown meal slot")
    if outcome not in LOG_OUTCOMES:
        raise DietError("Unknown log outcome")
    today = clock.today(user.timezone)
    day = logged_date or today
    if day > today:
        raise DietError("Cannot log a future meal")

    planned = planned_meal_for(db, user.id, day, slot)
    kcal, source, substitute, protein, carbs, fat, macros_source = _calories_for_log(
        outcome=outcome,
        planned=planned,
        slot=slot,
        substitute_text=substitute_text,
        calories_kcal=calories_kcal,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
        client_factory=client_factory,
    )

    row = get_log(db, user.id, day, slot)
    if row is None:
        row = DietLog(user_id=user.id, logged_date=day, slot=slot)
        db.add(row)
    row.outcome = outcome
    row.recipe_id = recipe_id or (planned.recipe_id if planned else None)
    row.title = title or (planned.title if planned else None)
    row.substitute_text = substitute
    row.calories_kcal = kcal
    row.calories_source = source
    row.protein_g = protein
    row.carbs_g = carbs
    row.fat_g = fat
    row.macros_source = macros_source
    db.commit()
    db.refresh(row)
    return row


def _resolve_extra_nutrition(
    *,
    description: str,
    calories_kcal: int | None,
    protein_g: float | None,
    carbs_g: float | None,
    fat_g: float | None,
    client_factory=None,
) -> tuple[int, str, float | None, float | None, float | None, str]:
    text = (description or "").strip()
    if not text:
        raise DietError("description is required")

    user_macros = any(value is not None for value in (protein_g, carbs_g, fat_g))
    if user_macros:
        for label, value in (
            ("protein_g", protein_g),
            ("carbs_g", carbs_g),
            ("fat_g", fat_g),
        ):
            if value is not None and not (0 <= value <= 500):
                raise DietError(f"{label} must be between 0 and 500")

    if calories_kcal is not None:
        if not (0 <= calories_kcal <= MAX_CALORIES):
            raise DietError(
                f"calories_kcal must be between 0 and {MAX_CALORIES}"
            )
        macros_source = "user" if user_macros else "none"
        return (
            calories_kcal,
            "user",
            protein_g if user_macros else None,
            carbs_g if user_macros else None,
            fat_g if user_macros else None,
            macros_source,
        )

    estimated = estimate_meal_nutrition(text, client_factory=client_factory)
    if estimated is None:
        raise DietError("Enter the calories for what you ate")
    if user_macros:
        return (
            estimated.kcal,
            "llm",
            protein_g,
            carbs_g,
            fat_g,
            "user",
        )
    has_llm_macros = any(
        value is not None
        for value in (estimated.protein_g, estimated.carbs_g, estimated.fat_g)
    )
    return (
        estimated.kcal,
        "llm",
        estimated.protein_g,
        estimated.carbs_g,
        estimated.fat_g,
        "llm" if has_llm_macros else "none",
    )


def create_extra_intake(
    db: Session,
    user: User,
    *,
    description: str,
    logged_date: date | None = None,
    calories_kcal: int | None = None,
    protein_g: float | None = None,
    carbs_g: float | None = None,
    fat_g: float | None = None,
    client_factory=None,
) -> DietExtraIntake:
    today = clock.today(user.timezone)
    day = logged_date or today
    if day > today:
        raise DietError("Cannot log a future meal")
    kcal, cal_source, protein, carbs, fat, macros_source = _resolve_extra_nutrition(
        description=description,
        calories_kcal=calories_kcal,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
        client_factory=client_factory,
    )
    row = DietExtraIntake(
        user_id=user.id,
        logged_date=day,
        description=description.strip(),
        calories_kcal=kcal,
        calories_source=cal_source,
        protein_g=protein,
        carbs_g=carbs,
        fat_g=fat,
        macros_source=macros_source,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_extra_intakes(
    db: Session, user_id: str, logged_date: date | None = None
) -> list[DietExtraIntake]:
    query = db.query(DietExtraIntake).filter(DietExtraIntake.user_id == user_id)
    if logged_date is not None:
        query = query.filter(DietExtraIntake.logged_date == logged_date)
    return query.order_by(
        DietExtraIntake.logged_date.desc(), DietExtraIntake.created_at.desc()
    ).all()


def get_extra_intake(
    db: Session, user_id: str, extra_id: str
) -> DietExtraIntake | None:
    return (
        db.query(DietExtraIntake)
        .filter(DietExtraIntake.id == extra_id, DietExtraIntake.user_id == user_id)
        .one_or_none()
    )


def delete_extra_intake(db: Session, user_id: str, extra_id: str) -> bool:
    row = get_extra_intake(db, user_id, extra_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


@dataclass(frozen=True)
class Adherence:
    window_days: int
    planned: int
    eaten: int
    skipped: int
    unlogged: int
    logged_rate: float
    adherence_rate: float | None


def adherence(db: Session, user: User, window_days: int = 7) -> Adherence:
    if window_days < 1:
        raise DietError("days must be at least 1")
    today = clock.today(user.timezone)
    start = today - timedelta(days=window_days - 1)

    logs = (
        db.query(DietLog)
        .filter(
            DietLog.user_id == user.id,
            DietLog.logged_date >= start,
            DietLog.logged_date <= today,
        )
        .all()
    )
    eaten = sum(1 for row in logs if row.outcome == "eaten")
    skipped = sum(1 for row in logs if row.outcome == "skipped")
    logged_keys = {(row.logged_date, row.slot) for row in logs}

    planned = 0
    unlogged = 0
    plan = current_plan(db, user.id)
    if plan is not None:
        for meal in plan.meals:
            day = meal_date(plan, meal)
            if start <= day <= today:
                planned += 1
                if (day, meal.slot) not in logged_keys:
                    unlogged += 1

    logged_planned = planned - unlogged
    logged_rate = (logged_planned / planned) if planned else 0.0
    total_logged = eaten + skipped
    rate = (eaten / total_logged) if total_logged else None
    return Adherence(
        window_days=window_days,
        planned=planned,
        eaten=eaten,
        skipped=skipped,
        unlogged=unlogged,
        logged_rate=logged_rate,
        adherence_rate=rate,
    )


@dataclass(frozen=True)
class DayProgress:
    date: date
    target: int | None
    intake: int
    eaten: int
    skipped: int
    replaced: int
    unlogged: int
    extras: int = 0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0


@dataclass(frozen=True)
class Progress:
    window_days: int
    calorie_target: int | None
    planned: int
    eaten: int
    skipped: int
    replaced: int
    unlogged: int
    start_weight_kg: float | None
    latest_weight_kg: float | None
    target_weight_kg: float | None
    weight_progress: float | None
    days: list[DayProgress]
    weigh_ins: list[DietWeighIn]
    extras: int = 0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0


def _weight_progress(
    start: float | None, latest: float | None, target: float | None
) -> float | None:
    if start is None or latest is None or target is None:
        return None
    delta = start - target
    if abs(delta) < 0.05:
        return None
    return (start - latest) / delta


def _macro_sum(rows, attr: str) -> float:
    total = 0.0
    for row in rows:
        value = getattr(row, attr, None)
        if value is not None:
            total += float(value)
    return round(total, 1)


def progress(db: Session, user: User, window_days: int = 7) -> Progress:
    if window_days < 1:
        raise DietError("days must be at least 1")
    today = clock.today(user.timezone)
    start = today - timedelta(days=window_days - 1)
    profile = get_profile(db, user.id)
    target = resolved_calories(profile) if profile is not None else None
    target_weight = profile.target_weight_kg if profile is not None else None

    logs = (
        db.query(DietLog)
        .filter(
            DietLog.user_id == user.id,
            DietLog.logged_date >= start,
            DietLog.logged_date <= today,
        )
        .all()
    )
    extras = (
        db.query(DietExtraIntake)
        .filter(
            DietExtraIntake.user_id == user.id,
            DietExtraIntake.logged_date >= start,
            DietExtraIntake.logged_date <= today,
        )
        .all()
    )
    by_day: dict[date, list[DietLog]] = {}
    for row in logs:
        by_day.setdefault(row.logged_date, []).append(row)
    extras_by_day: dict[date, list[DietExtraIntake]] = {}
    for row in extras:
        extras_by_day.setdefault(row.logged_date, []).append(row)

    planned_keys: set[tuple[date, str]] = set()
    plan = current_plan(db, user.id)
    if plan is not None:
        for meal in plan.meals:
            day = meal_date(plan, meal)
            if start <= day <= today:
                planned_keys.add((day, meal.slot))

    days: list[DayProgress] = []
    eaten = skipped = replaced = unlogged = 0
    extras_count = 0
    protein_total = carbs_total = fat_total = 0.0
    cursor = start
    while cursor <= today:
        rows = by_day.get(cursor, [])
        day_extras = extras_by_day.get(cursor, [])
        day_eaten = sum(1 for row in rows if row.outcome == "eaten")
        day_skipped = sum(1 for row in rows if row.outcome == "skipped")
        day_replaced = sum(
            1
            for row in rows
            if row.outcome == "skipped" and row.substitute_text
        )
        logged_slots = {row.slot for row in rows}
        day_unlogged = sum(
            1
            for day, slot in planned_keys
            if day == cursor and slot not in logged_slots
        )
        intake = sum(row.calories_kcal or 0 for row in rows) + sum(
            row.calories_kcal or 0 for row in day_extras
        )
        day_protein = _macro_sum(rows, "protein_g") + _macro_sum(
            day_extras, "protein_g"
        )
        day_carbs = _macro_sum(rows, "carbs_g") + _macro_sum(day_extras, "carbs_g")
        day_fat = _macro_sum(rows, "fat_g") + _macro_sum(day_extras, "fat_g")
        eaten += day_eaten
        skipped += day_skipped
        replaced += day_replaced
        unlogged += day_unlogged
        extras_count += len(day_extras)
        protein_total += day_protein
        carbs_total += day_carbs
        fat_total += day_fat
        days.append(
            DayProgress(
                date=cursor,
                target=target,
                intake=intake,
                eaten=day_eaten,
                skipped=day_skipped,
                replaced=day_replaced,
                unlogged=day_unlogged,
                extras=len(day_extras),
                protein_g=round(day_protein, 1),
                carbs_g=round(day_carbs, 1),
                fat_g=round(day_fat, 1),
            )
        )
        cursor += timedelta(days=1)

    weigh_ins = (
        db.query(DietWeighIn)
        .filter(DietWeighIn.user_id == user.id)
        .order_by(DietWeighIn.logged_date.asc(), DietWeighIn.id.asc())
        .all()
    )
    start_weight = weigh_ins[0].weight_kg if weigh_ins else None
    latest_weight = weigh_ins[-1].weight_kg if weigh_ins else None
    return Progress(
        window_days=window_days,
        calorie_target=target,
        planned=len(planned_keys),
        eaten=eaten,
        skipped=skipped,
        replaced=replaced,
        unlogged=unlogged,
        start_weight_kg=start_weight,
        latest_weight_kg=latest_weight,
        target_weight_kg=target_weight,
        weight_progress=_weight_progress(
            start_weight, latest_weight, target_weight
        ),
        days=days,
        weigh_ins=weigh_ins,
        extras=extras_count,
        protein_g=round(protein_total, 1),
        carbs_g=round(carbs_total, 1),
        fat_g=round(fat_total, 1),
    )
