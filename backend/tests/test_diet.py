"""Diet profile, recipe matching, plan generation, and progress.

The load-bearing rules: closed-set intake, whole-name inventory matching,
urgency as a score boost only, expired items are not cookable, calorie targets
from Mifflin-St Jeor (typed override still wins), and skip substitutes feeding
intake analytics.
"""

import json
from datetime import timedelta

import pytest

from app.core import clock
from app.core import config
from app.models.diet import DietPlanMeal
from app.models.inventory import Expiration, InventoryItem
from app.services import llm_recipes as recipes_llm
from app.services.auth import create_user
from app.services.diet import (
    DietError,
    _usable_match,
    adherence,
    current_plan,
    decorate_meal,
    generate_plan,
    get_profile,
    goal_calorie_target,
    match_recipe,
    meal_date,
    meal_kcal_for,
    mifflin_bmr,
    names_match,
    pantry_for_prompt,
    parse_allergens,
    parse_preferences,
    progress,
    resolved_calories,
    unique_names,
    upsert_log,
    upsert_profile,
    upsert_weigh_in,
)
from recipe_doubles import FakeClient, week
from app.services.recipes import (
    PLACEHOLDER_TITLE,
    load_recipes,
    questionnaire,
    recipe_by_id,
    reset_recipe_cache,
)

# female, 28, 165 cm, 62 kg, light → TDEE 1857. lose 1357, gain 2157.
PROFILE_BODY = {
    "sex": "female",
    "age": 28,
    "height_cm": 165.0,
    "weight_kg": 62.0,
    "target_weight_kg": 58.0,
    "activity": "light",
}


def add_item(db, name, days=5, user=None, resolved=False, category=None):
    owner = user or db.info["user"]
    item = InventoryItem(
        name=name,
        quantity=1.0,
        unit="count",
        user_id=owner.id,
        category=category,
        resolved_at=clock.utcnow() if resolved else None,
    )
    db.add(item)
    db.flush()
    expiration = None if days is None else clock.today() + timedelta(days=days)
    db.add(Expiration(item_id=item.id, expiration_date=expiration, source="user"))
    db.commit()
    db.refresh(item)
    return item


def save_profile(db, user=None, **overrides):
    owner = user or db.info["user"]
    payload = {
        "goal": "maintain",
        "eating_pattern": "omnivore",
        "allergens": [],
        "meals_per_day": 3,
        "calorie_target": None,
        **PROFILE_BODY,
    }
    payload.update(overrides)
    return upsert_profile(db, owner, **payload)


class TestQuestionnaire:
    def test_it_lists_the_closed_sets(self):
        body = questionnaire()
        assert "vegan" in body["eating_patterns"]
        assert "dairy" in body["allergens"]
        assert body["meals_per_day"] == [2, 3, 4]
        assert body["default_calories"]["maintain"] == 2000
        assert body["plan_modes"] == ["ideal", "pantry"]
        assert "female" in body["sexes"]
        assert "light" in body["activities"]
        assert "high_protein" in body["preferences"]
        assert body["age_range"] == [16, 90]
        assert "Mifflin" in body["calorie_disclaimer"]


