"""Fill coverage gaps from extras / recipes / macros / nutrition slices."""

import json

from app.services import llm_recipes
from app.services import nutrition as nutrition_mod
from app.services.diet import (
    DietError,
    _calories_for_log,
    _load_recipe_card,
    _resolve_extra_nutrition,
    create_extra_intake,
    load_recipe_card,
    recent_intake_blurb,
    upsert_log,
)
from app.services.llm_recipes import (
    NutritionEstimate,
    _parse_ingredient_list,
    _parse_macro_g,
    _parse_meals,
    _parse_steps_list,
    _profile_blurb,
    estimate_meal_nutrition,
)
from app.services.recipes import (
    _parse_macro,
    _parse_positive_int,
    _parse_steps,
    _valid_ingredient,
    _valid_recipe,
    load_recipes,
    recipe_card_from_recipe,
    reset_recipe_cache,
)


class TestRecipeHelpers:
    def test_parse_helpers(self):
        assert _parse_positive_int(None, default=2, lo=1, hi=12) == 2
        assert _parse_positive_int(True, default=2, lo=1, hi=12) == 2
        assert _parse_positive_int("x", default=2, lo=1, hi=12) == 2
        assert _parse_positive_int(99, default=2, lo=1, hi=12) == 2
        assert _parse_positive_int(3, default=2, lo=1, hi=12) == 3
        assert _parse_macro(None) is None
        assert _parse_macro(True) is None
        assert _parse_macro("x") is None
        assert _parse_macro(-1) is None
        assert _parse_macro(12.34) == 12.3
        assert _parse_steps("nope")[0].startswith("Prepare")
        assert _parse_steps(["", " Mix "]) == ["Mix"]
        assert _valid_ingredient({"name": "Rice", "amount": "1 cup"})["amount"] == "1 cup"
        assert recipe_card_from_recipe(None, slot="lunch", title="X", kcal=400)[
            "steps"
        ]
        card = recipe_card_from_recipe(
            {
                "ingredients": [
                    "Rice",
                    {"name": "Salt", "amount": "pinch"},
                    "",
                    {"name": ""},
                    None,
                ],
                "steps": ["Cook."],
                "servings": 2,
                "prep_min": 5,
                "cook_min": 10,
                "protein_g": 10,
            },
            slot="lunch",
            title="Rice",
            kcal=400,
        )
        assert card["ingredients"][0]["name"] == "Rice"
        assert len(card["ingredients"]) == 2

    def test_valid_recipe_rejects_and_accepts(self, tmp_path, monkeypatch):
        assert _valid_recipe({"id": "x"}) is None
        assert _valid_recipe(
            {
                "id": "x",
                "title": "T",
                "slots": ["nope"],
                "patterns": ["vegan"],
                "ingredients": [{"name": "Rice"}],
            }
        ) is None
        assert _valid_recipe(
            {
                "id": "x",
                "title": "T",
                "slots": ["lunch"],
                "patterns": ["vegan"],
                "allergens": "bad",
                "ingredients": [{"name": "Rice"}],
            }
        ) is None
        assert _valid_recipe(
            {
                "id": "x",
                "title": "T",
                "slots": ["lunch"],
                "patterns": ["vegan"],
                "ingredients": [],
            }
        ) is None
        assert _valid_recipe(
            {
                "id": "x",
                "title": "T",
                "slots": ["lunch"],
                "patterns": ["vegan"],
                "ingredients": [{"name": ""}],
            }
        ) is None
        good = _valid_recipe(
            {
                "id": "x",
                "title": "T",
                "slots": ["lunch"],
                "patterns": ["vegan"],
                "ingredients": [{"name": "Rice", "amount": "1 cup"}],
                "kcal": "bad",
                "steps": ["Cook"],
                "protein_g": 8,
            }
        )
        assert good["id"] == "x"
        missing = tmp_path / "missing.json"
        monkeypatch.setattr(
            "app.services.recipes.settings.recipes_path", str(missing)
        )
        reset_recipe_cache()
        assert load_recipes() == []


