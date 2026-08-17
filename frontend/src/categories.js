/** Closed-set categories from the backend, with labels and image paths. */

export const CATEGORY_ORDER = [
  "produce",
  "dairy",
  "meat_seafood",
  "bakery",
  "grains_pulses",
  "spices_condiments",
  "snacks_sweets",
  "beverages",
  "pantry",
  "unknown"
];

export const CATEGORY_META = {
  produce: {
    id: "produce",
    label: "Produce",
    image: "/categories/produce.svg"
  },
  dairy: {
    id: "dairy",
    label: "Dairy",
    image: "/categories/dairy.svg"
  },
  meat_seafood: {
    id: "meat_seafood",
    label: "Meat & seafood",
    image: "/categories/meat_seafood.svg"
  },
  bakery: {
    id: "bakery",
    label: "Bakery",
    image: "/categories/bakery.svg"
  },
  grains_pulses: {
    id: "grains_pulses",
    label: "Grains & pulses",
    image: "/categories/grains_pulses.svg"
  },
  spices_condiments: {
    id: "spices_condiments",
    label: "Spices & condiments",
    image: "/categories/spices_condiments.svg"
  },
  snacks_sweets: {
    id: "snacks_sweets",
    label: "Snacks & sweets",
    image: "/categories/snacks_sweets.svg"
  },
  beverages: {
    id: "beverages",
    label: "Beverages",
    image: "/categories/beverages.svg"
  },
  pantry: {
    id: "pantry",
    label: "Pantry",
    image: "/categories/pantry.svg"
  },
  unknown: {
    id: "unknown",
    label: "Uncategorised",
    image: "/categories/unknown.svg"
  }
};

const KEYWORDS = [
  [
    "produce",
    [
      "tomato",
      "onion",
      "spinach",
      "lettuce",
      "potato",
      "apple",
      "banana",
      "coriander",
      "herb",
      "carrot",
      "cucumber",
      "pepper",
      "garlic",
      "ginger",
      "lemon",
      "lime",
      "berry",
      "fruit",
      "veg"
    ]
  ],
  [
    "dairy",
    [
      "milk",
      "cheese",
      "yogurt",
      "yoghurt",
      "paneer",
      "butter",
      "ghee",
      "cream",
      "egg"
    ]
  ],
  [
    "meat_seafood",
    [
      "chicken",
      "beef",
      "pork",
      "lamb",
      "fish",
      "shrimp",
      "prawn",
      "salmon",
      "meat",
      "turkey"
    ]
  ],
  ["bakery", ["bread", "bun", "bagel", "tortilla", "naan", "roti"]],
  [
    "grains_pulses",
    ["rice", "dal", "lentil", "chickpea", "flour", "oat", "quinoa", "bean", "pulse"]
  ],
  [
    "spices_condiments",
    [
      "salt",
      "cumin",
      "turmeric",
      "spice",
      "sauce",
      "mustard",
      "pepper",
      "chili",
      "chilli",
      "masala"
    ]
  ],
  ["snacks_sweets", ["biscuit", "cookie", "chip", "chocolate", "candy", "snack"]],
  ["beverages", ["tea", "coffee", "juice", "soda", "water", "drink"]],
  ["pantry", ["oil", "sugar", "vinegar", "coconut", "stock", "broth", "pasta"]]
];

export function normalizeCategory(raw) {
  if (!raw || !CATEGORY_META[raw]) return "unknown";
  return raw;
}

export function guessCategory(name) {
  const text = String(name || "").toLowerCase();
  if (!text) return "unknown";
  for (const [category, words] of KEYWORDS) {
    if (words.some((word) => text.includes(word))) {
      return category;
    }
  }
  return "unknown";
}

export function categoryMeta(raw) {
  return CATEGORY_META[normalizeCategory(raw)];
}

/** Group inventory-like rows that already have a `category` field. */
export function groupItemsByCategory(items) {
  const buckets = new Map();
  for (const item of items || []) {
    const key = normalizeCategory(item.category);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(item);
  }
  return CATEGORY_ORDER.filter((id) => buckets.has(id)).map((id) => ({
    ...CATEGORY_META[id],
    items: buckets.get(id)
  }));
}

/** Group bare shopping-list names using keyword guesses. */
export function groupNamesByCategory(names) {
  const buckets = new Map();
  for (const name of names || []) {
    const key = guessCategory(name);
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(name);
  }
  return CATEGORY_ORDER.filter((id) => buckets.has(id)).map((id) => ({
    ...CATEGORY_META[id],
    items: buckets.get(id)
  }));
}
