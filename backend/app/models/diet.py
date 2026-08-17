import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.models.base import Base


class DietProfile(Base):
    """The current intake answers for one person.

    One row per user. Changing it does not rewrite an already-generated plan:
    the plan snapshots the answers it was built from, the same way a disposition
    snapshots the item so a later rename cannot rewrite last month's report.
    """

    __tablename__ = "diet_profiles"

    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", name="fk_diet_profiles_user_id"),
        primary_key=True,
    )
    goal: Mapped[str] = mapped_column(String, nullable=False)
    eating_pattern: Mapped[str] = mapped_column(String, nullable=False)
    # JSON list of closed-set allergen ids. A string column keeps SQLite and
    # Postgres interchangeable without a native JSON type.
    allergens: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    meals_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    # Null means "use the default for this goal". The default is applied when a
    # plan is generated, not stored here, so changing the goal later is visible.
    calorie_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sex: Mapped[str] = mapped_column(String, nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    height_cm: Mapped[float] = mapped_column(Float, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    target_weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    activity: Mapped[str] = mapped_column(String, nullable=False)
    cooking_time: Mapped[str] = mapped_column(String, nullable=False, default="about_30")
    preferences: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DietPlan(Base):
    """One generated week of meals.

    The latest plan for a user is the current one. Older rows stay so
    regenerating is an append, not a rewrite of what was suggested last week.
    """

    __tablename__ = "diet_plans"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", name="fk_diet_plans_user_id"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    calorie_target: Mapped[int] = mapped_column(Integer, nullable=False)
    goal: Mapped[str] = mapped_column(String, nullable=False)
    eating_pattern: Mapped[str] = mapped_column(String, nullable=False)
    meals_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    # "pantry" cooks from what is on the shelf. "ideal" ignores the shelf and
    # the missing ingredients become the shopping list.
    mode: Mapped[str] = mapped_column(String, nullable=False, default="pantry")

    meals: Mapped[list["DietPlanMeal"]] = relationship(
        "DietPlanMeal",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="DietPlanMeal.day_offset",
    )


class DietPlanMeal(Base):
    """One suggested meal in a plan.

    `recipe_id` is set when the meal came from the curated fallback file.
    LLM meals store a title and an ingredient list instead. Uses and missing
    are recomputed on read against the current fridge.
    """

    __tablename__ = "diet_plan_meals"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    plan_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("diet_plans.id", name="fk_diet_plan_meals_plan_id"),
        nullable=False,
        index=True,
    )
    day_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    slot: Mapped[str] = mapped_column(String, nullable=False)
    recipe_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    uses_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    missing_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    ingredients_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # Estimated kcal for this suggested meal. Fallback recipes carry a number in
    # the curated file; LLM meals send one in the JSON. Not a lab measurement.
    kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Full recipe card JSON: servings, times, detailed ingredients, steps, macros.
    # ingredients_json stays the flat name list used for pantry matching.
    recipe_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    plan: Mapped[DietPlan] = relationship("DietPlan", back_populates="meals")


class DietLog(Base):
    """Whether a planned slot was eaten or skipped.

    Independent of plan rows, so regenerating a week cannot erase what was
    already logged. One row per user, date, and slot: a second log for the same
    slot is a correction, not a second meal.
    """

    __tablename__ = "diet_logs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "logged_date",
            "slot",
            name="uq_diet_logs_user_date_slot",
        ),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", name="fk_diet_logs_user_id"),
        nullable=False,
        index=True,
    )
    logged_date: Mapped[date] = mapped_column(Date, nullable=False)
    slot: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    recipe_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    substitute_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    calories_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # planned, user, llm, or none. Distinguishes an estimate from a number the
    # person typed, the same way shelf-life source does.
    calories_source: Mapped[str | None] = mapped_column(String, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    macros_source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DietWeighIn(Base):
    """One recorded weight, for progress toward the target.

    The profile holds the current weight used for calorie math. This table is
    the history, so a later edit cannot rewrite last week's chart. One row per
    user per day: a second weigh-in that day is a correction.
    """

    __tablename__ = "diet_weigh_ins"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "logged_date",
            name="uq_diet_weigh_ins_user_date",
        ),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", name="fk_diet_weigh_ins_user_id"),
        nullable=False,
        index=True,
    )
    logged_date: Mapped[date] = mapped_column(Date, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DietExtraIntake(Base):
    """Food beyond a planned slot — snacks, restaurant meals, seconds.

    Not a DietLog row: plan logs stay unique on (user, date, slot). Many extras
    can land on the same day. Calories and macros carry a source label the same
    way shelf-life and skip-substitutes do.
    """

    __tablename__ = "diet_extra_intakes"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", name="fk_diet_extra_intakes_user_id"),
        nullable=False,
        index=True,
    )
    logged_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    calories_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calories_source: Mapped[str | None] = mapped_column(String, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    macros_source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
