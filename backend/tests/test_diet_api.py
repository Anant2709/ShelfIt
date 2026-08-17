"""HTTP surface for the diet module, including isolation."""

from datetime import timedelta

from app.core import clock
from app.models.inventory import Expiration, InventoryItem
from app.services.auth import COOKIE_NAME, create_session, create_user


PROFILE_BODY = {
    "sex": "female",
    "age": 28,
    "height_cm": 165,
    "weight_kg": 62,
    "target_weight_kg": 58,
    "activity": "light",
}


def add_item(db, name, days=5, user=None):
    owner = user or db.info["user"]
    item = InventoryItem(
        name=name, quantity=1.0, unit="count", user_id=owner.id
    )
    db.add(item)
    db.flush()
    db.add(
        Expiration(
            item_id=item.id,
            expiration_date=clock.today() + timedelta(days=days),
            source="user",
        )
    )
    db.commit()
    return item


def save_profile(client, **overrides):
    payload = {
        "goal": "maintain",
        "eating_pattern": "omnivore",
        "allergens": [],
        "meals_per_day": 3,
        "calorie_target": None,
        **PROFILE_BODY,
    }
    payload.update(overrides)
    return client.put("/api/diet/profile", json=payload)


class TestAuthGate:
    def test_every_diet_route_requires_a_session(self, anonymous_client):
        assert anonymous_client.get("/api/diet/questionnaire").status_code == 401
        assert anonymous_client.get("/api/diet/profile").status_code == 401
        assert anonymous_client.put(
            "/api/diet/profile",
            json={
                "goal": "maintain",
                "eating_pattern": "omnivore",
                "meals_per_day": 3,
                **PROFILE_BODY,
            },
        ).status_code == 401
        assert anonymous_client.get("/api/diet/plan").status_code == 401
        assert anonymous_client.post("/api/diet/plan").status_code == 401
        assert anonymous_client.get("/api/diet/today").status_code == 401
        assert anonymous_client.post(
            "/api/diet/log", json={"slot": "lunch", "outcome": "eaten"}
        ).status_code == 401
        assert anonymous_client.get("/api/diet/adherence").status_code == 401
        assert anonymous_client.post(
            "/api/diet/weigh-ins", json={"weight_kg": 60}
        ).status_code == 401
        assert anonymous_client.get("/api/diet/progress").status_code == 401
        assert anonymous_client.get("/api/diet/extras").status_code == 401
        assert anonymous_client.post(
            "/api/diet/extras", json={"description": "snack", "calories_kcal": 100}
        ).status_code == 401