class TestRecipeLoading:
    def test_a_missing_file_is_an_empty_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            config.settings, "recipes_path", str(tmp_path / "absent.json")
        )
        reset_recipe_cache()
        assert load_recipes() == []
        assert recipe_by_id("tomato-rice") is None
        assert recipe_by_id(None) is None
        assert recipe_by_id("") is None

    def test_invalid_entries_are_dropped(self, tmp_path, monkeypatch):
        path = tmp_path / "recipes.json"
        path.write_text(
            json.dumps(
                [
                    "not a recipe",
                    {"id": "", "title": "x"},
                    {"id": "ok", "title": ""},
                    {
                        "id": "slots-not-list",
                        "title": "X",
                        "slots": "lunch",
                        "patterns": ["vegan"],
                        "ingredients": [{"name": "Rice"}],
                    },
                    {
                        "id": "patterns-not-list",
                        "title": "X",
                        "slots": ["lunch"],
                        "patterns": "vegan",
                        "ingredients": [{"name": "Rice"}],
                    },
                    {
                        "id": "no-slots",
                        "title": "X",
                        "slots": ["brunch"],
                        "patterns": ["vegan"],
                        "ingredients": [{"name": "Rice"}],
                    },
                    {
                        "id": "no-patterns",
                        "title": "X",
                        "slots": ["lunch"],
                        "patterns": ["carnivore"],
                        "ingredients": [{"name": "Rice"}],
                    },
                    {
                        "id": "bad-allergens",
                        "title": "X",
                        "slots": ["lunch"],
                        "patterns": ["vegan"],
                        "allergens": "dairy",
                        "ingredients": [{"name": "Rice"}],
                    },
                    {
                        "id": "no-ingredients",
                        "title": "X",
                        "slots": ["lunch"],
                        "patterns": ["vegan"],
                        "ingredients": [],
                    },
                    {
                        "id": "bad-ingredient-list",
                        "title": "X",
                        "slots": ["lunch"],
                        "patterns": ["vegan"],
                        "ingredients": "rice",
                    },
                    {
                        "id": "unusable-ingredients",
                        "title": "X",
                        "slots": ["lunch"],
                        "patterns": ["vegan"],
                        "ingredients": [{"aliases": ["Rice"]}, "x"],
                    },
                    {
                        "id": "bad-alias-type",
                        "title": "X",
                        "slots": ["lunch"],
                        "patterns": ["vegan"],
                        "ingredients": [{"name": "Rice", "aliases": "Rice"}],
                    },
                    {
                        "id": "keep",
                        "title": "Keeper",
                        "slots": ["lunch"],
                        "patterns": ["vegan"],
                        "allergens": ["dairy", "not-a-thing"],
                        "ingredients": [
                            {"name": "Rice", "aliases": ["Basmati Rice", 1, ""]}
                        ],
                        "kcal": 400,
                    },
                    {
                        "id": "kcal-bool",
                        "title": "X",
                        "slots": ["lunch"],
                        "patterns": ["vegan"],
                        "ingredients": [{"name": "Rice"}],
                        "kcal": True,
                    },
                    {
                        "id": "kcal-bad",
                        "title": "X",
                        "slots": ["lunch"],
                        "patterns": ["vegan"],
                        "ingredients": [{"name": "Rice"}],
                        "kcal": "nope",
                    },
                    {
                        "id": "kcal-low",
                        "title": "X",
                        "slots": ["lunch"],
                        "patterns": ["vegan"],
                        "ingredients": [{"name": "Rice"}],
                        "kcal": 10,
                    },
                    {
                        "id": "keep",
                        "title": "Duplicate id is dropped",
                        "slots": ["dinner"],
                        "patterns": ["vegan"],
                        "ingredients": [{"name": "Rice"}],
                    },
                    {"not": "a recipe"},
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(config.settings, "recipes_path", str(path))
        reset_recipe_cache()
        loaded = load_recipes()
        assert [recipe["id"] for recipe in loaded] == [
            "keep",
            "kcal-bool",
            "kcal-bad",
            "kcal-low",
        ]
        assert loaded[0]["allergens"] == ["dairy"]
        assert loaded[0]["kcal"] == 400
        assert loaded[1]["kcal"] is None
        assert loaded[2]["kcal"] is None
        assert loaded[3]["kcal"] is None
        assert "Basmati Rice" in loaded[0]["ingredients"][0]["aliases"]

    def test_a_non_list_file_is_empty(self, tmp_path, monkeypatch):
        path = tmp_path / "recipes.json"
        path.write_text(json.dumps({"id": "nope"}), encoding="utf-8")
        monkeypatch.setattr(config.settings, "recipes_path", str(path))
        reset_recipe_cache()
        assert load_recipes() == []

    def test_the_second_load_uses_the_cache(self, recipes):
        first = load_recipes()
        assert load_recipes() is first

    def test_editing_the_file_is_picked_up(self, recipes):
        assert load_recipes()
        recipes.write_text("[]", encoding="utf-8")
        assert load_recipes() == []


class TestProfile:
    def test_it_is_created_and_updated(self, db, user):
        created = save_profile(db, goal="lose_weight", allergens=["dairy", "dairy"])
        assert created.user_id == user.id
        assert parse_allergens(created.allergens) == ["dairy"]
        assert resolved_calories(created) == 1357
        updated = save_profile(db, calorie_target=1800)
        assert get_profile(db, user.id).calorie_target == 1800
        assert resolved_calories(updated) == 1800
        assert updated.user_id == created.user_id
        assert created.sex == "female"
        assert created.weight_kg == 62.0
        unique = save_profile(db, preferences=["spicy", "budget", "spicy"])
        assert parse_preferences(unique.preferences) == ["budget", "spicy"]
        assert unique.cooking_time == "about_30"

    def test_unknown_values_are_refused(self, db, user):
        with pytest.raises(DietError, match="Unknown goal"):
            save_profile(db, goal="bulk")
        with pytest.raises(DietError, match="eating pattern"):
            save_profile(db, eating_pattern="pescatarian")
        with pytest.raises(DietError, match="meals_per_day"):
            save_profile(db, meals_per_day=5)
        with pytest.raises(DietError, match="Unknown allergen"):
            save_profile(db, allergens=["nightshade"])
        with pytest.raises(DietError, match="calorie_target"):
            save_profile(db, calorie_target=800)
        with pytest.raises(DietError, match="calorie_target"):
            save_profile(db, calorie_target=9000)
        with pytest.raises(DietError, match="Unknown sex"):
            save_profile(db, sex="other")
        with pytest.raises(DietError, match="activity"):
            save_profile(db, activity="extreme")
        with pytest.raises(DietError, match="cooking time"):
            save_profile(db, cooking_time="all_day")
        with pytest.raises(DietError, match="age"):
            save_profile(db, age=12)
        with pytest.raises(DietError, match="height_cm"):
            save_profile(db, height_cm=80)
        with pytest.raises(DietError, match="weight_kg"):
            save_profile(db, weight_kg=10)
        with pytest.raises(DietError, match="target_weight_kg"):
            save_profile(db, target_weight_kg=10)
        with pytest.raises(DietError, match="Unknown preference"):
            save_profile(db, preferences=["keto"])

    def test_corrupt_allergen_json_is_empty(self):
        assert parse_allergens("{") == []
        assert parse_allergens("1") == []
        assert parse_allergens('["dairy"]') == ["dairy"]
        assert parse_preferences("{") == []
        assert parse_preferences("1") == []
        assert parse_preferences('["high_protein", "nope"]') == ["high_protein"]


class TestMatching:
    def test_whole_name_match_uses_the_inventory_name(self, db, recipes):
        add_item(db, "Basmati Rice", days=20)
        add_item(db, "Tomatoes", days=5)
        recipe = recipe_by_id("tomato-rice")
        items = db.query(InventoryItem).all()
        uses, missing, score = match_recipe(recipe, items, clock.today())
        assert uses == ["Tomatoes", "Basmati Rice"]
        assert missing == []
        assert score == 20

    def test_milk_does_not_match_coconut_milk(self, db, recipes):
        add_item(db, "Coconut Milk", days=10)
        recipe = {
            "id": "tea",
            "title": "Tea",
            "slots": ["breakfast"],
            "patterns": ["vegan"],
            "allergens": [],
            "ingredients": [{"name": "Milk", "aliases": ["Milk"]}],
        }
        uses, missing, _ = match_recipe(recipe, db.query(InventoryItem).all(), clock.today())
        assert uses == []
        assert missing == ["Milk"]

    def test_chicken_matches_chicken_breast(self):
        assert names_match("chicken", "Chicken Breast") is True
        assert names_match("Chicken Breast", "Chicken") is True
        assert names_match("rice", "Basmati Rice") is True
        assert names_match("milk", "Coconut Milk") is False
        assert names_match("tomato", "Tomatoes") is True
        assert names_match("tomatoes", "Tomato") is True
        assert names_match("apple", "apples") is True
        assert names_match("apples", "apple") is True
        assert names_match("box", "boxes") is True
        assert names_match("boxes", "box") is True
        assert names_match("", "Milk") is False

    def test_shopping_list_dedupes(self):
        assert unique_names([["Rice", "Tomatoes"], ["rice", "Oil"], [""]]) == [
            "Rice",
            "Tomatoes",
            "Oil",
        ]

    def test_pantry_prompt_hides_expired_and_forbidden_foods(self, db):
        today = clock.today()
        add_item(db, "Milk", days=2, category="dairy")
        add_item(db, "Chicken Breast", days=2, category="meat_seafood")
        add_item(db, "Baby Spinach", days=-1, category="produce")
        add_item(db, "Tomatoes", days=5, category="produce")
        add_item(db, "Whole Wheat Bread", days=1, category="bakery")
        visible = {
            row["name"]
            for row in pantry_for_prompt(
                db.query(InventoryItem).all(),
                today,
                eating_pattern="vegan",
                allergens=["gluten"],
            )
        }
        assert visible == {"Tomatoes"}

    def test_allergen_words_are_stripped_from_the_pantry(self, db):
        add_item(db, "Eggs", days=5, category="dairy")
        add_item(db, "Peanut Butter", days=5, category="pantry")
        add_item(db, "Soy Sauce", days=5, category="spices_condiments")
        add_item(db, "Shrimp", days=2, category="meat_seafood")
        add_item(db, "Rice", days=20, category="grains_pulses")
        visible = {
            row["name"]
            for row in pantry_for_prompt(
                db.query(InventoryItem).all(),
                clock.today(),
                eating_pattern="omnivore",
                allergens=["eggs", "nuts", "soy", "shellfish"],
            )
        }
        assert visible == {"Rice"}

    def test_dairy_allergen_hides_dairy_for_an_omnivore(self, db):
        add_item(db, "Milk", days=2, category="dairy")
        add_item(db, "Tomatoes", days=5, category="produce")
        visible = {
            row["name"]
            for row in pantry_for_prompt(
                db.query(InventoryItem).all(),
                clock.today(),
                eating_pattern="omnivore",
                allergens=["dairy"],
            )
        }
        assert visible == {"Tomatoes"}

    def test_expired_items_are_not_usable(self, db, recipes):
        add_item(db, "Tomatoes", days=-1)
        add_item(db, "Basmati Rice", days=20)
        uses, missing, _ = match_recipe(
            recipe_by_id("tomato-rice"),
            db.query(InventoryItem).all(),
            clock.today(),
        )
        assert "Tomatoes" not in uses
        assert "Tomatoes" in missing
        assert "Basmati Rice" in uses

    def test_resolved_items_are_ignored_when_planning(self, db, user, recipes):
        add_item(db, "Tomatoes", days=5, resolved=True)
        add_item(db, "Basmati Rice", days=20)
        save_profile(db, eating_pattern="vegan")
        plan = generate_plan(db, user)
        lunch = next(meal for meal in plan.meals if meal.slot == "lunch" and meal.day_offset == 0)
        assert "Tomatoes" in json.loads(lunch.missing_json)

    def test_urgency_breaks_a_tie_between_same_named_items(self, db, recipes):
        later = add_item(db, "Tomatoes", days=10)
        soon = add_item(db, "Tomatoes", days=1)
        add_item(db, "Basmati Rice", days=20)
        items = db.query(InventoryItem).all()
        match = _usable_match(
            {"name": "Tomatoes", "aliases": ["Tomatoes"]},
            items,
            clock.today(),
        )
        assert match.id == soon.id
        uses, _, score = match_recipe(
            recipe_by_id("tomato-rice"), items, clock.today()
        )
        assert "Tomatoes" in uses
        assert score == 25
        assert later.id != soon.id

    def test_undated_items_are_usable_but_not_urgent(self, db, recipes):
        add_item(db, "Tomatoes", days=None)
        add_item(db, "Basmati Rice", days=None)
        uses, missing, score = match_recipe(
            recipe_by_id("tomato-rice"),
            db.query(InventoryItem).all(),
            clock.today(),
        )
        assert sorted(uses) == ["Basmati Rice", "Tomatoes"]
        assert missing == []
        assert score == 20


class TestPlanGeneration:
    def test_a_profile_is_required(self, db, user, recipes):
        with pytest.raises(DietError, match="profile"):
            generate_plan(db, user)

    def test_unknown_mode_is_refused(self, db, user, recipes):
        save_profile(db)
        with pytest.raises(DietError, match="plan mode"):
            generate_plan(db, user, mode="surprise")

    def test_vegetarian_never_gets_chicken(self, db, user, recipes):
        add_item(db, "Chicken Breast", days=2)
        add_item(db, "Tomatoes", days=5)
        add_item(db, "Basmati Rice", days=20)
        save_profile(db, eating_pattern="vegetarian")
        plan = generate_plan(db, user)
        titles = {meal.title for meal in plan.meals}
        assert "Chicken and rice" not in titles
        assert "Paneer and tomato" in titles or "Tomato rice" in titles

    def test_vegan_never_gets_paneer(self, db, user, recipes):
        add_item(db, "Paneer", days=1)
        add_item(db, "Tomatoes", days=5)
        add_item(db, "Basmati Rice", days=20)
        save_profile(db, eating_pattern="vegan")
        plan = generate_plan(db, user)
        assert {meal.recipe_id for meal in plan.meals} == {"tomato-rice"}

    def test_an_allergen_excludes_the_recipe(self, db, user, recipes):
        add_item(db, "Paneer", days=1)
        add_item(db, "Tomatoes", days=5)
        add_item(db, "Basmati Rice", days=20)
        save_profile(db, eating_pattern="vegetarian", allergens=["dairy"])
        plan = generate_plan(db, user)
        assert {meal.recipe_id for meal in plan.meals} == {"tomato-rice"}

    def test_another_users_fridge_is_invisible(self, db, user, recipes):
        other = create_user(db, email="other@local", password="password1")
        add_item(db, "Chicken Breast", days=1, user=other)
        add_item(db, "Tomatoes", days=5)
        add_item(db, "Basmati Rice", days=20)
        save_profile(db, eating_pattern="omnivore")
        plan = generate_plan(db, user)
        lunch = next(
            meal for meal in plan.meals if meal.slot == "lunch" and meal.day_offset == 0
        )
        assert "Chicken Breast" not in json.loads(lunch.uses_json)

    def test_llm_pantry_plan_matches_the_shelf(self, db, user, monkeypatch):
        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "test-key")
        add_item(db, "Tomatoes", days=5)
        add_item(db, "Basmati Rice", days=20)
        save_profile(db, eating_pattern="vegan", meals_per_day=2)
        client = FakeClient(content=json.dumps(week(ingredients=["Tomatoes", "Cumin"])))
        plan = generate_plan(
            db, user, mode="pantry", client_factory=lambda: client
        )
        assert plan.mode == "pantry"
        lunch = next(
            meal for meal in plan.meals if meal.slot == "lunch" and meal.day_offset == 0
        )
        assert lunch.title == "Tomato rice"
        assert json.loads(lunch.uses_json) == ["Tomatoes"]
        assert "Cumin" in json.loads(lunch.missing_json)

    def test_llm_ideal_plan_lists_what_to_buy(self, db, user, monkeypatch):
        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "test-key")
        add_item(db, "Tomatoes", days=5)
        save_profile(db, meals_per_day=2)
        client = FakeClient(
            content=json.dumps(week(title="Dal", ingredients=["Lentils", "Tomatoes"]))
        )
        plan = generate_plan(
            db, user, mode="ideal", client_factory=lambda: client
        )
        assert plan.mode == "ideal"
        lunch = next(
            meal for meal in plan.meals if meal.slot == "lunch" and meal.day_offset == 0
        )
        assert "Tomatoes" in json.loads(lunch.uses_json)
        assert "Lentils" in json.loads(lunch.missing_json)

    def test_llm_gaps_become_placeholders(self, db, user, monkeypatch):
        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "test-key")
        save_profile(db, meals_per_day=2)
        client = FakeClient(
            content=json.dumps(
                {
                    "meals": [
                        {
                            "day_offset": 0,
                            "slot": "lunch",
                            "title": "Soup",
                            "ingredients": ["Tomatoes"],
                        }
                    ]
                }
            )
        )
        plan = generate_plan(db, user, client_factory=lambda: client)
        titles = {(meal.day_offset, meal.slot): meal.title for meal in plan.meals}
        assert titles[(0, "lunch")] == "Soup"
        assert titles[(0, "dinner")] == PLACEHOLDER_TITLE
        dinner = next(
            meal
            for meal in plan.meals
            if meal.day_offset == 0 and meal.slot == "dinner"
        )
        assert dinner.kcal == 600

    def test_empty_recipes_produce_placeholders(self, db, user):
        save_profile(db)
        plan = generate_plan(db, user)
        assert {meal.title for meal in plan.meals} == {PLACEHOLDER_TITLE}
        assert all(meal.recipe_id is None for meal in plan.meals)

    def test_regenerating_appends_a_new_current_plan(self, db, user, recipes):
        save_profile(db)
        first = generate_plan(db, user)
        second = generate_plan(db, user)
        assert first.id != second.id
        assert current_plan(db, user.id).id == second.id

    def test_meals_per_day_selects_the_slots(self, db, user, recipes):
        save_profile(db, meals_per_day=2)
        plan = generate_plan(db, user)
        assert plan.meals_per_day == 2
        assert {meal.slot for meal in plan.meals} == {"lunch", "dinner"}

    def test_calorie_default_is_snapshotted(self, db, user, recipes):
        save_profile(db, goal="gain_weight")
        plan = generate_plan(db, user)
        assert plan.calorie_target == 2157

    def test_rotation_reuses_when_the_set_is_exhausted(self, db, user, recipes):
        save_profile(db, eating_pattern="vegan", meals_per_day=4)
        plan = generate_plan(db, user)
        assert len(plan.meals) == 28
        assert {meal.recipe_id for meal in plan.meals} == {"tomato-rice"}


