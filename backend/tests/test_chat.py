"""Tests for the inventory-grounded assistant.

The fake client streams, because streaming is the only implementation -- the
buffered path drains the same generator.
"""

from datetime import date, timedelta

from app.core import clock

import pytest

from app.core import config
from app.models.inventory import Disposition, Expiration, InventoryItem
from app.services.chatbot import (
    ChatUnavailableError,
    DoneEvent,
    TokenEvent,
    ToolEvent,
    build_inventory_context,
    build_messages,
    generate_chat_reply,
    stream_chat_turn,
    to_snapshot,
)
from chat_doubles import (
    FakeClient,
    chunk,
    connection_error,
    text_chunks,
    tool_call_chunks,
    tool_delta,
)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(config.settings, "openai_api_key", "test-key")


def add_item(db, name="Milk", quantity=1.0, unit="l", days=2, category="dairy"):
    item = InventoryItem(
        name=name,
        quantity=quantity,
        unit=unit,
        category=category,
        category_source="dataset" if category else "unknown",
        user_id=db.info["user"].id,
    )
    db.add(item)
    db.flush()
    db.add(
        Expiration(
            item_id=item.id,
            expiration_date=(
                clock.today() + timedelta(days=days) if days is not None else None
            ),
            source="user",
        )
    )
    db.commit()
    db.refresh(item)
    return item


def run(db, message, responses, **kwargs):
    kwargs.setdefault("user_id", db.info["user"].id)
    client = FakeClient(responses)
    return generate_chat_reply(
        db, message, client_factory=lambda: client, **kwargs
    ), client


class TestExpiryPhrasing:
    """The model is told how urgent something is, never asked to work it out.

    A model does not reliably know today's date and cannot be trusted to subtract
    two dates, so handing it a raw expiry and hoping is the one thing to avoid.
    """

    def context_for(self, days):
        today = date(2026, 8, 15)
        expiry = today + timedelta(days=days) if days is not None else None
        return build_inventory_context(
            [
                {
                    "id": "abc",
                    "name": "Milk",
                    "category": "dairy",
                    "quantity": 1,
                    "unit": "l",
                    "expiration_date": expiry,
                }
            ],
            today=today,
        )

    def test_todays_date_is_stated(self):
        assert "Today is 2026-08-15." in self.context_for(2)

    def test_days_remaining_is_spelled_out(self):
        assert "2 days left" in self.context_for(2)

    def test_one_day_is_singular(self):
        assert "1 day left" in self.context_for(1)

    def test_expiring_today_is_emphasised(self):
        assert "expires TODAY" in self.context_for(0)

    def test_expired_says_how_long_ago(self):
        assert "EXPIRED 6 days ago" in self.context_for(-6)

    def test_expired_yesterday_is_singular(self):
        assert "EXPIRED yesterday" in self.context_for(-1)

    def test_missing_date_is_stated_not_omitted(self):
        """Silence would read as "fine"; the model should know it is unknown."""
        assert "no expiry date recorded" in self.context_for(None)

    def test_raw_dates_are_not_used_for_urgency(self):
        assert "2026-08-17" not in self.context_for(2)


