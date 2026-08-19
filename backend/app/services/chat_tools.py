"""What the assistant is allowed to do to the inventory, and how it is constrained.

Letting a language model write to a database on its reading of a sentence is the
riskiest thing in this codebase, so the boundaries are drawn deliberately.

**It cannot delete.** `DELETE` erases an item and its entire outcome history; a
disposition appends an event that a person can inspect and reason about afterwards.
So the assistant gets the append-only operations and never the destructive one. If
it misunderstands "I finished the milk", the worst case is a wrong event in a log,
not the silent loss of records. This is the same distinction the disposition work
established -- delete is a correction, disposition is an outcome -- and here it
doubles as a privilege boundary.

**It cannot name an item, only choose one.** Tools take an `item_id`, and the id
must be one that was actually shown to the model this turn. Matching on a free-text
name would mean "mark the yogurt as finished" could resolve to the wrong yogurt, or
to an item that was never in front of the model at all. This is the same principle
as the closed set of categories: the model selects from what it was given rather
than producing an identifier, so a hallucination fails loudly instead of hitting a
real row.

**It cannot edit.** No renaming, no changing quantities or categories. Those are
corrections to a record, and a correction the user did not ask for is
indistinguishable from corruption.

Failures are returned to the model rather than raised, so it can explain the
problem in the reply instead of the request collapsing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.inventory import InventoryItem
from app.services.disposition import (
    CONSUMED,
    OUTCOMES,
    WASTED,
    DispositionError,
    apply_disposition,
)
from app.services.inventory import create_item

# Anything written by a tool call is attributed to the assistant, never the user.
ASSISTANT_SOURCE = "assistant"

# A turn that keeps calling tools is either confused or looping. Three is enough
# for "mark two things used then answer" and short enough to bound the cost.
MAX_TOOL_ITERATIONS = 3


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "record_disposition",
            "description": (
                "Record that the user has eaten, used up, or thrown away an item "
                "that is in their inventory. Only call this when the user clearly "
                "states something has been used or binned. Never call it to guess."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": (
                            "The id of the item, copied exactly from the inventory "
                            "list you were given. Do not invent an id."
                        ),
                    },
                    "outcome": {
                        "type": "string",
                        "enum": [CONSUMED, WASTED],
                        "description": (
                            "'consumed' if it was eaten or used, 'wasted' if it "
                            "was thrown away."
                        ),
                    },
                    "quantity": {
                        "type": "number",
                        "description": (
                            "How much, in the item's own unit. Pass this when the "
                            "user names an amount (two tomatoes, 150g). Omit only "
                            "to record the entire remaining amount."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief reason, if the user gave one.",
                    },
                },
                "required": ["item_id", "outcome"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_item",
            "description": (
                "Add a new item to the inventory. Only call this when the user "
                "says they have bought or acquired something. The expiry date and "
                "category are worked out automatically, so do not ask for them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The item name, e.g. 'Paneer'.",
                    },
                    "quantity": {
                        "type": "number",
                        "description": "How much. Defaults to 1.",
                    },
                    "unit": {
                        "type": "string",
                        "description": (
                            "Unit such as g, kg, ml, l, or 'count' for countable "
                            "things. Defaults to 'count'."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
    },
]


def named_item_ids(message: str, inventory: list[dict[str, Any]]) -> frozenset[str]:
    """Ids of items whose name the user literally typed.

    Guards against a measured failure that prompt wording only made rarer: asked
    "I used up the paneer", the model sometimes recorded *Whole Wheat Bread* — the
    most urgent item in the fridge, and the first one listed. It was applying
    "prefer items expiring soonest" to the choice of what to act on.

    Deliberately conservative. Matching is on the item's whole name appearing in
    the message, not on shared words, so "I used the whole packet" does not
    accidentally name "Whole Wheat Bread". When nothing matches — "I finished it",
    or any pronoun after a previous turn — this returns empty and imposes no
    constraint at all, because the alternative is refusing legitimate requests.

    So it only ever fires when the user did name something recognisable, and then
    only to insist the model act on what they named.
    """
    haystack = message.strip().lower()
    return frozenset(
        item["id"]
        for item in inventory
        if item.get("name") and item["name"].strip().lower() in haystack
    )


@dataclass
class TurnLedger:
    """What has already been written during the current turn.

    Exists to catch one measured failure. Asked "I used up the paneer" with two
    items of that name on the shelf, the model sometimes disposes *both* rather
    than asking which was meant. Prompt wording reduced how often that happens but
    could not remove it, so the rule is enforced here instead: two different items
    that share a name cannot both be recorded in one turn.

    The guard is precise rather than broad. Disposing two items with *different*
    names in one turn is a normal request ("I used the milk and the bread"), and
    recording the same item twice is coherent too ("used half, binned the rest"),
    so neither is blocked. Only the pattern that has no sensible reading is.
    """

    # Normalised item name -> the ids recorded under that name this turn.
    recorded: dict[str, set[str]] = field(default_factory=dict)
    # Ids the user named in this message. Empty means they named nothing that
    # matches the shelf, which is not a reason to refuse anything.
    named: frozenset[str] = frozenset()
    user_id: str | None = None

    def was_not_named(self, item_id: str) -> bool:
        return bool(self.named) and item_id not in self.named

    def conflicting_item(self, name: str, item_id: str) -> bool:
        seen = self.recorded.get(name.strip().lower())
        return seen is not None and item_id not in seen

    def note(self, name: str, item_id: str) -> None:
        self.recorded.setdefault(name.strip().lower(), set()).add(item_id)


@dataclass(frozen=True)
class ToolResult:
    """The outcome of one tool call.

    `payload` goes back to the model. `summary` is for the user interface, so a
    person can see what was changed on their behalf without reading JSON.

    `undo` is a handle the interface can use to reverse the change, and is kept out
    of `payload` deliberately: the model has no business undoing its own work, and
    telling it how would invite exactly that.
    """

    name: str
    ok: bool
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    undo: dict[str, str] | None = None

    def to_content(self) -> str:
        return json.dumps(self.payload)


def _failure(name: str, message: str) -> ToolResult:
    return ToolResult(
        name=name,
        ok=False,
        summary=message,
        payload={"error": message},
    )


def _record_disposition(
    db: Session,
    arguments: dict[str, Any],
    visible_ids: frozenset[str],
    ledger: TurnLedger,
) -> ToolResult:
    name = "record_disposition"

    item_id = arguments.get("item_id")
    if not isinstance(item_id, str) or item_id not in visible_ids:
        # Refused rather than looked up. An id outside what the model was shown is
        # either invented or stale, and neither should reach a real row.
        return _failure(
            name,
            "That item id was not in the inventory you were given. Ask the user "
            "which item they mean.",
        )

    outcome = arguments.get("outcome")
    if outcome not in OUTCOMES:
        return _failure(name, "Outcome must be 'consumed' or 'wasted'.")

    quantity = arguments.get("quantity")
    if quantity is not None and not isinstance(quantity, (int, float)):
        return _failure(name, "Quantity must be a number.")
    if isinstance(quantity, bool):
        return _failure(name, "Quantity must be a number.")

    item = db.get(InventoryItem, item_id)
    if item is None:
        return _failure(name, "That item no longer exists.")

    if ledger.was_not_named(item.id):
        return _failure(
            name,
            f"The user did not mention {item.name!r}. Record only the item they "
            "named. How soon something expires is not a reason to act on it.",
        )

    if ledger.conflicting_item(item.name, item.id):
        return _failure(
            name,
            f"You have already recorded a different item called {item.name!r} in "
            "this turn. Do not record both. Ask the user which one they meant.",
        )

    reason = arguments.get("reason")
    try:
        event = apply_disposition(
            db,
            item,
            outcome=outcome,
            quantity=quantity,
            reason=reason if isinstance(reason, str) else None,
            source=ASSISTANT_SOURCE,
        )
    except DispositionError as exc:
        # Domain rules still apply to the assistant: it cannot dispose more than
        # remains, or touch an item that has already been fully resolved.
        return _failure(name, str(exc))

    db.commit()
    db.refresh(item)
    ledger.note(event.item_name, item.id)
    unit = "" if event.unit == "count" else f" {event.unit}"
    verb = "used" if outcome == CONSUMED else "binned"
    return ToolResult(
        name=name,
        ok=True,
        summary=f"Recorded {event.quantity:g}{unit} of {event.item_name} as {verb}.",
        undo={"item_id": item.id, "disposition_id": event.id},
        payload={
            "recorded": True,
            "item": event.item_name,
            "outcome": outcome,
            "quantity": event.quantity,
            "unit": event.unit,
            "remaining": item.quantity,
            "fully_used_up": item.resolved_at is not None,
        },
    )


def _add_item(
    db: Session,
    arguments: dict[str, Any],
    visible_ids: frozenset[str],
    ledger: TurnLedger,
) -> ToolResult:
    name = "add_item"

    item_name = arguments.get("name")
    if not isinstance(item_name, str) or not item_name.strip():
        return _failure(name, "An item name is required.")

    quantity = arguments.get("quantity", 1.0)
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        return _failure(name, "Quantity must be a number.")
    if quantity <= 0:
        return _failure(name, "Quantity must be greater than zero.")

    unit = arguments.get("unit", "count")
    if not isinstance(unit, str) or not unit.strip():
        unit = "count"

    item = create_item(
        db,
        name=item_name.strip(),
        quantity=float(quantity),
        unit=unit.strip(),
        user_id=ledger.user_id,
    )
    expiry = item.expiration.expiration_date if item.expiration else None
    return ToolResult(
        name=name,
        ok=True,
        summary=f"Added {item.name} to your inventory.",
        payload={
            "added": True,
            "item": item.name,
            "quantity": item.quantity,
            "unit": item.unit,
            "category": item.category,
            "expiration_date": expiry.isoformat() if expiry else None,
            # Reported so the assistant can ask, rather than quietly leaving an
            # item that will never appear in a reminder.
            "needs_expiry_date": expiry is None,
        },
    )


_Handler = Callable[[Session, dict, frozenset[str], TurnLedger], ToolResult]

_HANDLERS: dict[str, _Handler] = {
    "record_disposition": _record_disposition,
    "add_item": _add_item,
}


def execute_tool(
    db: Session,
    name: str,
    raw_arguments: str,
    visible_ids: frozenset[str],
    ledger: TurnLedger | None = None,
) -> ToolResult:
    """Run one tool call, returning a result even when it fails."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return _failure(name, f"There is no tool called {name!r}.")

    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return _failure(name, "The tool arguments were not valid JSON.")
    if not isinstance(arguments, dict):
        return _failure(name, "The tool arguments must be an object.")

    return handler(db, arguments, visible_ids, ledger or TurnLedger())
