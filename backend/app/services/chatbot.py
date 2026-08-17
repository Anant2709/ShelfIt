"""The inventory-grounded assistant.

Three things make this more than a wrapper around a chat completion.

**Expiry is pre-computed, not handed over as dates.** A language model does not
reliably know today's date and cannot be trusted to subtract one date from another,
so asking it to work out that milk expiring 2026-08-17 is urgent is asking it to do
the one thing it is worst at. The prompt says "2 days left" and "EXPIRED 6 days
ago", because the server already knows. The same rule the badges use produces the
wording, so the assistant and the interface cannot disagree about urgency.

**The inventory is rebuilt every turn and never stored in the transcript.** If the
fridge were captured in the history, a model on turn four would be reasoning about
the fridge as it was on turn one, and would confidently suggest recipes using milk
the user has already drunk. Only what the person said and what the assistant
replied is durable; current state is regenerated.

**Streaming is the only implementation.** `generate_chat_reply` drains the same
generator the streaming endpoint consumes, so the two transports cannot drift into
answering differently -- which they would, given the tool loop is the complicated
part and nobody would remember to fix it twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Module import rather than a direct one, because `today` is a parameter name below.
from app.core import clock
from typing import Any, Iterable, Iterator

from openai import OpenAI, OpenAIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.inventory import InventoryItem
from app.services.chat_tools import (
    MAX_TOOL_ITERATIONS,
    TOOL_SCHEMAS,
    ToolResult,
    TurnLedger,
    execute_tool,
    named_item_ids,
)
from app.services.urgency import days_until, sort_key

# Suggesting and recording are kept in separate sections on purpose. An earlier
# version opened with a blanket "prefer items expiring soonest", which was meant as
# advice for recommendations. Measured against two items sharing a name, the model
# applied it to tool targets too and recorded the most urgent item in the whole
# fridge -- bread -- when asked about paneer. Urgency is a reason to *suggest*
# something and never a reason to act on it, so the two rules no longer share a
# paragraph and the recording rule states the exclusion outright.
SYSTEM_PROMPT = (
    "You are Shelf It, a grocery assistant that can see and update the user's "
    "kitchen inventory.\n"
    "\n"
    "WHEN SUGGESTING what to cook or use up: ground every answer in the inventory "
    "you are given, prefer items expiring soonest, and say plainly when something "
    "has already expired rather than suggesting the user eat it.\n"
    "\n"
    "WHEN RECORDING that something was used or thrown away: act only on the item "
    "the user actually named. How urgent an item is has no bearing on which item "
    "to record -- never substitute a more urgent or sooner-expiring item for the "
    "one the user mentioned. If what they named is not in the inventory, say so "
    "and record nothing. If more than one item could match, ask which they mean "
    "and record nothing until they answer. Record one item per request unless the "
    "user clearly named several.\n"
    "\n"
    "You may also add items the user says they bought. Only record or add when the "
    "user has clearly stated it; never because it seems likely. You cannot delete "
    "or edit items; if the user wants that, tell them to do it in the app.\n"
    "\n"
    "After using a tool, tell the user in one short sentence what you changed.\n"
    "\n"
    "If asked something unrelated to food, answer briefly, mention that you are a "
    "grocery assistant, and steer back to the kitchen."
)


class ChatUnavailableError(RuntimeError):
    """The assistant could not answer for an infrastructural reason.

    Raised for missing configuration or an upstream provider failure, so the API
    layer can return a deliberate status code instead of leaking a stack trace.
    """


@dataclass(frozen=True)
class TokenEvent:
    """A fragment of the reply, as it arrives."""

    text: str


@dataclass(frozen=True)
class ToolEvent:
    """A tool the assistant ran, surfaced so the user sees what it changed."""

    result: ToolResult


@dataclass(frozen=True)
class DoneEvent:
    """The finished reply."""

    reply: str


ChatEvent = TokenEvent | ToolEvent | DoneEvent


@dataclass(frozen=True)
class ChatTurn:
    reply: str
    tool_results: tuple[ToolResult, ...] = ()


def to_snapshot(items: Iterable[InventoryItem]) -> list[dict[str, Any]]:
    """Flatten ORM items into the plain shape the prompt builder consumes."""
    return [
        {
            "id": item.id,
            "name": item.name,
            "category": item.category,
            "quantity": item.quantity,
            "unit": item.unit,
            "expiration_date": (
                item.expiration.expiration_date if item.expiration else None
            ),
        }
        for item in items
    ]


def _quantity_phrase(quantity: Any, unit: str | None) -> str:
    unit = unit or "count"
    if unit == "count":
        return f"x{quantity}"
    return f"{quantity} {unit}"


def _expiry_phrase(expiration_date: date | None, today: date | None = None) -> str:
    """Urgency in words, because the model should not be doing date arithmetic."""
    remaining = days_until(expiration_date, today)
    if remaining is None:
        return "no expiry date recorded"
    if remaining < 0:
        gone = abs(remaining)
        return "EXPIRED yesterday" if gone == 1 else f"EXPIRED {gone} days ago"
    if remaining == 0:
        return "expires TODAY"
    if remaining == 1:
        return "1 day left"
    return f"{remaining} days left"


def build_inventory_context(
    items: list[dict[str, Any]], today: date | None = None
) -> str:
    """The inventory as the model sees it, most urgent first.

    Ids are included because the tools take an id rather than a name, so this list
    is also the set of items the assistant is permitted to act on.
    """
    reference = today or clock.today()
    header = f"Today is {reference.isoformat()}."
    if not items:
        return f"{header}\n\nInventory is empty."

    ordered = sorted(
        items, key=lambda item: sort_key(item.get("expiration_date"), reference)
    )
    lines = []
    for item in ordered:
        category = item.get("category") or "uncategorised"
        quantity = _quantity_phrase(item.get("quantity"), item.get("unit"))
        expiry = _expiry_phrase(item.get("expiration_date"), reference)
        lines.append(
            f"- id={item['id']} | {item['name']} ({category}), {quantity} — {expiry}"
        )
    body = "\n".join(lines)
    return f"{header}\n\nInventory, most urgent first:\n{body}"


def build_messages(
    message: str,
    history: list[dict[str, str]],
    inventory_context: str,
) -> list[dict[str, Any]]:
    """Assemble the request.

    Inventory is a system message rather than part of the user turn, so untrusted
    text cannot masquerade as trusted context -- which matters more now that the
    model can act on what it reads there.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": inventory_context},
    ]
    messages.extend(
        {"role": turn["role"], "content": turn["content"]} for turn in history
    )
    messages.append({"role": "user", "content": message})
    return messages


