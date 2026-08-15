"""Shelf-life estimation by language model.

This replaces an earlier tier that called Spoonacular. That call could only
confirm a string was a recognisable food -- Spoonacular does not publish
shelf-life data -- after which the code returned a hardcoded 5 days and labelled
the result `source="api"`. So sugar and cooking oil were both reported as keeping
for five days, with provenance that implied a data provider had said so.

Asking a model that can actually answer the question, and labelling the result
`llm`, is both more accurate and honest about where the number came from.

Every estimate is cached, including "I don't know". That caps the cost at one
call per distinct item name and makes the answer stable: the same item must not
keep for 5 days today and 7 days tomorrow.
"""

from __future__ import annotations

import json

from openai import OpenAI, OpenAIError

from app.core.config import settings
from app.services.cache import MISS, Cache, get_cache

CACHE_NAMESPACE = "shelf_life_llm_v1"

# Bounds for a plausible answer. One day is the shortest useful granularity, and
# ten years exceeds any grocery item, so anything outside is a model error.
MIN_DAYS = 1
MAX_DAYS = 3650

PROMPT = """You estimate how long a grocery item stays good.

Return JSON of the form {"days": 7} or {"days": null} if you genuinely cannot say.

Assume typical domestic storage: perishables refrigerated, shelf-stable goods in a
pantry, packaging unopened, bought fresh today. Give the conservative end of the
normal range, counted from today. "days" must be a whole number of days.
"""


class _Unavailable:
    """The call could not be made, as distinct from the model not knowing.

    The difference matters because answers are cached for weeks. "The model says
    it cannot tell" is a real answer worth remembering; "the network was down for
    a second" is not, and caching it would poison the entry long after the outage
    ended.
    """

    __slots__ = ()


UNAVAILABLE = _Unavailable()


def _ask_model(name: str, client_factory) -> int | None | _Unavailable:
    client = client_factory()
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": f"How long does this keep: {name}"},
            ],
            max_tokens=60,
        )
    except OpenAIError:
        return UNAVAILABLE

    return _parse(response.choices[0].message.content)


def _parse(content: str | None) -> int | None:
    """Extract a plausible day count, rejecting anything unusable."""
    if not content:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    raw = payload.get("days")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return None
    if days < MIN_DAYS or days > MAX_DAYS:
        return None
    return days


def estimate_shelf_life_days(
    name: str,
    cache: Cache | None = None,
    client_factory=None,
) -> int | None:
    """Days the item keeps, or None if no estimate is available."""
    if not settings.openai_api_key:
        return None

    active_cache = cache if cache is not None else get_cache()
    cache_key = f"{settings.openai_model}:{name}"

    cached = active_cache.get(CACHE_NAMESPACE, cache_key)
    if cached is not MISS:
        return cached.get("days") if isinstance(cached, dict) else None

    factory = client_factory or (lambda: OpenAI(api_key=settings.openai_api_key))
    days = _ask_model(name, factory)
    if days is UNAVAILABLE:
        # Deliberately not cached: the caller falls back to a cheaper tier now,
        # and the next attempt should try again rather than inherit the outage.
        return None

    # A genuine "cannot say" is cached, so an unanswerable item is not re-asked
    # on every single add.
    active_cache.set(CACHE_NAMESPACE, cache_key, {"days": days})
    return days
