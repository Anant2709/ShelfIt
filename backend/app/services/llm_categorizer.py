"""Category assignment by language model, constrained to a closed set.

The constraint is the whole design. Free-text categories from a model would come
back as "Dairy", "dairy products", and "milk & dairy" for the same shelf, and the
waste analytics that group by category would fragment silently -- the same class
of bug as two spellings of spinach getting different shelf lives, but harder to
notice because each individual answer looks reasonable.

So the model does not name a category, it *picks* one. Anything outside the set
is discarded rather than coerced to the nearest match, because a reply the
prompt did not permit is evidence the model was not answering the question
asked.

No caching here. The caller persists results in the learned store, which is
permanent, inspectable, and correctable.
"""

from __future__ import annotations

import json

from openai import OpenAI, OpenAIError

from app.core.config import settings

PROMPT = """You assign a grocery item to exactly one category.

Allowed categories, and what belongs in each:
  produce             fresh fruit and vegetables, fresh herbs
  dairy               milk, curd, yogurt, paneer, cheese, butter, ghee, eggs
  meat_seafood        any meat, poultry, fish, or shellfish
  bakery              bread, buns, pav, cakes, pastries
  grains_pulses       rice, atta, flour, dals, lentils, beans, pasta
  spices_condiments   spices, salt, masalas, sauces, pickles, vinegar
  snacks_sweets       biscuits, chips, namkeen, chocolate, mithai, desserts
  beverages           tea, coffee, juice, soft drinks, drink concentrates
  pantry              other shelf-stable cooking staples: oils, sugar, canned goods

Return JSON of one of these forms:
  {"category": "dairy"}
  {"category": null}

Use null only if the item is genuinely not a grocery, or you cannot tell which
single category applies. Do not invent a category name that is not listed above.

Eggs are grouped with dairy here, by shelf convention rather than biology.
Coconut milk is pantry, not dairy: it is a canned shelf-stable good.
"""


def _format_candidates(candidates: dict[str, str]) -> str:
    if not candidates:
        return "No similar items are known yet."
    lines = [f"- {name}: {category}" for name, category in candidates.items()]
    return "Categories already assigned to similar items:\n" + "\n".join(lines)


def _parse(content: str | None, allowed: frozenset[str]) -> str | None:
    """Validate the reply, discarding anything outside the closed set."""
    if not content:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    category = payload.get("category")
    if not isinstance(category, str):
        return None
    category = category.strip().lower()
    if category not in allowed:
        return None
    return category


def resolve_category(
    name: str,
    allowed: frozenset[str],
    candidates: dict[str, str] | None = None,
    client_factory=None,
) -> str | None:
    """Pick a category from `allowed`, or return None if none could be chosen."""
    if not settings.openai_api_key:
        return None

    candidates = candidates or {}
    factory = client_factory or (lambda: OpenAI(api_key=settings.openai_api_key))
    client = factory()

    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "system", "content": _format_candidates(candidates)},
                {"role": "user", "content": f"Categorise this item: {name}"},
            ],
            max_tokens=30,
        )
    except OpenAIError:
        return None

    return _parse(response.choices[0].message.content, allowed)