class TestInventoryContext:
    def test_empty_inventory_is_stated_explicitly(self):
        context = build_inventory_context([], today=date(2026, 8, 15))
        assert "Inventory is empty." in context
        assert "Today is 2026-08-15." in context

    def test_ids_are_included_so_tools_can_reference_items(self):
        context = build_inventory_context(
            [
                {
                    "id": "item-1",
                    "name": "Milk",
                    "category": "dairy",
                    "quantity": 1,
                    "unit": "l",
                    "expiration_date": None,
                }
            ]
        )
        assert "id=item-1" in context

    def test_countable_items_use_multiplier_notation(self):
        context = build_inventory_context(
            [
                {
                    "id": "a",
                    "name": "Eggs",
                    "category": "dairy",
                    "quantity": 12,
                    "unit": "count",
                    "expiration_date": None,
                }
            ]
        )
        assert "x12" in context

    def test_measured_items_include_the_unit(self):
        context = build_inventory_context(
            [
                {
                    "id": "a",
                    "name": "Milk",
                    "category": "dairy",
                    "quantity": 1.5,
                    "unit": "l",
                    "expiration_date": None,
                }
            ]
        )
        assert "1.5 l" in context

    def test_missing_unit_defaults_to_count(self):
        context = build_inventory_context(
            [
                {
                    "id": "a",
                    "name": "Apples",
                    "category": None,
                    "quantity": 3,
                    "unit": None,
                    "expiration_date": None,
                }
            ]
        )
        assert "x3" in context

    def test_absent_category_is_named_not_shown_as_none(self):
        context = build_inventory_context(
            [
                {
                    "id": "a",
                    "name": "Leftovers",
                    "category": None,
                    "quantity": 1,
                    "unit": "count",
                    "expiration_date": None,
                }
            ]
        )
        assert "uncategorised" in context
        assert "None" not in context

    def test_most_urgent_item_comes_first(self):
        today = date(2026, 8, 15)
        items = [
            {
                "id": "later",
                "name": "Rice",
                "category": "grains_pulses",
                "quantity": 1,
                "unit": "kg",
                "expiration_date": today + timedelta(days=200),
            },
            {
                "id": "gone",
                "name": "Yogurt",
                "category": "dairy",
                "quantity": 1,
                "unit": "count",
                "expiration_date": today - timedelta(days=3),
            },
        ]
        lines = build_inventory_context(items, today=today).splitlines()
        item_lines = [line for line in lines if line.startswith("- ")]
        assert "Yogurt" in item_lines[0]
        assert "Rice" in item_lines[1]

    def test_undated_items_come_last(self):
        today = date(2026, 8, 15)
        items = [
            {
                "id": "salt",
                "name": "Salt",
                "category": "spices_condiments",
                "quantity": 1,
                "unit": "kg",
                "expiration_date": None,
            },
            {
                "id": "milk",
                "name": "Milk",
                "category": "dairy",
                "quantity": 1,
                "unit": "l",
                "expiration_date": today + timedelta(days=300),
            },
        ]
        item_lines = [
            line
            for line in build_inventory_context(items, today=today).splitlines()
            if line.startswith("- ")
        ]
        assert "Milk" in item_lines[0]
        assert "Salt" in item_lines[1]