class TestProfileAndPlan:
    def test_questionnaire_is_the_closed_set(self, client):
        body = client.get("/api/diet/questionnaire").json()
        assert "vegan" in body["eating_patterns"]
        assert body["plan_days"] == 7
        assert "ideal" in body["plan_modes"]
        assert "female" in body["sexes"]
        assert "Mifflin" in body["calorie_disclaimer"]

    def test_missing_profile_and_plan_are_404(self, client):
        assert client.get("/api/diet/profile").status_code == 404
        assert client.get("/api/diet/plan").status_code == 404

    def test_today_without_a_plan_is_empty(self, client):
        body = client.get("/api/diet/today").json()
        assert body["meals"] == []
        assert body["calorie_target"] is None

    def test_plan_requires_a_profile(self, client, recipes):
        response = client.post("/api/diet/plan")
        assert response.status_code == 400
        assert "profile" in response.json()["detail"]

    def test_invalid_profile_is_422(self, client):
        response = save_profile(client, goal="bulk")
        assert response.status_code == 422

    def test_the_round_trip(self, client, db, recipes):
        add_item(db, "Tomatoes")
        add_item(db, "Basmati Rice")
        saved = save_profile(client, eating_pattern="vegan", meals_per_day=2)
        assert saved.status_code == 200
        assert saved.json()["resolved_calorie_target"] == 1857
        assert saved.json()["tdee"] == 1857
        assert saved.json()["weight_kg"] == 62
        assert client.get("/api/diet/profile").json()["eating_pattern"] == "vegan"

        created = client.post("/api/diet/plan")
        assert created.status_code == 200
        assert created.json()["meals_per_day"] == 2
        assert {meal["slot"] for meal in created.json()["meals"]} == {
            "lunch",
            "dinner",
        }
        fetched = client.get("/api/diet/plan")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == created.json()["id"]
        assert fetched.json()["mode"] == "pantry"
        assert "shopping_list" in fetched.json()

        today = client.get("/api/diet/today").json()
        assert today["calorie_target"] == 1857
        assert today["mode"] == "pantry"
        assert len(today["meals"]) == 2
        assert "Basmati Rice" in today["meals"][0]["uses"]

        logged = client.post(
            "/api/diet/log",
            json={
                "slot": "lunch",
                "outcome": "eaten",
                "recipe_id": today["meals"][0]["recipe_id"],
                "title": today["meals"][0]["title"],
            },
        )
        assert logged.status_code == 200
        assert logged.json()["outcome"] == "eaten"
        assert logged.json()["calories_source"] == "planned"
        today_after = client.get("/api/diet/today").json()
        lunch = next(meal for meal in today_after["meals"] if meal["slot"] == "lunch")
        assert lunch["log"]["outcome"] == "eaten"
        assert lunch["kcal"] == logged.json()["calories_kcal"]

        report = client.get("/api/diet/adherence?days=7").json()
        assert report["planned"] == 2
        assert report["eaten"] == 1
        assert report["skipped"] == 0
        assert report["unlogged"] == 1
        assert report["adherence_rate"] == 1.0

    def test_a_bad_adherence_window_is_422(self, client):
        response = client.get("/api/diet/adherence?days=0")
        assert response.status_code == 422

    def test_unknown_plan_mode_is_422(self, client, recipes):
        save_profile(client)
        response = client.post("/api/diet/plan?mode=surprise")
        assert response.status_code == 422

    def test_ideal_mode_uses_the_fallback_set(self, client, db, recipes):
        add_item(db, "Tomatoes")
        save_profile(client, eating_pattern="vegan", meals_per_day=2)
        created = client.post("/api/diet/plan?mode=ideal")
        assert created.status_code == 200
        assert created.json()["mode"] == "ideal"
        assert isinstance(created.json()["shopping_list"], list)

    def test_a_future_log_is_422(self, client):
        tomorrow = (clock.today() + timedelta(days=1)).isoformat()
        response = client.post(
            "/api/diet/log",
            json={"slot": "lunch", "outcome": "eaten", "logged_date": tomorrow},
        )
        assert response.status_code == 422


class TestIsolation:
    def other_client(self, db, anonymous_client):
        other = create_user(db, email="diet-other@local", password="password1")
        token = create_session(db, other)
        anonymous_client.cookies.set(COOKIE_NAME, token)
        return anonymous_client, other

    def test_another_users_profile_is_not_visible(self, db, client, anonymous_client):
        save_profile(client, goal="lose_weight")
        other, _ = self.other_client(db, anonymous_client)
        assert other.get("/api/diet/profile").status_code == 404

    def test_another_users_plan_is_not_visible(
        self, db, client, anonymous_client, recipes
    ):
        save_profile(client)
        client.post("/api/diet/plan")
        other, _ = self.other_client(db, anonymous_client)
        assert other.get("/api/diet/plan").status_code == 404
        assert other.get("/api/diet/today").json()["meals"] == []

    def test_another_users_logs_do_not_change_adherence(
        self, db, client, anonymous_client
    ):
        client.post(
            "/api/diet/log", json={"slot": "lunch", "outcome": "eaten"}
        )
        other, _ = self.other_client(db, anonymous_client)
        report = other.get("/api/diet/adherence").json()
        assert report["eaten"] == 0
        assert report["adherence_rate"] is None
        other_progress = other.get("/api/diet/progress").json()
        assert other_progress["eaten"] == 0
        assert other_progress["weigh_ins"] == []

    def test_another_users_extra_is_404(self, db, client, anonymous_client):
        save_profile(client)
        created = client.post(
            "/api/diet/extras",
            json={"description": "chips", "calories_kcal": 150},
        ).json()
        other, _ = self.other_client(db, anonymous_client)
        assert other.delete(f"/api/diet/extras/{created['id']}").status_code == 404