class TestLlmRecipeParse:
    def test_macro_and_ingredient_parse(self):
        assert _parse_macro_g(True) is None
        assert _parse_macro_g("x") is None
        assert _parse_macro_g(-1) is None
        assert _parse_macro_g(600) is None
        assert _parse_macro_g(12.2) == 12.2
        assert _parse_ingredient_list("no") is None
        assert _parse_ingredient_list([{"name": ""}, 3]) is None
        names, details = _parse_ingredient_list(
            ["Rice", {"name": "Salt", "amount": "1 tsp"}, "Rice", {"name": 1}]
        )
        assert names == ["Rice", "Salt"]
        assert details[1]["amount"] == "1 tsp"
        assert _parse_steps_list("x") == []
        assert _parse_steps_list(["", "Boil"]) == ["Boil"]
        blurb = _profile_blurb(
            goal="maintain",
            eating_pattern="vegan",
            allergens=[],
            meals_per_day=2,
            calorie_target=2000,
            slots=("lunch", "dinner"),
            sex="female",
            age=30,
            height_cm=165,
            weight_kg=60,
            target_weight_kg=58,
            activity="light",
            recent_intake="Recent macros were high protein.",
        )
        assert "Recent macros" in blurb
        meals = _parse_meals(
            json.dumps(
                {
                    "meals": [
                        {
                            "day_offset": 0,
                            "slot": "lunch",
                            "title": "Rice bowl",
                            "ingredients": [{"name": "Rice", "amount": "1 cup"}],
                            "steps": ["Cook"],
                            "kcal": 400,
                            "servings": "two",
                            "prep_min": True,
                            "cook_min": 20,
                            "protein_g": 12,
                        },
                        {
                            "day_offset": 0,
                            "slot": "dinner",
                            "title": "Soup",
                            "ingredients": ["Water"],
                            "servings": 2,
                        },
                    ]
                }
            ),
            ("lunch", "dinner"),
        )
        assert meals[0]["protein_g"] == 12
        assert meals[1]["servings"] == 2
        assert "prep_min" not in meals[0]

    def test_estimate_meal_nutrition_paths(self, monkeypatch):
        monkeypatch.setattr(llm_recipes.settings, "openai_api_key", "")
        assert estimate_meal_nutrition("pizza") is None
        monkeypatch.setattr(llm_recipes.settings, "openai_api_key", "sk")

        class Client:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        return type(
                            "R",
                            (),
                            {
                                "choices": [
                                    type(
                                        "C",
                                        (),
                                        {
                                            "message": type(
                                                "M",
                                                (),
                                                {
                                                    "content": json.dumps(
                                                        {
                                                            "kcal": 500,
                                                            "protein_g": 20,
                                                            "carbs_g": 40,
                                                            "fat_g": 15,
                                                        }
                                                    )
                                                },
                                            )()
                                        },
                                    )()
                                ]
                            },
                        )()

        estimated = estimate_meal_nutrition("pizza", client_factory=lambda: Client())
        assert estimated == NutritionEstimate(500, 20.0, 40.0, 15.0)