class TestDecorateAndLog:
    def test_decorate_falls_back_to_the_snapshot(self, db, user, recipes):
        save_profile(db)
        plan = generate_plan(db, user)
        meal = DietPlanMeal(
            plan_id=plan.id,
            day_offset=0,
            slot="lunch",
            recipe_id="gone",
            title="Old dish",
            uses_json='["Milk"]',
            missing_json="not-json",
        )
        uses, missing = decorate_meal(plan, meal, [], clock.today())
        assert uses == ["Milk"]
        assert missing == []
        meal.missing_json = "1"
        _, missing = decorate_meal(plan, meal, [], clock.today())
        assert missing == []

    def test_decorate_recomputes_from_a_fallback_recipe(self, db, user, recipes):
        save_profile(db)
        plan = generate_plan(db, user)
        add_item(db, "Tomatoes", days=5)
        add_item(db, "Basmati Rice", days=20)
        meal = DietPlanMeal(
            plan_id=plan.id,
            day_offset=0,
            slot="lunch",
            recipe_id="tomato-rice",
            title="Tomato rice",
            uses_json="[]",
            missing_json="[]",
            ingredients_json="[]",
        )
        uses, missing = decorate_meal(
            plan, meal, db.query(InventoryItem).all(), clock.today()
        )
        assert "Tomatoes" in uses
        assert "Basmati Rice" in uses
        assert missing == []

    def test_logging_upserts_and_refuses_the_future(self, db, user):
        row = upsert_log(
            db,
            user,
            logged_date=None,
            slot="lunch",
            outcome="eaten",
            recipe_id="tomato-rice",
            title="Tomato rice",
        )
        again = upsert_log(
            db,
            user,
            logged_date=row.logged_date,
            slot="lunch",
            outcome="skipped",
            recipe_id="tomato-rice",
            title="Tomato rice",
        )
        assert again.id == row.id
        assert again.outcome == "skipped"
        with pytest.raises(DietError, match="Unknown meal slot"):
            upsert_log(
                db, user, logged_date=None, slot="brunch", outcome="eaten",
                recipe_id=None, title=None,
            )
        with pytest.raises(DietError, match="Unknown log outcome"):
            upsert_log(
                db, user, logged_date=None, slot="lunch", outcome="tasted",
                recipe_id=None, title=None,
            )
        with pytest.raises(DietError, match="future"):
            upsert_log(
                db,
                user,
                logged_date=clock.today() + timedelta(days=1),
                slot="lunch",
                outcome="eaten",
                recipe_id=None,
                title=None,
            )


