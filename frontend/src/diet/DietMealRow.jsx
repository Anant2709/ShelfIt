import React, { useState } from "react";
import { prettyLabel } from "../utils";

export default function DietMealRow({ meal, busy, isToday, onEaten, onSkipped }) {
  const [substitute, setSubstitute] = useState("");
  const [skipKcal, setSkipKcal] = useState("");
  const [skipOpen, setSkipOpen] = useState(false);
  const [recipeOpen, setRecipeOpen] = useState(true);
  const recipe = meal.recipe;

  return (
    <li className={isToday ? "diet-meal diet-meal-today" : "diet-meal"}>
      <strong>
        {prettyLabel(meal.slot)} — {meal.title}
        {meal.kcal != null ? ` · ${meal.kcal} kcal` : ""}
      </strong>
      <p className="hint">
        Use: {meal.uses.length ? meal.uses.join(", ") : "nothing on the shelf"}
        {meal.missing.length ? ` · Need: ${meal.missing.join(", ")}` : ""}
      </p>
      {recipe ? (
        <div className="diet-recipe">
          <button
            type="button"
            className="ghost-button diet-recipe-toggle"
            onClick={() => setRecipeOpen((open) => !open)}
          >
            {recipeOpen ? "Hide recipe" : "Show recipe"}
          </button>
          {recipeOpen && (
            <div className="diet-recipe-card">
              <p className="hint">
                {recipe.servings} servings · prep {recipe.prep_min} min · cook{" "}
                {recipe.cook_min} min
                {recipe.protein_g != null ||
                recipe.carbs_g != null ||
                recipe.fat_g != null
                  ? ` · P${recipe.protein_g ?? "—"} C${recipe.carbs_g ?? "—"} F${recipe.fat_g ?? "—"}`
                  : ""}
              </p>
              {recipe.ingredients?.length > 0 && (
                <>
                  <h4>Ingredients</h4>
                  <ul>
                    {recipe.ingredients.map((item) => (
                      <li key={`${item.name}-${item.amount}`}>
                        {item.amount} {item.name}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {recipe.steps?.length > 0 && (
                <>
                  <h4>Steps</h4>
                  <ol>
                    {recipe.steps.map((step, index) => (
                      <li key={`${index}-${step.slice(0, 24)}`}>{step}</li>
                    ))}
                  </ol>
                </>
              )}
            </div>
          )}
        </div>
      ) : (
        <p className="hint">No recipe details for this meal yet — regenerate the week.</p>
      )}
      {meal.log ? (
        <p className="hint">
          Logged: {meal.log.outcome}
          {meal.log.substitute_text ? ` — ate ${meal.log.substitute_text}` : ""}
          {meal.log.calories_kcal != null
            ? ` · ${meal.log.calories_kcal} kcal (${meal.log.calories_source})`
            : ""}
        </p>
      ) : (
        <>
          <div className="diet-actions">
            <button type="button" disabled={busy} onClick={() => onEaten(meal)}>
              Eaten
            </button>
            <button
              type="button"
              className="ghost-button"
              disabled={busy}
              onClick={() => setSkipOpen((open) => !open)}
            >
              Skipped
            </button>
          </div>
          {skipOpen && (
            <div className="diet-skip">
              <input
                type="text"
                placeholder="Ate something else?"
                value={substitute}
                onChange={(event) => setSubstitute(event.target.value)}
              />
              <input
                type="number"
                min="0"
                max="4000"
                placeholder="kcal if you know"
                value={skipKcal}
                onChange={(event) => setSkipKcal(event.target.value)}
              />
              <button
                type="button"
                className="ghost-button"
                disabled={busy}
                onClick={() =>
                  onSkipped(
                    meal,
                    substitute,
                    skipKcal === "" ? null : Number(skipKcal)
                  )
                }
              >
                Log skip
              </button>
            </div>
          )}
        </>
      )}
    </li>
  );
}