class TestSnapshot:
    def test_orm_items_are_flattened(self, db):
        item = add_item(db, name="Paneer", quantity=200, unit="g", days=3)
        snapshot = to_snapshot([item])
        assert snapshot[0]["id"] == item.id
        assert snapshot[0]["name"] == "Paneer"
        assert snapshot[0]["category"] == "dairy"
        assert snapshot[0]["expiration_date"] == clock.today() + timedelta(days=3)

    def test_item_without_an_expiration_row_has_no_date(self, db):
        item = InventoryItem(
            name="Salt", quantity=1, unit="kg", user_id=db.info["user"].id
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        assert to_snapshot([item])[0]["expiration_date"] is None


class TestMessageAssembly:
    def test_inventory_is_a_separate_system_message(self):
        """Context outside the user turn limits prompt-injection surface.

        It matters more now: the assistant can act on what it reads there.
        """
        messages = build_messages("what can I cook?", [], "Inventory is empty.")
        assert [message["role"] for message in messages] == [
            "system",
            "system",
            "user",
        ]
        assert messages[2]["content"] == "what can I cook?"

    def test_history_sits_between_context_and_the_new_message(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        messages = build_messages("now what?", history, "Inventory is empty.")
        assert [message["role"] for message in messages] == [
            "system",
            "system",
            "user",
            "assistant",
            "user",
        ]
        assert messages[-1]["content"] == "now what?"


class TestPlainReply:
    def test_reply_is_assembled_from_streamed_tokens(self, db, with_key):
        turn, _ = run(db, "ideas?", [text_chunks("Make ", "toast", ".")])
        assert turn.reply == "Make toast."

    def test_reply_is_stripped(self, db, with_key):
        turn, _ = run(db, "ideas?", [text_chunks("  Make toast.  ")])
        assert turn.reply == "Make toast."

    def test_no_tool_calls_means_no_actions(self, db, with_key):
        turn, _ = run(db, "ideas?", [text_chunks("Nothing to do.")])
        assert turn.tool_results == ()

    def test_configured_model_is_used(self, db, with_key, monkeypatch):
        monkeypatch.setattr(config.settings, "openai_model", "gpt-4o")
        _, client = run(db, "hi", [text_chunks("hello")])
        assert client.chat.completions.calls[0]["model"] == "gpt-4o"

    def test_streaming_is_requested(self, db, with_key):
        _, client = run(db, "hi", [text_chunks("hello")])
        assert client.chat.completions.calls[0]["stream"] is True

    def test_tools_are_offered(self, db, with_key):
        _, client = run(db, "hi", [text_chunks("hello")])
        names = [
            tool["function"]["name"]
            for tool in client.chat.completions.calls[0]["tools"]
        ]
        assert sorted(names) == ["add_item", "record_disposition"]

    def test_chunks_without_choices_are_skipped(self, db, with_key):
        """Some providers emit a final chunk carrying only usage information."""
        turn, _ = run(db, "hi", [[chunk(choices=False), chunk(content="hello")]])
        assert turn.reply == "hello"

    def test_missing_key_raises_rather_than_returning_prose(self, db, monkeypatch):
        monkeypatch.setattr(config.settings, "openai_api_key", None)
        with pytest.raises(ChatUnavailableError):
            generate_chat_reply(db, "hi")

    def test_no_inventory_still_produces_a_grounded_prompt(self, db, with_key):
        _, client = run(db, "ideas?", [text_chunks("Buy something.")])
        context = client.chat.completions.calls[0]["messages"][1]["content"]
        assert "Inventory is empty." in context


class TestEventStream:
    def test_tokens_arrive_as_they_stream(self, db, with_key):
        client = FakeClient([text_chunks("a", "b")])
        events = list(
            stream_chat_turn(db, "hi", client_factory=lambda: client)
        )
        tokens = [event.text for event in events if isinstance(event, TokenEvent)]
        assert tokens == ["a", "b"]

    def test_done_event_carries_the_full_reply(self, db, with_key):
        client = FakeClient([text_chunks("a", "b")])
        events = list(stream_chat_turn(db, "hi", client_factory=lambda: client))
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reply == "ab"


class TestToolCalling:
    def disposition_script(self, item_id, quantity=None, outcome="consumed"):
        """A tool call delivered in fragments, then a follow-up prose answer."""
        arguments = f'{{"item_id": "{item_id}", "outcome": "{outcome}"'
        if quantity is not None:
            arguments += f', "quantity": {quantity}'
        arguments += "}"
        return [
            tool_call_chunks("record_disposition", arguments, split=True),
            text_chunks("Marked it used."),
        ]

    def test_disposition_is_recorded(self, db, with_key):
        item = add_item(db, name="Milk", quantity=1, unit="l")
        turn, _ = run(
            db,
            "I finished the milk",
            self.disposition_script(item.id),
            inventory=to_snapshot([item]),
        )
        assert turn.reply == "Marked it used."
        assert turn.tool_results[0].ok is True
        db.refresh(item)
        assert item.resolved_at is not None

    def test_arguments_split_across_chunks_are_reassembled(self, db, with_key):
        """The fragments are only valid JSON once concatenated."""
        item = add_item(db, name="Yogurt", quantity=400, unit="g")
        turn, _ = run(
            db,
            "used 150g",
            self.disposition_script(item.id, quantity=150),
            inventory=to_snapshot([item]),
        )
        assert turn.tool_results[0].ok is True
        db.refresh(item)
        assert item.quantity == 250

    def test_action_is_attributed_to_the_assistant(self, db, with_key):
        """A change the model made must not look like one the user made."""
        item = add_item(db)
        run(
            db,
            "finished it",
            self.disposition_script(item.id),
            inventory=to_snapshot([item]),
        )
        event = db.query(Disposition).one()
        assert event.source == "assistant"

    def test_tool_result_is_fed_back_to_the_model(self, db, with_key):
        item = add_item(db)
        _, client = run(
            db,
            "finished it",
            self.disposition_script(item.id),
            inventory=to_snapshot([item]),
        )
        second_request = client.chat.completions.calls[1]["messages"]
        roles = [message["role"] for message in second_request]
        assert "tool" in roles
        assert second_request[-1]["tool_call_id"] == "call-1"

    def test_summary_is_human_readable(self, db, with_key):
        item = add_item(db, name="Milk", quantity=1, unit="l")
        turn, _ = run(
            db,
            "finished it",
            self.disposition_script(item.id),
            inventory=to_snapshot([item]),
        )
        assert turn.tool_results[0].summary == "Recorded 1 l of Milk as used."

    def test_add_item_creates_it_through_the_normal_cascade(self, db, with_key):
        script = [
            tool_call_chunks(
                "add_item", '{"name": "Paneer", "quantity": 200, "unit": "g"}'
            ),
            text_chunks("Added it."),
        ]
        turn, _ = run(db, "I bought paneer", script)
        assert turn.tool_results[0].ok is True
        item = db.query(InventoryItem).filter(InventoryItem.name == "Paneer").one()
        assert item.quantity == 200
        assert item.unit == "g"
        # Went through create_item, so it has an expiration row like any other.
        assert item.expiration is not None

    def test_two_tool_calls_in_one_turn_both_run(self, db, with_key):
        first = add_item(db, name="Milk", quantity=1, unit="l")
        second = add_item(db, name="Bread", quantity=1, unit="count")
        script = [
            [
                chunk(
                    tool_calls=[
                        tool_delta(
                            index=0,
                            call_id="call-1",
                            name="record_disposition",
                            arguments=(
                                f'{{"item_id": "{first.id}", '
                                '"outcome": "consumed"}'
                            ),
                        ),
                        tool_delta(
                            index=1,
                            call_id="call-2",
                            name="record_disposition",
                            arguments=(
                                f'{{"item_id": "{second.id}", '
                                '"outcome": "wasted"}'
                            ),
                        ),
                    ]
                )
            ],
            text_chunks("Done."),
        ]
        turn, _ = run(
            db,
            "drank the milk and binned the bread",
            script,
            inventory=to_snapshot([first, second]),
        )
        assert [result.ok for result in turn.tool_results] == [True, True]
        assert db.query(Disposition).count() == 2

    def test_tool_events_are_emitted_in_the_stream(self, db, with_key):
        item = add_item(db)
        client = FakeClient(self.disposition_script(item.id))
        events = list(
            stream_chat_turn(
                db,
                "finished it",
                inventory=to_snapshot([item]),
                client_factory=lambda: client,
            )
        )
        assert any(isinstance(event, ToolEvent) for event in events)

    def looping_client(self, item):
        """A model that never stops asking for tools."""
        forever = tool_call_chunks(
            "record_disposition",
            f'{{"item_id": "{item.id}", "outcome": "consumed"}}',
        )
        return FakeClient([forever] * 10)

    def test_tool_loop_is_bounded(self, db, with_key):
        """A model that keeps calling tools does not get unbounded spend."""
        from app.services.chat_tools import MAX_TOOL_ITERATIONS

        item = add_item(db)
        client = self.looping_client(item)
        generate_chat_reply(
            db, "go", inventory=to_snapshot([item]), client_factory=lambda: client
        )
        assert len(client.calls) == MAX_TOOL_ITERATIONS + 1

    def test_final_attempt_withholds_the_tools(self, db, with_key):
        """Removing them forces prose instead of another round trip."""
        item = add_item(db)
        client = self.looping_client(item)
        generate_chat_reply(
            db, "go", inventory=to_snapshot([item]), client_factory=lambda: client
        )
        assert "tools" not in client.calls[-1]


class TestToolRefusals:
    def call_with(self, name, arguments):
        return [tool_call_chunks(name, arguments), text_chunks("Sorry, which one?")]

    def test_an_id_that_was_not_shown_is_refused(self, db, with_key):
        """The model chooses from what it was given; it does not supply ids.

        A hallucinated or stale id must fail loudly rather than reach a real row.
        """
        hidden = add_item(db, name="Milk")
        turn, _ = run(
            db,
            "finish the milk",
            self.call_with(
                "record_disposition",
                f'{{"item_id": "{hidden.id}", "outcome": "consumed"}}',
            ),
            inventory=[],
        )
        assert turn.tool_results[0].ok is False
        assert db.query(Disposition).count() == 0
        db.refresh(hidden)
        assert hidden.resolved_at is None

    def test_invented_id_is_refused(self, db, with_key):
        item = add_item(db)
        turn, _ = run(
            db,
            "finish it",
            self.call_with(
                "record_disposition",
                '{"item_id": "made-up", "outcome": "consumed"}',
            ),
            inventory=to_snapshot([item]),
        )
        assert turn.tool_results[0].ok is False

    def test_unknown_outcome_is_refused(self, db, with_key):
        item = add_item(db)
        turn, _ = run(
            db,
            "lost it",
            self.call_with(
                "record_disposition",
                f'{{"item_id": "{item.id}", "outcome": "lost"}}',
            ),
            inventory=to_snapshot([item]),
        )
        assert turn.tool_results[0].ok is False

    def test_disposing_more_than_remains_is_refused(self, db, with_key):
        item = add_item(db, quantity=1, unit="l")
        turn, _ = run(
            db,
            "drank 5 litres",
            self.call_with(
                "record_disposition",
                f'{{"item_id": "{item.id}", "outcome": "consumed", "quantity": 5}}',
            ),
            inventory=to_snapshot([item]),
        )
        assert turn.tool_results[0].ok is False
        db.refresh(item)
        assert item.quantity == 1

    def test_malformed_arguments_are_refused(self, db, with_key):
        item = add_item(db)
        turn, _ = run(
            db,
            "go",
            self.call_with("record_disposition", "{not json"),
            inventory=to_snapshot([item]),
        )
        assert turn.tool_results[0].ok is False

    def test_unknown_tool_name_is_refused(self, db, with_key):
        turn, _ = run(db, "go", self.call_with("delete_everything", "{}"))
        assert turn.tool_results[0].ok is False

    def test_refusal_is_still_reported_to_the_user(self, db, with_key):
        """A failed action must be visible, not silently swallowed."""
        turn, _ = run(db, "go", self.call_with("delete_everything", "{}"))
        assert turn.tool_results[0].summary


class TestProviderFailure:
    def test_failure_before_any_tool_call_raises(self, db, with_key):
        client = FakeClient([], error=connection_error())
        with pytest.raises(ChatUnavailableError):
            generate_chat_reply(db, "hi", client_factory=lambda: client)

    def test_failure_after_a_tool_call_reports_what_changed(self, db, with_key):
        """The write already happened; a bare 503 would hide it.

        The inventory would be altered with nothing explaining why, so the tool
        summaries become the reply instead.
        """
        item = add_item(db, name="Milk", quantity=1, unit="l")

        def fail_on_second(call_number):
            if call_number == 2:
                raise connection_error()

        client = FakeClient(
            [
                tool_call_chunks(
                    "record_disposition",
                    f'{{"item_id": "{item.id}", "outcome": "consumed"}}',
                ),
                text_chunks("unreachable"),
            ],
            error=fail_on_second,
        )
        turn = generate_chat_reply(
            db,
            "finished it",
            inventory=to_snapshot([item]),
            client_factory=lambda: client,
        )
        assert "Milk" in turn.reply
        db.refresh(item)
        assert item.resolved_at is not None