class TestAdherence:
    def test_empty_state_is_zeros_not_a_rate(self, db, user):
        report = adherence(db, user)
        assert report.planned == 0
        assert report.eaten == 0
        assert report.adherence_rate is None
        assert report.logged_rate == 0.0

    def test_it_counts_meals_not_calories(self, db, user, recipes):
        save_profile(db, meals_per_day=2)
        plan = generate_plan(db, user)
        today = clock.today()
        assert meal_date(plan, plan.meals[0]) == today
        upsert_log(
            db, user, logged_date=today, slot="lunch", outcome="eaten",
            recipe_id=None, title=None,
        )
        upsert_log(
            db, user, logged_date=today, slot="dinner", outcome="skipped",
            recipe_id=None, title=None,
        )
        report = adherence(db, user, window_days=7)
        assert report.planned == 2
        assert report.eaten == 1
        assert report.skipped == 1
        assert report.unlogged == 0
        assert report.logged_rate == 1.0
        assert report.adherence_rate == 0.5

    def test_a_bad_window_is_refused(self, db, user):
        with pytest.raises(DietError, match="days"):
            adherence(db, user, window_days=0)


class TestCalories:
    def test_mifflin_sex_constants(self):
        female = mifflin_bmr(62, 165, 28, "female")
        male = mifflin_bmr(62, 165, 28, "male")
        midpoint = mifflin_bmr(62, 165, 28, "prefer_not")
        assert female == pytest.approx(1350.25)
        assert male == pytest.approx(1516.25)
        assert midpoint == pytest.approx((female + male) / 2)

    def test_goal_adjusts_and_clamps(self):
        assert goal_calorie_target(1857, "lose_weight") == 1357
        assert goal_calorie_target(1857, "gain_weight") == 2157
        assert goal_calorie_target(1857, "maintain") == 1857
        assert goal_calorie_target(1857, "eat_healthier") == 1857
        assert goal_calorie_target(1000, "lose_weight") == 1200
        assert goal_calorie_target(5000, "gain_weight") == 4000
        assert goal_calorie_target(5000, "maintain") == 4000

    def test_typed_override_wins(self, db, user):
        profile = save_profile(db, calorie_target=2100)
        assert resolved_calories(profile) == 2100

    def test_fallback_meals_carry_kcal(self, db, user, recipes):
        save_profile(db, meals_per_day=2)
        plan = generate_plan(db, user)
        lunch = next(
            meal for meal in plan.meals if meal.slot == "lunch" and meal.day_offset == 0
        )
        assert lunch.kcal == recipe_by_id(lunch.recipe_id)["kcal"]
        assert meal_kcal_for("brunch") == 400

    def test_llm_kcal_is_stored(self, db, user, monkeypatch):
        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "test-key")
        save_profile(db, meals_per_day=2, preferences=["high_protein"])
        client = FakeClient(content=json.dumps(week(kcal=430)))
        plan = generate_plan(db, user, client_factory=lambda: client)
        lunch = next(
            meal for meal in plan.meals if meal.slot == "lunch" and meal.day_offset == 0
        )
        assert lunch.kcal == 430
        user_message = client.calls[0]["messages"][1]["content"]
        assert "high_protein" in user_message
        assert "activity light" in user_message
        assert "target weight" in user_message


