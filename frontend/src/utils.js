export function prettyLabel(value) {
  return String(value || "").replaceAll("_", " ");
}

export function formatQuantity(item) {
  const unit = item.unit || "count";
  if (unit === "count") {
    return `x${item.quantity}`;
  }
  return `${item.quantity} ${unit}`;
}

export function mealsByDate(meals) {
  const groups = [];
  const seen = new Map();
  for (const meal of meals || []) {
    if (!seen.has(meal.date)) {
      const group = { date: meal.date, meals: [] };
      seen.set(meal.date, group);
      groups.push(group);
    }
    seen.get(meal.date).meals.push(meal);
  }
  return groups;
}
