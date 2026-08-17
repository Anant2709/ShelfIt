"""Language-model week proposals: untrusted JSON, then a structured week or nothing."""

import json

import pytest
from openai import OpenAIError

from app.core import clock
from datetime import timedelta
from app.services import llm_recipes as recipes_llm
from app.services.llm_recipes import estimate_meal_calories, format_pantry, propose_week
from recipe_doubles import FakeClient, week


@pytest.fixture
def proposer(monkeypatch):
    monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "test-key")

    def _build(reply, error=None, **kwargs):
        content = reply if isinstance(reply, str) or reply is None else json.dumps(reply)
        client = FakeClient(content=content, error=error)
        extra = kwargs.pop("profile", {})
        result = propose_week(
            mode=kwargs.get("mode", "pantry"),
            goal="maintain",
            eating_pattern="vegetarian",
            allergens=["dairy"],
            meals_per_day=kwargs.get("meals_per_day", 2),
            calorie_target=2000,
            pantry=kwargs.get("pantry", [{"name": "Tomatoes", "category": "produce", "expiration_date": clock.today()}]),
            today=clock.today(),
            client_factory=lambda: client,
            **extra,
        )
        return result, client

    return _build


class TestProposeWeek:
    def test_a_full_week_is_kept(self, proposer):
        meals, client = proposer(week())
        assert meals is not None
        assert len(meals) == 14
        assert meals[0]["title"] == "Tomato rice"
        assert client.calls[0]["response_format"] == {"type": "json_object"}
        assert "On the shelf now" in client.calls[0]["messages"][1]["content"]

    def test_kcal_and_lifestyle_are_kept(self, proposer):
        meals, client = proposer(
            week(kcal=410),
            profile={
                "sex": "female",
                "age": 28,
                "activity": "light",
                "preferences": ["high_protein"],
            },
        )
        assert meals[0]["kcal"] == 410
        text = client.calls[0]["messages"][1]["content"]
        assert "high_protein" in text
        assert "activity light" in text

    def test_unusable_kcal_is_omitted(self, proposer):
        payload = week()
        payload["meals"][0]["kcal"] = 9999
        payload["meals"][1]["kcal"] = True
        meals, _ = proposer(payload)
        assert "kcal" not in meals[0]
        assert "kcal" not in meals[1]

    def test_ideal_mode_does_not_send_the_pantry(self, proposer):
        _, client = proposer(week(), mode="ideal")
        user_message = client.calls[0]["messages"][1]["content"]
        assert "On the shelf now" not in user_message
        assert "vegetarian" in user_message

    def test_no_key_is_none(self, monkeypatch):
        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "")
        assert (
            propose_week(
                mode="pantry",
                goal="maintain",
                eating_pattern="vegan",
                allergens=[],
                meals_per_day=2,
                calorie_target=2000,
                pantry=[],
                today=clock.today(),
            )
            is None
        )

    def test_unknown_mode_is_none(self, proposer):
        meals, _ = proposer(week(), mode="surprise")
        assert meals is None

    def test_provider_error_is_none(self, proposer):
        meals, _ = proposer(week(), error=OpenAIError("down"))
        assert meals is None

    def test_unusable_replies_are_dropped(self, proposer):
        assert proposer(None)[0] is None
        assert proposer("not-json")[0] is None
        assert proposer([1])[0] is None
        assert proposer({"meals": "nope"})[0] is None
        assert proposer({"meals": []})[0] is None
        assert proposer({"meals": ["x"]})[0] is None
        assert proposer({"meals": [{"day_offset": "x", "slot": "lunch", "title": "A", "ingredients": ["Rice"]}]})[0] is None
        assert proposer({"meals": [{"day_offset": 0, "slot": "brunch", "title": "A", "ingredients": ["Rice"]}]})[0] is None
        assert proposer({"meals": [{"day_offset": 0, "slot": "lunch", "title": "", "ingredients": ["Rice"]}]})[0] is None
        assert proposer({"meals": [{"day_offset": 0, "slot": "lunch", "title": "A", "ingredients": "Rice"}]})[0] is None
        assert proposer({"meals": [{"day_offset": 0, "slot": "lunch", "title": "A", "ingredients": [1, ""]}]})[0] is None

    def test_duplicate_ingredients_and_slots_are_folded(self, proposer):
        payload = week(
            extra=[
                {
                    "day_offset": 0,
                    "slot": "lunch",
                    "title": "Second lunch is ignored",
                    "ingredients": ["Tomatoes", "Tomatoes", "Rice"],
                }
            ]
        )
        meals, _ = proposer(payload)
        lunch = next(row for row in meals if row["day_offset"] == 0 and row["slot"] == "lunch")
        assert lunch["title"] == "Second lunch is ignored"
        assert lunch["ingredients"] == ["Tomatoes", "Rice"]

    def test_partial_weeks_keep_the_valid_slots(self, proposer):
        meals, _ = proposer(
            {
                "meals": [
                    {
                        "day_offset": 0,
                        "slot": "lunch",
                        "title": "Only this",
                        "ingredients": ["Rice"],
                    }
                ]
            }
        )
        assert len(meals) == 1
        assert meals[0]["title"] == "Only this"

    def test_empty_pantry_copy(self):
        assert format_pantry([], clock.today()) == "The pantry is empty."

    def test_expiry_phrases(self):
        today = clock.today()
        text = format_pantry(
            [
                {"name": "Milk", "category": "dairy", "expiration_date": None},
                {"name": "Bread", "category": None, "expiration_date": today},
                {
                    "name": "Yogurt",
                    "category": "dairy",
                    "expiration_date": today - timedelta(days=1),
                },
                {
                    "name": "Fish",
                    "category": "meat_seafood",
                    "expiration_date": today - timedelta(days=3),
                },
                {
                    "name": "Paneer",
                    "category": "dairy",
                    "expiration_date": today + timedelta(days=1),
                },
                {
                    "name": "Rice",
                    "category": "grains_pulses",
                    "expiration_date": today + timedelta(days=5),
                },
                {"name": None, "category": "produce", "expiration_date": today},
            ],
            today,
        )
        assert "no expiry date recorded" in text
        assert "expires TODAY" in text
        assert "uncategorised" in text
        assert "EXPIRED yesterday" in text
        assert "EXPIRED 3 days ago" in text
        assert "1 day left" in text
        assert "5 days left" in text
        assert "- unknown" in text


