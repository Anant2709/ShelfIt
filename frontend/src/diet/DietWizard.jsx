import React, { useMemo, useState } from "react";
import { prettyLabel } from "../utils";

const STEPS = [
  "goal",
  "pattern",
  "allergens",
  "sex_age",
  "height_weight",
  "target_weight",
  "activity",
  "meals_prefs",
  "calories",
  "review"
];

const INITIAL = {
  goal: "maintain",
  eating_pattern: "omnivore",
  allergens: [],
  sex: "female",
  age: "28",
  height_cm: "165",
  weight_kg: "62",
  target_weight_kg: "58",
  activity: "light",
  cooking_time: "about_30",
  meals_per_day: 3,
  preferences: [],
  calorie_target: ""
};

export default function DietWizard({
  questions,
  busy,
  onSave,
  initial = null,
  onCancel = null
}) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState(() =>
    initial
      ? {
          goal: initial.goal || INITIAL.goal,
          eating_pattern: initial.eating_pattern || INITIAL.eating_pattern,
          allergens: initial.allergens || [],
          sex: initial.sex || INITIAL.sex,
          age: String(initial.age ?? INITIAL.age),
          height_cm: String(initial.height_cm ?? INITIAL.height_cm),
          weight_kg: String(initial.weight_kg ?? INITIAL.weight_kg),
          target_weight_kg: String(
            initial.target_weight_kg ?? INITIAL.target_weight_kg
          ),
          activity: initial.activity || INITIAL.activity,
          cooking_time: initial.cooking_time || INITIAL.cooking_time,
          meals_per_day: initial.meals_per_day || INITIAL.meals_per_day,
          preferences: initial.preferences || [],
          calorie_target:
            initial.calorie_target != null ? String(initial.calorie_target) : ""
        }
      : INITIAL
  );
  const total = STEPS.length;
  const key = STEPS[step];

  const setField = (name, value) =>
    setForm((current) => ({ ...current, [name]: value }));

  const toggleList = (name, value) => {
    setForm((current) => {
      const list = current[name];
      return {
        ...current,
        [name]: list.includes(value)
          ? list.filter((item) => item !== value)
          : [...list, value]
      };
    });
  };

  const canContinue = useMemo(() => {
    if (key === "sex_age") {
      return form.sex && Number(form.age) >= 16 && Number(form.age) <= 90;
    }
    if (key === "height_weight") {
      return Number(form.height_cm) > 0 && Number(form.weight_kg) > 0;
    }
    if (key === "target_weight") {
      return Number(form.target_weight_kg) > 0;
    }
    return true;
  }, [form, key]);

  const handleFinish = async () => {
    await onSave({
      goal: form.goal,
      eating_pattern: form.eating_pattern,
      allergens: form.allergens,
      meals_per_day: Number(form.meals_per_day),
      calorie_target: form.calorie_target ? Number(form.calorie_target) : null,
      sex: form.sex,
      age: Number(form.age),
      height_cm: Number(form.height_cm),
      weight_kg: Number(form.weight_kg),
      target_weight_kg: Number(form.target_weight_kg),
      activity: form.activity,
      cooking_time: form.cooking_time,
      preferences: form.preferences
    });
  };

  return (
    <div className="card diet-wizard">
      <div className="wizard-progress" aria-hidden="true">
        {STEPS.map((_, index) => (
          <span
            key={index}
            className={
              index <= step ? "wizard-dot wizard-dot-on" : "wizard-dot"
            }
          />
        ))}
      </div>
      <p className="hint">
        Step {step + 1} of {total}
      </p>

      {key === "goal" && (
        <WizardChoice
          title="What is your goal?"
          options={questions.goals}
          value={form.goal}
          onChange={(value) => setField("goal", value)}
        />
      )}

      {key === "pattern" && (
        <WizardChoice
          title="How do you eat?"
          options={questions.eating_patterns}
          value={form.eating_pattern}
          onChange={(value) => setField("eating_pattern", value)}
        />
      )}

      {key === "allergens" && (
        <WizardChips
          title="Any allergens to exclude?"
          options={questions.allergens}
          selected={form.allergens}
          onToggle={(value) => toggleList("allergens", value)}
          hint="Skip if none."
        />
      )}

      {key === "sex_age" && (
        <div className="wizard-step">
          <h2>About you</h2>
          <label>
            Sex
            <select
              value={form.sex}
              onChange={(event) => setField("sex", event.target.value)}
            >
              {questions.sexes.map((sex) => (
                <option key={sex} value={sex}>
                  {prettyLabel(sex)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Age
            <input
              type="number"
              min={questions.age_range?.[0] || 16}
              max={questions.age_range?.[1] || 90}
              value={form.age}
              onChange={(event) => setField("age", event.target.value)}
            />
          </label>
        </div>
      )}

      {key === "height_weight" && (
        <div className="wizard-step">
          <h2>Height and weight</h2>
          <label>
            Height (cm)
            <input
              type="number"
              min={questions.height_cm_range?.[0] || 120}
              max={questions.height_cm_range?.[1] || 220}
              step="0.1"
              value={form.height_cm}
              onChange={(event) => setField("height_cm", event.target.value)}
            />
          </label>
          <label>
            Current weight (kg)
            <input
              type="number"
              min={questions.weight_kg_range?.[0] || 35}
              max={questions.weight_kg_range?.[1] || 250}
              step="0.1"
              value={form.weight_kg}
              onChange={(event) => setField("weight_kg", event.target.value)}
            />
          </label>
        </div>
      )}

      {key === "target_weight" && (
        <div className="wizard-step">
          <h2>Target weight</h2>
          <label>
            Target weight (kg)
            <input
              type="number"
              min={questions.weight_kg_range?.[0] || 35}
              max={questions.weight_kg_range?.[1] || 250}
              step="0.1"
              value={form.target_weight_kg}
              onChange={(event) =>
                setField("target_weight_kg", event.target.value)
              }
            />
          </label>
        </div>
      )}

      {key === "activity" && (
        <div className="wizard-step">
          <h2>Lifestyle</h2>
          <label>
            Activity
            <select
              value={form.activity}
              onChange={(event) => setField("activity", event.target.value)}
            >
              {questions.activities.map((activity) => (
                <option key={activity} value={activity}>
                  {prettyLabel(activity)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Cooking time
            <select
              value={form.cooking_time}
              onChange={(event) => setField("cooking_time", event.target.value)}
            >
              {questions.cooking_times.map((time) => (
                <option key={time} value={time}>
                  {prettyLabel(time)}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {key === "meals_prefs" && (
        <div className="wizard-step">
          <h2>Meals and preferences</h2>
          <label>
            Meals per day
            <select
              value={form.meals_per_day}
              onChange={(event) =>
                setField("meals_per_day", Number(event.target.value))
              }
            >
              {questions.meals_per_day.map((count) => (
                <option key={count} value={count}>
                  {count}
                </option>
              ))}
            </select>
          </label>
          <WizardChips
            title="Preferences"
            options={questions.preferences}
            selected={form.preferences}
            onToggle={(value) => toggleList("preferences", value)}
            hint="Optional."
          />
        </div>
      )}

      {key === "calories" && (
        <div className="wizard-step">
          <h2>Daily calories (optional)</h2>
          <p className="hint">
            Leave blank to use a Mifflin-St Jeor estimate from your body stats.
            {questions.calorie_disclaimer
              ? ` ${questions.calorie_disclaimer}`
              : ""}
          </p>
          <label>
            Override kcal
            <input
              type="number"
              min="1200"
              max="4000"
              placeholder="Estimated from body stats"
              value={form.calorie_target}
              onChange={(event) => setField("calorie_target", event.target.value)}
            />
          </label>
        </div>
      )}

      {key === "review" && (
        <div className="wizard-step">
          <h2>Review</h2>
          <ul className="review-list">
            <li>Goal: {prettyLabel(form.goal)}</li>
            <li>Pattern: {prettyLabel(form.eating_pattern)}</li>
            <li>
              Allergens:{" "}
              {form.allergens.length
                ? form.allergens.map(prettyLabel).join(", ")
                : "none"}
            </li>
            <li>
              {prettyLabel(form.sex)}, age {form.age}
            </li>
            <li>
              {form.height_cm} cm · {form.weight_kg} kg → {form.target_weight_kg}{" "}
              kg
            </li>
            <li>
              {prettyLabel(form.activity)} · {prettyLabel(form.cooking_time)} ·{" "}
              {form.meals_per_day} meals
            </li>
            <li>
              Preferences:{" "}
              {form.preferences.length
                ? form.preferences.map(prettyLabel).join(", ")
                : "none"}
            </li>
            <li>
              Calories:{" "}
              {form.calorie_target
                ? `${form.calorie_target} override`
                : "estimate from body stats"}
            </li>
          </ul>
        </div>
      )}

      <div className="diet-actions">
        {step > 0 && (
          <button
            type="button"
            className="ghost-button"
            disabled={busy}
            onClick={() => setStep((value) => value - 1)}
          >
            Back
          </button>
        )}
        {step === 0 && initial && onCancel ? (
          <button
            type="button"
            className="ghost-button"
            disabled={busy}
            onClick={onCancel}
          >
            Cancel
          </button>
        ) : null}
        {step < total - 1 ? (
          <button
            type="button"
            disabled={busy || !canContinue}
            onClick={() => setStep((value) => value + 1)}
          >
            Continue
          </button>
        ) : (
          <button type="button" disabled={busy} onClick={handleFinish}>
            Save profile
          </button>
        )}
      </div>
    </div>
  );
}

function WizardChoice({ title, options, value, onChange }) {
  return (
    <div className="wizard-step">
      <h2>{title}</h2>
      <div className="chip-grid">
        {options.map((option) => (
          <button
            key={option}
            type="button"
            className={value === option ? "chip chip-on" : "chip"}
            onClick={() => onChange(option)}
          >
            {prettyLabel(option)}
          </button>
        ))}
      </div>
    </div>
  );
}

function WizardChips({ title, options, selected, onToggle, hint }) {
  return (
    <div className="wizard-step">
      <h2>{title}</h2>
      {hint ? <p className="hint">{hint}</p> : null}
      <div className="chip-grid">
        {options.map((option) => (
          <button
            key={option}
            type="button"
            className={selected.includes(option) ? "chip chip-on" : "chip"}
            onClick={() => onToggle(option)}
          >
            {prettyLabel(option)}
          </button>
        ))}
      </div>
    </div>
  );
}
