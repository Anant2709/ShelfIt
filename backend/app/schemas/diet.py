from datetime import date, datetime

from pydantic import BaseModel, Field


class DietQuestionnaireOut(BaseModel):
    goals: list[str]
    eating_patterns: list[str]
    allergens: list[str]
    meals_per_day: list[int]
    slots: list[str]
    slots_for_meals_per_day: dict[str, list[str]]
    log_outcomes: list[str]
    default_calories: dict[str, int]
    plan_days: int
    plan_modes: list[str]
    sexes: list[str]
    activities: list[str]
    cooking_times: list[str]
    preferences: list[str]
    age_range: list[int]
    height_cm_range: list[float]
    weight_kg_range: list[float]
    calorie_disclaimer: str = (
        "Calorie targets are a Mifflin-St Jeor estimate, not medical advice."
    )


class DietProfileIn(BaseModel):
    goal: str
    eating_pattern: str
    allergens: list[str] = Field(default_factory=list)
    meals_per_day: int
    calorie_target: int | None = None
    sex: str
    age: int
    height_cm: float
    weight_kg: float
    target_weight_kg: float
    activity: str
    cooking_time: str = "about_30"
    preferences: list[str] = Field(default_factory=list)


class DietProfileOut(BaseModel):
    user_id: str
    goal: str
    eating_pattern: str
    allergens: list[str]
    meals_per_day: int
    calorie_target: int | None
    resolved_calorie_target: int
    tdee: int
    sex: str
    age: int
    height_cm: float
    weight_kg: float
    target_weight_kg: float
    activity: str
    cooking_time: str
    preferences: list[str]
    updated_at: datetime


class DietLogOut(BaseModel):
    id: str
    logged_date: date
    slot: str
    outcome: str
    recipe_id: str | None
    title: str | None
    substitute_text: str | None = None
    calories_kcal: int | None = None
    calories_source: str | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    macros_source: str | None = None

    class Config:
        from_attributes = True


class DietLogIn(BaseModel):
    slot: str
    outcome: str
    logged_date: date | None = None
    recipe_id: str | None = None
    title: str | None = None
    substitute_text: str | None = None
    calories_kcal: int | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None


class DietRecipeIngredientOut(BaseModel):
    name: str
    amount: str = "as needed"


class DietRecipeOut(BaseModel):
    servings: int = 2
    prep_min: int = 10
    cook_min: int = 20
    ingredients: list[DietRecipeIngredientOut] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    kcal: int | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None


class DietMealOut(BaseModel):
    id: str
    date: date
    day_offset: int
    slot: str
    recipe_id: str | None
    title: str
    uses: list[str]
    missing: list[str]
    kcal: int | None = None
    recipe: DietRecipeOut | None = None
    log: DietLogOut | None = None


class DietPlanOut(BaseModel):
    id: str
    created_at: datetime
    window_start: date
    window_days: int
    calorie_target: int
    goal: str
    eating_pattern: str
    meals_per_day: int
    mode: str
    shopping_list: list[str] = Field(default_factory=list)
    meals: list[DietMealOut]


class DietTodayOut(BaseModel):
    date: date
    calorie_target: int | None = None
    mode: str | None = None
    shopping_list: list[str] = Field(default_factory=list)
    meals: list[DietMealOut] = Field(default_factory=list)


class DietAdherenceOut(BaseModel):
    window_days: int
    planned: int
    eaten: int
    skipped: int
    unlogged: int
    logged_rate: float
    adherence_rate: float | None


class DietWeighInIn(BaseModel):
    weight_kg: float
    logged_date: date | None = None


class DietWeighInOut(BaseModel):
    id: str
    logged_date: date
    weight_kg: float

    class Config:
        from_attributes = True


class DietDayProgressOut(BaseModel):
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


class DietProgressOut(BaseModel):
    window_days: int
    calorie_target: int | None
    planned: int
    eaten: int
    skipped: int
    replaced: int
    unlogged: int
    extras: int = 0
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    start_weight_kg: float | None
    latest_weight_kg: float | None
    target_weight_kg: float | None
    weight_progress: float | None
    days: list[DietDayProgressOut]
    weigh_ins: list[DietWeighInOut]


class DietExtraIntakeIn(BaseModel):
    description: str
    logged_date: date | None = None
    calories_kcal: int | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None


class DietExtraIntakeOut(BaseModel):
    id: str
    logged_date: date
    description: str
    calories_kcal: int | None
    calories_source: str | None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    macros_source: str | None = None

    class Config:
        from_attributes = True