class TestEstimateCalories:
    def test_no_key_or_blank_is_none(self, monkeypatch):
        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "")
        assert estimate_meal_calories("pizza") is None
        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "test-key")
        assert estimate_meal_calories("  ") is None

    def test_a_number_is_kept(self, monkeypatch):
        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "test-key")
        client = FakeClient(content=json.dumps({"kcal": 520}))
        assert estimate_meal_calories("pizza", client_factory=lambda: client) == 520

    def test_unusable_replies_are_none(self, monkeypatch):
        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "test-key")
        assert (
            estimate_meal_calories("x", client_factory=lambda: FakeClient(content=None))
            is None
        )
        assert (
            estimate_meal_calories("x", client_factory=lambda: FakeClient(content="nope"))
            is None
        )
        assert (
            estimate_meal_calories("x", client_factory=lambda: FakeClient(content="[1]"))
            is None
        )
        assert (
            estimate_meal_calories(
                "x", client_factory=lambda: FakeClient(content=json.dumps({"kcal": 9}))
            )
            is None
        )
        assert (
            estimate_meal_calories(
                "x",
                client_factory=lambda: FakeClient(content=json.dumps({"kcal": {"n": 1}})),
            )
            is None
        )
        assert (
            estimate_meal_calories(
                "x",
                client_factory=lambda: FakeClient(content=json.dumps({"kcal": "nope"})),
            )
            is None
        )
        assert (
            estimate_meal_calories(
                "x", client_factory=lambda: FakeClient(error=OpenAIError("down"))
            )
            is None
        )