class TestSkipSubstitutesAndProgress:
    def test_eaten_uses_planned_kcal(self, db, user, recipes):
        save_profile(db, meals_per_day=2)
        plan = generate_plan(db, user)
        lunch = next(
            meal for meal in plan.meals if meal.slot == "lunch" and meal.day_offset == 0
        )
        row = upsert_log(
            db, user, logged_date=clock.today(), slot="lunch",
            outcome="eaten", recipe_id=None, title=None,
        )
        assert row.calories_source == "planned"
        assert row.calories_kcal == lunch.kcal
        assert row.title == lunch.title

    def test_skip_with_user_calories(self, db, user):
        row = upsert_log(
            db, user, logged_date=None, slot="lunch", outcome="skipped",
            recipe_id=None, title=None,
            substitute_text="  leftover pizza  ",
            calories_kcal=700,
        )
        assert row.calories_source == "user"
        assert row.calories_kcal == 700
        assert row.substitute_text == "leftover pizza"

    def test_skip_without_calories_needs_a_number(self, db, user):
        with pytest.raises(DietError, match="calories"):
            upsert_log(
                db, user, logged_date=None, slot="lunch", outcome="skipped",
                recipe_id=None, title=None, substitute_text="pizza",
            )

    def test_skip_llm_estimate(self, db, user, monkeypatch):
        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "test-key")
        client = FakeClient(content=json.dumps({"kcal": 640}))
        row = upsert_log(
            db, user, logged_date=None, slot="dinner", outcome="skipped",
            recipe_id=None, title=None,
            substitute_text="two dosas",
            client_factory=lambda: client,
        )
        assert row.calories_source == "llm"
        assert row.calories_kcal == 640

    def test_skip_empty_is_zero(self, db, user):
        row = upsert_log(
            db, user, logged_date=None, slot="snack", outcome="skipped",
            recipe_id=None, title=None,
        )
        assert row.calories_kcal == 0
        assert row.calories_source == "none"
        assert row.substitute_text is None

    def test_user_calories_out_of_range(self, db, user):
        with pytest.raises(DietError, match="calories_kcal"):
            upsert_log(
                db, user, logged_date=None, slot="lunch", outcome="skipped",
                recipe_id=None, title=None, substitute_text="buffet",
                calories_kcal=9000,
            )

    def test_weigh_in_writes_history_and_progress(self, db, user, recipes):
        save_profile(db, meals_per_day=2)
        generate_plan(db, user)
        today = clock.today()
        upsert_weigh_in(
            db, user, weight_kg=66.0, logged_date=today - timedelta(days=6)
        )
        upsert_log(
            db, user, logged_date=today, slot="lunch", outcome="eaten",
            recipe_id=None, title=None,
        )
        upsert_log(
            db, user, logged_date=today, slot="dinner", outcome="skipped",
            recipe_id=None, title=None,
            substitute_text="takeout", calories_kcal=800,
        )
        report = progress(db, user, window_days=7)
        assert report.start_weight_kg == 66.0
        assert report.latest_weight_kg == 62.0
        assert report.target_weight_kg == 58.0
        assert report.weight_progress == pytest.approx((66 - 62) / (66 - 58))
        assert report.eaten == 1
        assert report.skipped == 1
        assert report.replaced == 1
        today_row = next(day for day in report.days if day.date == today)
        assert today_row.replaced == 1
        assert today_row.intake >= 800
        with pytest.raises(DietError, match="days"):
            progress(db, user, window_days=0)
        with pytest.raises(DietError, match="future"):
            upsert_weigh_in(
                db, user, weight_kg=60, logged_date=today + timedelta(days=1)
            )
        with pytest.raises(DietError, match="weight_kg"):
            upsert_weigh_in(db, user, weight_kg=10)

    def test_no_weight_direction_is_none(self, db):
        other = create_user(db, email="hold@local", password="password1")
        save_profile(db, user=other, weight_kg=70, target_weight_kg=70)
        report = progress(db, other)
        assert report.start_weight_kg == 70
        assert report.target_weight_kg == 70
        assert report.weight_progress is None

    def test_progress_empty_state(self, db, user):
        report = progress(db, user)
        assert report.planned == 0
        assert report.calorie_target is None
        assert report.weigh_ins == []
        assert report.days[-1].intake == 0

    def test_weigh_in_without_a_profile_is_history_only(self, db, user):
        row = upsert_weigh_in(db, user, weight_kg=70)
        assert row.weight_kg == 70
        assert get_profile(db, user.id) is None

    def test_logging_an_unplanned_slot_still_works(self, db, user, recipes):
        save_profile(db, meals_per_day=2)
        generate_plan(db, user)
        row = upsert_log(
            db, user, logged_date=None, slot="snack", outcome="eaten",
            recipe_id=None, title=None,
        )
        assert row.calories_source == "planned"
        assert row.calories_kcal == 200