class TestDietMacroBranches:
    def test_load_recipe_card_paths(self):
        assert load_recipe_card(None) is None
        assert load_recipe_card("{") is None
        assert load_recipe_card("[1]") is None
        assert load_recipe_card('{"a": 1}') == {"a": 1}
        assert _load_recipe_card('{"a": 1}') == {"a": 1}

    def test_recent_intake_blurb(self, db, user, monkeypatch):
        assert recent_intake_blurb(db, user) is None
        monkeypatch.setattr(
            "app.services.diet.progress",
            lambda *args, **kwargs: (_ for _ in ()).throw(DietError("bad")),
        )
        assert recent_intake_blurb(db, user) is None

    def test_calories_for_log_macro_branches(self, monkeypatch):
        try:
            _calories_for_log(
                outcome="eaten",
                planned=None,
                slot="lunch",
                substitute_text=None,
                calories_kcal=None,
                protein_g=999,
            )
            assert False
        except DietError:
            pass

        kcal, source, _sub, p, c, f, ms = _calories_for_log(
            outcome="eaten",
            planned=None,
            slot="lunch",
            substitute_text=None,
            calories_kcal=None,
            protein_g=20,
            carbs_g=30,
            fat_g=10,
        )
        assert source == "planned" and ms == "user" and p == 20

        monkeypatch.setattr(
            "app.services.diet.estimate_meal_nutrition",
            lambda *a, **k: NutritionEstimate(400, 10, 20, 5),
        )
        kcal, source, sub, p, c, f, ms = _calories_for_log(
            outcome="skipped",
            planned=None,
            slot="lunch",
            substitute_text="pizza",
            calories_kcal=None,
            protein_g=11,
        )
        assert source == "llm" and ms == "user" and p == 11

        kcal, source, sub, p, c, f, ms = _calories_for_log(
            outcome="skipped",
            planned=None,
            slot="lunch",
            substitute_text="pizza",
            calories_kcal=None,
        )
        assert ms == "llm"

    def test_resolve_extra_nutrition_branches(self, monkeypatch):
        try:
            _resolve_extra_nutrition(
                description="",
                calories_kcal=None,
                protein_g=None,
                carbs_g=None,
                fat_g=None,
            )
            assert False
        except DietError:
            pass
        try:
            _resolve_extra_nutrition(
                description="snack",
                calories_kcal=None,
                protein_g=999,
                carbs_g=None,
                fat_g=None,
            )
            assert False
        except DietError:
            pass
        try:
            _resolve_extra_nutrition(
                description="snack",
                calories_kcal=9000,
                protein_g=None,
                carbs_g=None,
                fat_g=None,
            )
            assert False
        except DietError:
            pass
        monkeypatch.setattr(
            "app.services.diet.estimate_meal_nutrition",
            lambda *a, **k: NutritionEstimate(300, 8, 20, 9),
        )
        kcal, src, p, c, f, ms = _resolve_extra_nutrition(
            description="snack",
            calories_kcal=None,
            protein_g=7,
            carbs_g=None,
            fat_g=None,
        )
        assert src == "llm" and ms == "user" and p == 7
        kcal, src, p, c, f, ms = _resolve_extra_nutrition(
            description="snack",
            calories_kcal=None,
            protein_g=None,
            carbs_g=None,
            fat_g=None,
        )
        assert ms == "llm"

    def test_extra_llm_path_via_api_helper(self, db, user, monkeypatch):
        monkeypatch.setattr(
            "app.services.diet.estimate_meal_nutrition",
            lambda *a, **k: NutritionEstimate(220, None, None, None),
        )
        row = create_extra_intake(db, user, description="chips")
        assert row.calories_source == "llm"
        assert row.macros_source == "none"
        from datetime import timedelta

        from app.core import clock
        from app.services.diet import list_extra_intakes

        try:
            create_extra_intake(
                db,
                user,
                description="future",
                calories_kcal=100,
                logged_date=clock.today(user.timezone) + timedelta(days=1),
            )
            assert False
        except DietError:
            pass
        assert list_extra_intakes(db, user.id, logged_date=row.logged_date)

    def test_upsert_log_with_user_macros_on_skip(self, db, user):
        row = upsert_log(
            db,
            user,
            logged_date=None,
            slot="lunch",
            outcome="skipped",
            recipe_id=None,
            title=None,
            substitute_text="burger",
            calories_kcal=700,
            protein_g=30,
            carbs_g=40,
            fat_g=25,
        )
        assert row.macros_source == "user"
        assert row.protein_g == 30


class TestNutritionRemaining:
    def test_off_skips_non_dict_products(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"products": ["skip", {"nutriments": {}}]}

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        assert (
            nutrition_mod.lookup_open_food_facts("x", session=FakeSession()) is None
        )

    def test_exa_skips_non_dict_results(self, monkeypatch):
        monkeypatch.setattr(nutrition_mod.settings, "exa_api_key", "key")

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": ["skip", {"title": "nope"}]}

        class FakeSession:
            def post(self, *args, **kwargs):
                return FakeResponse()

        assert nutrition_mod.lookup_exa("x", session=FakeSession()) is None
