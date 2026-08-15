"""Shelf-life resolution by language model.

This replaces an earlier tier that called Spoonacular. That call could only
confirm a string was a recognisable food -- Spoonacular does not publish
shelf-life data -- after which the code returned a hardcoded 5 days and labelled
the result `source="api"`. So sugar and cooking oil were both reported as keeping
for five days, with provenance implying a data provider had said so.

The model is shown the shelf lives already known for similar items and asked to
either recognise the item as a variety of one of them, or estimate it outright. It
reports which known item it used, if any -- the *anchor*.

Why the anchor matters:

- It anchors "baby spinach" to the curated "spinach" value instead of inventing an
  independent number, so two spellings of one item agree.
- It turns a bare number into a checkable claim. "coconut milk: 5, derived from
  milk" is visibly wrong on inspection; "coconut milk: 5" is not.
- It separates two very different confidences: recognising a variant of a
  human-curated item inherits that human's judgment, while estimating with no
  reference does not.

Caching is not done here. The caller persists results in the learned store, which
is permanent, inspectable, and correctable -- properties a TTL cache lacks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from openai import OpenAI, OpenAIError

from app.core.config import settings

# Bounds for a plausible answer. One day is the shortest useful granularity and
# ten years exceeds any grocery item, so anything outside is a model error.
MIN_DAYS = 1
MAX_DAYS = 3650

PROMPT = """You determine how long a grocery item stays good, in whole days.

You are given shelf lives already recorded for similar items. If the item is
essentially a variety, brand, cut, or preparation of one of them, reuse that
item's number exactly and name it as the anchor. Only estimate independently when
none of them applies, and then set anchor to null.

Return JSON of one of these forms:
  {"days": 4, "anchor": "spinach"}
  {"days": 365, "anchor": null}
  {"days": null, "anchor": null}

Use the last form only if you genuinely cannot say. Assume typical domestic
storage: perishables refrigerated, shelf-stable goods in a pantry, packaging
unopened, bought fresh today. Give the conservative end of the normal range.

"baby spinach" is a variety of "spinach" and should anchor to it.
"milk chocolate" is not a variety of "milk" and should not anchor to it.
"""


@dataclass(frozen=True)
class Resolution:
    """A resolved shelf life and the known item it was derived from."""

    days: int
    anchor: str | None = None
    anchor_days: int | None = None

    @property
    def is_anchored(self) -> bool:
        return self.anchor is not None


class _Unavailable:
    """The call could not be made, as distinct from the model not knowing.

    The difference matters because results are persisted indefinitely. "The model
    cannot tell" is a real finding; "the network was down for a second" is not,
    and storing it would outlast the outage.
    """

    __slots__ = ()


UNAVAILABLE = _Unavailable()


def _format_candidates(candidates: dict[str, int]) -> str:
    if not candidates:
        return "No similar items are known yet."
    lines = [f"- {name}: {days} days" for name, days in candidates.items()]
    return "Shelf lives already known for similar items:\n" + "\n".join(lines)


def _parse(content: str | None, candidates: dict[str, int]) -> Resolution | None:
    """Validate the reply, discarding anything unusable.

    An anchor naming an item the model was not actually shown is dropped rather
    than stored: a hallucinated reference must not be recorded as a real one.
    """
    if not content:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    raw_days = payload.get("days")
    if raw_days is None or isinstance(raw_days, bool):
        return None
    try:
        days = int(raw_days)
    except (TypeError, ValueError):
        return None
    if days < MIN_DAYS or days > MAX_DAYS:
        return None

    anchor = payload.get("anchor")
    if not isinstance(anchor, str) or anchor.strip().lower() not in candidates:
        return Resolution(days=days)

    anchor = anchor.strip().lower()
    return Resolution(days=days, anchor=anchor, anchor_days=candidates[anchor])


def resolve_shelf_life(
    name: str,
    candidates: dict[str, int] | None = None,
    client_factory=None,
) -> Resolution | None:
    """Resolve a shelf life, or return None if no answer is available."""
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
                {"role": "user", "content": f"How long does this keep: {name}"},
            ],
            max_tokens=80,
        )
    except OpenAIError:
        return None

    return _parse(response.choices[0].message.content, candidates)