def _merge_tool_call_deltas(
    collected: dict[int, dict[str, Any]], deltas: Iterable[Any]
) -> None:
    """Reassemble streamed tool calls.

    Arguments arrive as fragments across many chunks and are only valid JSON once
    concatenated, so each call is accumulated by its index.
    """
    for delta in deltas:
        index = getattr(delta, "index", 0) or 0
        entry = collected.setdefault(index, {"id": None, "name": "", "arguments": ""})
        if getattr(delta, "id", None):
            entry["id"] = delta.id
        function = getattr(delta, "function", None)
        if function is None:
            continue
        if getattr(function, "name", None):
            entry["name"] = function.name
        if getattr(function, "arguments", None):
            entry["arguments"] += function.arguments


def _assistant_tool_message(calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            }
            for call in calls
        ],
    }


def stream_chat_turn(
    db: Session,
    message: str,
    history: list[dict[str, str]] | None = None,
    inventory: list[dict[str, Any]] | None = None,
    client_factory=None,
    user_id: str | None = None,
    today: date | None = None,
) -> Iterator[ChatEvent]:
    """Run one turn, yielding tokens and tool results as they happen.

    Tool calls are executed between model calls, so a turn may make several
    requests. The loop is bounded: a model that keeps calling tools is confused or
    looping, and neither deserves unbounded spend.
    """
    if not settings.openai_api_key:
        raise ChatUnavailableError(
            "The assistant is not configured. Set OPENAI_API_KEY to enable chat."
        )

    inventory = inventory or []
    visible_ids = frozenset(item["id"] for item in inventory)
    messages = build_messages(
        message,
        history or [],
        build_inventory_context(inventory, today=today),
    )

    factory = client_factory or (lambda: OpenAI(api_key=settings.openai_api_key))
    client = factory()

    tool_results: list[ToolResult] = []
    reply_parts: list[str] = []
    # One ledger per turn, spanning every tool iteration, since the failures it
    # guards against show up as calls in separate iterations of this loop.
    ledger = TurnLedger(named=named_item_ids(message, inventory), user_id=user_id)

    for iteration in range(MAX_TOOL_ITERATIONS + 1):
        # The final iteration withholds the tools, which forces a prose answer
        # instead of letting the model spend another round trip on a tool it has
        # already been given the result of.
        offer_tools = iteration < MAX_TOOL_ITERATIONS
        request: dict[str, Any] = {
            "model": settings.openai_model,
            "messages": messages,
            "max_tokens": 400,
            "stream": True,
        }
        if offer_tools:
            request["tools"] = TOOL_SCHEMAS

        try:
            stream = client.chat.completions.create(**request)
            collected_calls: dict[int, dict[str, Any]] = {}
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None)
                if text:
                    reply_parts.append(text)
                    yield TokenEvent(text=text)
                calls = getattr(delta, "tool_calls", None)
                if calls:
                    _merge_tool_call_deltas(collected_calls, calls)
        except OpenAIError as exc:
            if tool_results:
                # Something was already changed on the user's behalf. Losing that
                # to a 503 would leave the inventory altered with no explanation,
                # so the summaries become the reply.
                reply = " ".join(result.summary for result in tool_results)
                yield DoneEvent(reply=reply)
                return
            raise ChatUnavailableError(
                "The assistant is temporarily unavailable. Please try again."
            ) from exc

        if not collected_calls:
            break

        ordered_calls = [collected_calls[index] for index in sorted(collected_calls)]
        messages.append(_assistant_tool_message(ordered_calls))
        for call in ordered_calls:
            result = execute_tool(
                db,
                name=call["name"],
                raw_arguments=call["arguments"],
                visible_ids=visible_ids,
                ledger=ledger,
            )
            tool_results.append(result)
            yield ToolEvent(result=result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result.to_content(),
                }
            )

    yield DoneEvent(reply="".join(reply_parts).strip())


def generate_chat_reply(
    db: Session,
    message: str,
    history: list[dict[str, str]] | None = None,
    inventory: list[dict[str, Any]] | None = None,
    client_factory=None,
    user_id: str | None = None,
    today: date | None = None,
) -> ChatTurn:
    """Run one turn to completion.

    Drains the streaming generator rather than reimplementing the tool loop, so
    the buffered and streamed endpoints cannot answer differently.
    """
    reply = ""
    tool_results: list[ToolResult] = []
    for event in stream_chat_turn(
        db,
        message,
        history=history,
        inventory=inventory,
        client_factory=client_factory,
        user_id=user_id,
        today=today,
    ):
        if isinstance(event, ToolEvent):
            tool_results.append(event.result)
        elif isinstance(event, DoneEvent):
            reply = event.reply
    return ChatTurn(reply=reply, tool_results=tuple(tool_results))