class TestProgressAndSubstitutes:
    def test_the_week_has_every_day(self, client, recipes):
        save_profile(client, meals_per_day=3)
        created = client.post("/api/diet/plan")
        meals = created.json()["meals"]
        assert created.status_code == 200
        assert len(meals) == 21
        assert {meal["day_offset"] for meal in meals} == set(range(7))
        assert all("kcal" in meal for meal in meals)
        sample = next(meal for meal in meals if meal["title"] != "No matching recipe")
        assert sample["recipe"] is not None
        assert sample["recipe"]["steps"]
        assert sample["recipe"]["ingredients"]
        assert "amount" in sample["recipe"]["ingredients"][0]

    def test_skip_substitute_feeds_progress(self, client, recipes):
        save_profile(client, meals_per_day=2)
        client.post("/api/diet/plan")
        skipped = client.post(
            "/api/diet/log",
            json={
                "slot": "lunch",
                "outcome": "skipped",
                "substitute_text": "pizza",
                "calories_kcal": 650,
            },
        )
        assert skipped.status_code == 200
        assert skipped.json()["calories_source"] == "user"
        assert skipped.json()["substitute_text"] == "pizza"
        report = client.get("/api/diet/progress?days=7").json()
        assert report["replaced"] == 1
        assert report["calorie_target"] == 1857
        assert any(day["intake"] == 650 and day["replaced"] == 1 for day in report["days"])

    def test_skip_without_calories_is_422(self, client, monkeypatch):
        from app.services import llm_recipes as recipes_llm

        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "")
        response = client.post(
            "/api/diet/log",
            json={
                "slot": "lunch",
                "outcome": "skipped",
                "substitute_text": "pizza",
            },
        )
        assert response.status_code == 422

    def test_weigh_in_requires_a_profile(self, client):
        assert (
            client.post("/api/diet/weigh-ins", json={"weight_kg": 60}).status_code
            == 400
        )

    def test_weigh_in_round_trip(self, client):
        save_profile(client)
        saved = client.post("/api/diet/weigh-ins", json={"weight_kg": 60.5})
        assert saved.status_code == 200
        assert saved.json()["weight_kg"] == 60.5
        assert client.get("/api/diet/profile").json()["weight_kg"] == 60.5
        report = client.get("/api/diet/progress").json()
        assert report["latest_weight_kg"] == 60.5
        assert report["start_weight_kg"] == 60.5

    def test_a_future_weigh_in_is_422(self, client):
        save_profile(client)
        tomorrow = (clock.today() + timedelta(days=1)).isoformat()
        response = client.post(
            "/api/diet/weigh-ins",
            json={"weight_kg": 60, "logged_date": tomorrow},
        )
        assert response.status_code == 422

    def test_a_bad_progress_window_is_422(self, client):
        assert client.get("/api/diet/progress?days=0").status_code == 422

    def test_extra_intake_feeds_progress_and_deletes(self, client):
        save_profile(client)
        created = client.post(
            "/api/diet/extras",
            json={
                "description": "latte and croissant",
                "calories_kcal": 420,
                "protein_g": 12,
                "carbs_g": 48,
                "fat_g": 18,
            },
        )
        assert created.status_code == 200
        body = created.json()
        assert body["calories_source"] == "user"
        assert body["macros_source"] == "user"
        assert body["calories_kcal"] == 420
        listed = client.get("/api/diet/extras").json()
        assert len(listed) == 1
        report = client.get("/api/diet/progress?days=7").json()
        assert report["extras"] == 1
        assert any(day["intake"] == 420 and day["extras"] == 1 for day in report["days"])
        assert report["protein_g"] == 12
        deleted = client.delete(f"/api/diet/extras/{body['id']}")
        assert deleted.status_code == 200
        assert client.get("/api/diet/extras").json() == []
        assert client.get("/api/diet/progress").json()["extras"] == 0

    def test_eaten_log_copies_planned_macros(self, client, recipes):
        save_profile(client, meals_per_day=2)
        plan = client.post("/api/diet/plan").json()
        meal = next(row for row in plan["meals"] if row["slot"] == "lunch")
        logged = client.post(
            "/api/diet/log",
            json={
                "slot": "lunch",
                "outcome": "eaten",
                "logged_date": meal["date"],
                "recipe_id": meal["recipe_id"],
                "title": meal["title"],
            },
        )
        assert logged.status_code == 200
        body = logged.json()
        assert body["calories_source"] == "planned"
        if meal["recipe"] and meal["recipe"].get("protein_g") is not None:
            assert body["protein_g"] == meal["recipe"]["protein_g"]
            assert body["macros_source"] == "planned"

    def test_extra_without_calories_is_422_without_key(self, client, monkeypatch):
        from app.services import llm_recipes as recipes_llm

        monkeypatch.setattr(recipes_llm.settings, "openai_api_key", "")
        save_profile(client)
        response = client.post(
            "/api/diet/extras",
            json={"description": "street tacos"},
        )
        assert response.status_code == 422
