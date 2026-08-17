from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import clock
from app.db.deps import get_db
from app.models.user import User
from app.schemas.diet import (
    DietAdherenceOut,
    DietDayProgressOut,
    DietExtraIntakeIn,
    DietExtraIntakeOut,
    DietLogIn,
    DietLogOut,
    DietMealOut,
    DietPlanOut,
    DietProfileIn,
    DietProfileOut,
    DietProgressOut,
    DietQuestionnaireOut,
    DietRecipeOut,
    DietTodayOut,
    DietWeighInIn,
    DietWeighInOut,
)
from app.services.diet import (
    DietError,
    adherence,
    create_extra_intake,
    current_plan,
    decorate_meal,
    delete_extra_intake,
    generate_plan,
    get_log,
    get_profile,
    list_extra_intakes,
    meal_date,
    open_inventory,
    parse_allergens,
    parse_preferences,
    progress,
    recipe_card_for_meal,
    resolved_calories,
    sorted_meals,
    tdee_kcal,
    unique_names,
    upsert_log,
    upsert_profile,
    upsert_weigh_in,
)
from app.services.recipes import questionnaire

router = APIRouter()


def _raise(exc: DietError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _profile_out(profile) -> DietProfileOut:
    return DietProfileOut(
        user_id=profile.user_id,
        goal=profile.goal,
        eating_pattern=profile.eating_pattern,
        allergens=parse_allergens(profile.allergens),
        meals_per_day=profile.meals_per_day,
        calorie_target=profile.calorie_target,
        resolved_calorie_target=resolved_calories(profile),
        tdee=tdee_kcal(profile),
        sex=profile.sex,
        age=profile.age,
        height_cm=profile.height_cm,
        weight_kg=profile.weight_kg,
        target_weight_kg=profile.target_weight_kg,
        activity=profile.activity,
        cooking_time=profile.cooking_time,
        preferences=parse_preferences(profile.preferences),
        updated_at=profile.updated_at,
    )


def _log_out(row) -> DietLogOut | None:
    if row is None:
        return None
    return DietLogOut.model_validate(row)


def _meal_out(plan, meal, items, today, log=None) -> DietMealOut:
    uses, missing = decorate_meal(plan, meal, items, today)
    card = recipe_card_for_meal(meal)
    recipe = DietRecipeOut(**card) if card else None
    return DietMealOut(
        id=meal.id,
        date=meal_date(plan, meal),
        day_offset=meal.day_offset,
        slot=meal.slot,
        recipe_id=meal.recipe_id,
        title=meal.title,
        uses=uses,
        missing=missing,
        kcal=meal.kcal,
        recipe=recipe,
        log=_log_out(log),
    )


def _plan_out(db, user, plan) -> DietPlanOut:
    today = clock.today(user.timezone)
    items = open_inventory(db, user.id)
    meals = [
        _meal_out(
            plan,
            meal,
            items,
            today,
            get_log(db, user.id, meal_date(plan, meal), meal.slot),
        )
        for meal in sorted_meals(plan)
    ]
    return DietPlanOut(
        id=plan.id,
        created_at=plan.created_at,
        window_start=plan.window_start,
        window_days=plan.window_days,
        calorie_target=plan.calorie_target,
        goal=plan.goal,
        eating_pattern=plan.eating_pattern,
        meals_per_day=plan.meals_per_day,
        mode=plan.mode,
        shopping_list=unique_names([meal.missing for meal in meals]),
        meals=meals,
    )


def _weigh_in_out(row) -> DietWeighInOut:
    return DietWeighInOut.model_validate(row)


def _progress_out(report) -> DietProgressOut:
    return DietProgressOut(
        window_days=report.window_days,
        calorie_target=report.calorie_target,
        planned=report.planned,
        eaten=report.eaten,
        skipped=report.skipped,
        replaced=report.replaced,
        unlogged=report.unlogged,
        extras=report.extras,
        protein_g=report.protein_g,
        carbs_g=report.carbs_g,
        fat_g=report.fat_g,
        start_weight_kg=report.start_weight_kg,
        latest_weight_kg=report.latest_weight_kg,
        target_weight_kg=report.target_weight_kg,
        weight_progress=report.weight_progress,
        days=[DietDayProgressOut(**asdict(day)) for day in report.days],
        weigh_ins=[_weigh_in_out(row) for row in report.weigh_ins],
    )


def _extra_out(row) -> DietExtraIntakeOut:
    return DietExtraIntakeOut.model_validate(row)


@router.get("/questionnaire", response_model=DietQuestionnaireOut)
def get_questionnaire(user: User = Depends(get_current_user)):
    """Closed options for the intake form. Auth-gated with the rest of /diet."""
    return DietQuestionnaireOut(**questionnaire())


@router.get("/profile", response_model=DietProfileOut)
def read_profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    profile = get_profile(db, user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No diet profile")
    return _profile_out(profile)


@router.put("/profile", response_model=DietProfileOut)
def write_profile(
    payload: DietProfileIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        profile = upsert_profile(
            db,
            user,
            goal=payload.goal,
            eating_pattern=payload.eating_pattern,
            allergens=payload.allergens,
            meals_per_day=payload.meals_per_day,
            calorie_target=payload.calorie_target,
            sex=payload.sex,
            age=payload.age,
            height_cm=payload.height_cm,
            weight_kg=payload.weight_kg,
            target_weight_kg=payload.target_weight_kg,
            activity=payload.activity,
            cooking_time=payload.cooking_time,
            preferences=payload.preferences,
        )
    except DietError as exc:
        _raise(exc)
    return _profile_out(profile)


@router.post("/plan", response_model=DietPlanOut)
def create_plan(
    mode: str = "pantry",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        plan = generate_plan(db, user, mode=mode)
    except DietError as exc:
        _raise(exc)
    return _plan_out(db, user, plan)


@router.get("/plan", response_model=DietPlanOut)
def read_plan(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    plan = current_plan(db, user.id)
    if plan is None:
        raise HTTPException(status_code=404, detail="No diet plan")
    return _plan_out(db, user, plan)


@router.get("/today", response_model=DietTodayOut)
def read_today(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today = clock.today(user.timezone)
    plan = current_plan(db, user.id)
    if plan is None:
        return DietTodayOut(date=today, meals=[])
    items = open_inventory(db, user.id)
    meals = [
        _meal_out(
            plan,
            meal,
            items,
            today,
            get_log(db, user.id, today, meal.slot),
        )
        for meal in sorted_meals(plan)
        if meal_date(plan, meal) == today
    ]
    week = [
        _meal_out(plan, meal, items, today)
        for meal in sorted_meals(plan)
    ]
    return DietTodayOut(
        date=today,
        calorie_target=plan.calorie_target,
        mode=plan.mode,
        shopping_list=unique_names([meal.missing for meal in week]),
        meals=meals,
    )


@router.post("/log", response_model=DietLogOut)
def write_log(
    payload: DietLogIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        row = upsert_log(
            db,
            user,
            logged_date=payload.logged_date,
            slot=payload.slot,
            outcome=payload.outcome,
            recipe_id=payload.recipe_id,
            title=payload.title,
            substitute_text=payload.substitute_text,
            calories_kcal=payload.calories_kcal,
            protein_g=payload.protein_g,
            carbs_g=payload.carbs_g,
            fat_g=payload.fat_g,
        )
    except DietError as exc:
        _raise(exc)
    return DietLogOut.model_validate(row)


@router.get("/adherence", response_model=DietAdherenceOut)
def read_adherence(
    days: int = 7,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        report = adherence(db, user, window_days=days)
    except DietError as exc:
        _raise(exc)
    return DietAdherenceOut(
        window_days=report.window_days,
        planned=report.planned,
        eaten=report.eaten,
        skipped=report.skipped,
        unlogged=report.unlogged,
        logged_rate=report.logged_rate,
        adherence_rate=report.adherence_rate,
    )


@router.post("/weigh-ins", response_model=DietWeighInOut)
def write_weigh_in(
    payload: DietWeighInIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if get_profile(db, user.id) is None:
        raise HTTPException(status_code=400, detail="Save a diet profile first")
    try:
        row = upsert_weigh_in(
            db,
            user,
            weight_kg=payload.weight_kg,
            logged_date=payload.logged_date,
        )
    except DietError as exc:
        _raise(exc)
    return _weigh_in_out(row)


@router.get("/progress", response_model=DietProgressOut)
def read_progress(
    days: int = 7,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        report = progress(db, user, window_days=days)
    except DietError as exc:
        _raise(exc)
    return _progress_out(report)


@router.get("/extras", response_model=list[DietExtraIntakeOut])
def read_extras(
    logged_date: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return [
        _extra_out(row)
        for row in list_extra_intakes(db, user.id, logged_date=logged_date)
    ]


@router.post("/extras", response_model=DietExtraIntakeOut)
def write_extra(
    payload: DietExtraIntakeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        row = create_extra_intake(
            db,
            user,
            description=payload.description,
            logged_date=payload.logged_date,
            calories_kcal=payload.calories_kcal,
            protein_g=payload.protein_g,
            carbs_g=payload.carbs_g,
            fat_g=payload.fat_g,
        )
    except DietError as exc:
        _raise(exc)
    return _extra_out(row)


@router.delete("/extras/{extra_id}", response_model=dict)
def remove_extra(
    extra_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not delete_extra_intake(db, user.id, extra_id):
        raise HTTPException(status_code=404, detail="Extra intake not found")
    return {"ok": True}
