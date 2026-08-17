import React, { useMemo, useState } from "react";
import { groupNamesByCategory } from "../categories";
import CategoryAccordion from "../components/CategoryAccordion";
import { mealsByDate } from "../utils";
import CalorieGauge from "./CalorieGauge";
import DietCharts from "./DietCharts";
import DietMealRow from "./DietMealRow";

export default function DietDashboard({
  profile,
  plan,
  today,
  progress,
  adherence,
  extras = [],
  busy,
  onGenerate,
  onLogMeal,
  onWeighIn,
  onLogExtra,
  onDeleteExtra,
  onEditProfile
}) {
  const [weighKg, setWeighKg] = useState(
    profile?.weight_kg != null ? String(profile.weight_kg) : ""
  );
  const [extraDescription, setExtraDescription] = useState("");
  const [extraKcal, setExtraKcal] = useState("");
  const [extraProtein, setExtraProtein] = useState("");
  const [extraCarbs, setExtraCarbs] = useState("");
  const [extraFat, setExtraFat] = useState("");

  const todayKey = today?.date;
  const todayProgress = progress?.days?.find((day) => day.date === todayKey);
  const intake = todayProgress?.intake ?? 0;
  const target =
    profile?.resolved_calorie_target ??
    plan?.calorie_target ??
    progress?.calorie_target ??
    2000;

  const shoppingGroups = useMemo(
    () => groupNamesByCategory(plan?.shopping_list || []),
    [plan?.shopping_list]
  );

  const todayExtras = useMemo(
    () => extras.filter((row) => row.logged_date === todayKey),
    [extras, todayKey]
  );

  return (
    <div className="diet-dashboard">
      <section className="card gauge-card">
        <CalorieGauge intake={intake} target={target} />
        <p className="hint">
          Estimated {profile.resolved_calorie_target} kcal/day from a TDEE of{" "}
          {profile.tdee}. Calorie targets are a Mifflin-St Jeor estimate, not
          medical advice.
        </p>
        {progress && (
          <p className="hint">
            Last {progress.window_days} days: {progress.eaten} eaten,{" "}
            {progress.skipped} skipped, {progress.replaced} replaced,{" "}
            {progress.unlogged} unlogged of {progress.planned} planned
            {progress.extras ? `, ${progress.extras} extras` : ""}
            {adherence?.adherence_rate != null
              ? ` — ${Math.round(adherence.adherence_rate * 100)}% of logged meals eaten`
              : ""}
            .
          </p>
        )}
        {progress?.weight_progress != null && (
          <p className="hint">
            Weight {progress.start_weight_kg} → {progress.latest_weight_kg} kg
            toward {progress.target_weight_kg} kg (
            {Math.round(progress.weight_progress * 100)}% of the way).
          </p>
        )}
      </section>

      <section className="card">
        <h2>Log something else</h2>
        <p className="hint">
          Snacks, restaurant food, or seconds — separate from planned meal
          slots. Leave calories blank to estimate when a key is configured.
        </p>
        <form
          className="diet-extra-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!extraDescription.trim() || !onLogExtra) return;
            onLogExtra({
              description: extraDescription.trim(),
              logged_date: todayKey || undefined,
              calories_kcal: extraKcal === "" ? null : Number(extraKcal),
              protein_g: extraProtein === "" ? null : Number(extraProtein),
              carbs_g: extraCarbs === "" ? null : Number(extraCarbs),
              fat_g: extraFat === "" ? null : Number(extraFat)
            });
            setExtraDescription("");
            setExtraKcal("");
            setExtraProtein("");
            setExtraCarbs("");
            setExtraFat("");
          }}
        >
          <label>
            What did you eat?
            <input
              type="text"
              value={extraDescription}
              onChange={(event) => setExtraDescription(event.target.value)}
              placeholder="e.g. latte and croissant"
              required
            />
          </label>
          <div className="diet-extra-macros">
            <label>
              kcal
              <input
                type="number"
                min="0"
                max="4000"
                value={extraKcal}
                onChange={(event) => setExtraKcal(event.target.value)}
                placeholder=""
                aria-label="Calories optional"
              />
            </label>
            <label>
              protein g
              <input
                type="number"
                min="0"
                max="500"
                step="0.1"
                value={extraProtein}
                onChange={(event) => setExtraProtein(event.target.value)}
                placeholder=""
                aria-label="Protein grams optional"
              />
            </label>
            <label>
              carbs g
              <input
                type="number"
                min="0"
                max="500"
                step="0.1"
                value={extraCarbs}
                onChange={(event) => setExtraCarbs(event.target.value)}
                placeholder=""
                aria-label="Carbs grams optional"
              />
            </label>
            <label>
              fat g
              <input
                type="number"
                min="0"
                max="500"
                step="0.1"
                value={extraFat}
                onChange={(event) => setExtraFat(event.target.value)}
                placeholder=""
                aria-label="Fat grams optional"
              />
            </label>
          </div>
          <button type="submit" disabled={busy}>
            Log extra
          </button>
        </form>
        {todayExtras.length > 0 && (
          <ul className="diet-extras-list">
            {todayExtras.map((row) => (
              <li key={row.id}>
                <div>
                  <strong>{row.description}</strong>
                  <span className="hint">
                    {" "}
                    · {row.calories_kcal ?? "—"} kcal ({row.calories_source})
                    {row.protein_g != null ||
                    row.carbs_g != null ||
                    row.fat_g != null
                      ? ` · P${row.protein_g ?? "—"} C${row.carbs_g ?? "—"} F${row.fat_g ?? "—"}`
                      : ""}
                  </span>
                </div>
                <button
                  type="button"
                  className="link-button"
                  disabled={busy}
                  onClick={() => onDeleteExtra?.(row.id)}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <h2>Weigh-in</h2>
        <form
          className="diet-weigh"
          onSubmit={(event) => {
            event.preventDefault();
            if (!weighKg) return;
            onWeighIn(Number(weighKg));
          }}
        >
          <label>
            Today&apos;s weight (kg)
            <input
              type="number"
              min="35"
              max="250"
              step="0.1"
              value={weighKg}
              onChange={(event) => setWeighKg(event.target.value)}
              required
            />
          </label>
          <button type="submit" disabled={busy}>
            Log weigh-in
          </button>
        </form>
      </section>

      <section className="card">
        <h2>This week&apos;s plan</h2>
        <div className="diet-actions">
          <button
            type="button"
            disabled={busy}
            onClick={() => onGenerate("pantry")}
          >
            Plan from my pantry
          </button>
          <button
            type="button"
            className="ghost-button"
            disabled={busy}
            onClick={() => onGenerate("ideal")}
          >
            Ideal week + shopping list
          </button>
          <button
            type="button"
            className="link-button"
            disabled={busy}
            onClick={onEditProfile}
          >
            Edit profile
          </button>
        </div>
        {plan && (
          <p className="hint">
            Current week:{" "}
            {plan.mode === "ideal"
              ? "ideal diet, then checked against the fridge"
              : "cooked from what is on the shelf"}
            . {plan.calorie_target} kcal/day.
          </p>
        )}
        {plan?.shopping_list?.length > 0 && (
          <div className="diet-shopping">
            <h3>Buy this week</h3>
            <p className="hint">Grouped by category — tap to expand.</p>
            <CategoryAccordion
              groups={shoppingGroups}
              initiallyOpen={shoppingGroups[0]?.id || null}
              renderItem={(name) => <strong>{name}</strong>}
            />
          </div>
        )}
      </section>

      {plan?.meals?.length > 0 ? (
        <div className="diet-week">
          {mealsByDate(plan.meals).map((group) => (
            <section key={group.date} className="card diet-day">
              <h3>
                {group.date}
                {group.date === todayKey ? " · today" : ""}
              </h3>
              <ul className="diet-meals">
                {group.meals.map((meal) => (
                  <DietMealRow
                    key={meal.id}
                    meal={meal}
                    busy={busy}
                    isToday={group.date === todayKey}
                    onEaten={(item) => onLogMeal(item, "eaten")}
                    onSkipped={(item, text, kcal) =>
                      onLogMeal(item, "skipped", {
                        substitute_text: text || null,
                        calories_kcal: kcal
                      })
                    }
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      ) : (
        <p className="hint">Generate a pantry or ideal week to see meals.</p>
      )}

      {progress?.days?.length > 0 && <DietCharts progress={progress} />}

      {progress?.days?.length > 0 && (
        <section className="card">
          <h3>Daily intake</h3>
          <table className="diet-table">
            <thead>
              <tr>
                <th>Day</th>
                <th>Intake</th>
                <th>Extras</th>
                <th>Target</th>
              </tr>
            </thead>
            <tbody>
              {progress.days.map((day) => (
                <tr
                  key={day.date}
                  className={day.date === todayKey ? "diet-meal-today" : undefined}
                >
                  <td>{day.date}</td>
                  <td>{day.intake}</td>
                  <td>{day.extras ?? 0}</td>
                  <td>{day.target ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
