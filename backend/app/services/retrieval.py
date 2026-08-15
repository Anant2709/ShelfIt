"""Choosing which known items to show a resolver.

Retrieval is deliberately separate from judgment. Picking what to put in front of
a model is not the same act as making the decision, and the failure modes differ:
a poor retrieval means the model reasons without a useful reference, whereas a
poor *match* used to become the answer outright -- which is how an earlier version
gave "milk chocolate" a five-day dairy shelf life.

Because these scores only order reference material, being approximate is fine.
Nothing here may ever be used to answer a question directly.

Shared by the shelf-life and category resolvers so there is one implementation to
reason about, generic over the value type since one maps names to day counts and
the other to categories.
"""

from __future__ import annotations

import re
from typing import TypeVar

T = TypeVar("T")


def tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value))


def relevance(key: str, name: str, name_tokens: set[str]) -> int:
    """How likely a known item is to be a useful reference for `name`."""
    if key in name:
        # A contained phrase is the strongest signal, weighted by its length so a
        # specific match outranks a generic one.
        return 100 + len(key)
    shared = len(tokens(key) & name_tokens)
    return shared * 10 if shared else 0


def top_matches(name: str, known: dict[str, T], limit: int) -> dict[str, T]:
    """The `limit` most relevant entries of `known`, most relevant first."""
    name_tokens = tokens(name)
    scored = [
        (relevance(key, name, name_tokens), key, value)
        for key, value in known.items()
    ]
    relevant = sorted(
        (item for item in scored if item[0] > 0), key=lambda item: -item[0]
    )
    return {key: value for _, key, value in relevant[:limit]}
