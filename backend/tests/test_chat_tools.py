"""The tools the assistant may call, tested directly.

These are the guard rails on a language model writing to the database, so they are
exercised at the boundary rather than only through a scripted conversation. What
matters is that a bad call is *refused and reported*, never raised and never
partially applied.
"""

from types import SimpleNamespace

from app.models.inventory import Disposition, Expiration, InventoryItem
from app.services.chat_tools import (
    TOOL_SCHEMAS,
    TurnLedger,
    execute_tool,
    named_item_ids,
)
from app.services.chatbot import _merge_tool_call_deltas


def add_item(db, name="Milk", quantity=1.0, unit="l"):
    item = InventoryItem(
        name=name,
        quantity=quantity,
        unit=unit,
        category="dairy",
        user_id=db.info["user"].id,
    )
    db.add(item)
    db.flush()
    db.add(Expiration(item_id=item.id, expiration_date=None, source="unknown"))
    db.commit()
    db.refresh(item)
    return item


def owned_ledger(db, **kwargs):
    return TurnLedger(user_id=db.info["user"].id, **kwargs)


def add_via_tool(db, raw_arguments):
    return execute_tool(
        db,
        name="add_item",
        raw_arguments=raw_arguments,
        visible_ids=frozenset(),
        ledger=owned_ledger(db),
    )


def dispose(db, item_id, visible, ledger=None, **arguments):
    import json

    payload = {"item_id": item_id, "outcome": "consumed", **arguments}
    return execute_tool(
        db,
        name="record_disposition",
        raw_arguments=json.dumps(payload),
        visible_ids=frozenset(visible),
        ledger=ledger,
    )


class TestAvailableTools:
    def test_the_assistant_cannot_delete(self):
        """Delete erases history; a disposition appends to it.

        The model gets the append-only operations and never the destructive one,
        so a misread sentence costs a wrong log entry rather than lost records.
        """
        names = {tool["function"]["name"] for tool in TOOL_SCHEMAS}
        assert "delete_item" not in names
        assert not any("delete" in name for name in names)

    def test_the_assistant_cannot_edit(self):
        """An unrequested correction is indistinguishable from corruption."""
        names = {tool["function"]["name"] for tool in TOOL_SCHEMAS}
        assert "update_item" not in names
        assert names == {"record_disposition", "add_item"}

    def test_disposition_takes_an_id_rather_than_a_name(self):
        """Choosing from what it was shown, not naming something itself."""
        schema = next(
            tool
            for tool in TOOL_SCHEMAS
            if tool["function"]["name"] == "record_disposition"
        )
        properties = schema["function"]["parameters"]["properties"]
        assert "item_id" in properties
        assert "name" not in properties

    def test_outcome_is_constrained_to_the_two_real_ones(self):
        schema = next(
            tool
            for tool in TOOL_SCHEMAS
            if tool["function"]["name"] == "record_disposition"
        )
        outcome = schema["function"]["parameters"]["properties"]["outcome"]
        assert sorted(outcome["enum"]) == ["consumed", "wasted"]


class TestDispositionTool:
    def test_a_visible_item_can_be_disposed(self, db):
        item = add_item(db)
        result = dispose(db, item.id, [item.id])
        assert result.ok is True
        db.refresh(item)
        assert item.resolved_at is not None

    def test_partial_quantity_leaves_the_rest(self, db):
        item = add_item(db, quantity=400, unit="g")
        result = dispose(db, item.id, [item.id], quantity=150)
        assert result.ok is True
        db.refresh(item)
        assert item.quantity == 250

    def test_reason_is_recorded(self, db):
        item = add_item(db)
        dispose(db, item.id, [item.id], outcome="wasted", reason="went sour")
        assert db.query(Disposition).one().reason == "went sour"

    def test_non_string_reason_is_dropped_rather_than_stored(self, db):
        item = add_item(db)
        dispose(db, item.id, [item.id], reason=42)
        assert db.query(Disposition).one().reason is None

    def test_count_units_are_not_printed_in_the_summary(self, db):
        item = add_item(db, name="Eggs", quantity=6, unit="count")
        result = dispose(db, item.id, [item.id])
        assert result.summary == "Recorded 6 of Eggs as used."

    def test_wasted_reads_as_binned(self, db):
        item = add_item(db, name="Milk", quantity=1, unit="l")
        result = dispose(db, item.id, [item.id], outcome="wasted")
        assert result.summary == "Recorded 1 l of Milk as binned."

    def test_payload_tells_the_model_what_remains(self, db):
        item = add_item(db, quantity=400, unit="g")
        result = dispose(db, item.id, [item.id], quantity=150)
        assert result.payload["remaining"] == 250
        assert result.payload["fully_used_up"] is False

    def test_payload_flags_a_fully_used_item(self, db):
        item = add_item(db, quantity=1, unit="l")
        result = dispose(db, item.id, [item.id])
        assert result.payload["fully_used_up"] is True

    def test_an_undo_handle_is_returned(self, db):
        """What makes a plausible-but-wrong action correctable."""
        item = add_item(db)
        result = dispose(db, item.id, [item.id])
        event = db.query(Disposition).one()
        assert result.undo == {"item_id": item.id, "disposition_id": event.id}

    def test_the_undo_handle_is_withheld_from_the_model(self, db):
        """It has no business reversing its own work."""
        item = add_item(db)
        result = dispose(db, item.id, [item.id])
        assert "disposition_id" not in result.to_content()

    def test_a_refusal_has_nothing_to_undo(self, db):
        item = add_item(db)
        result = dispose(db, item.id, visible=[])
        assert result.undo is None


class TestDispositionRefusals:
    def test_an_id_outside_the_visible_set_is_refused(self, db):
        item = add_item(db)
        result = dispose(db, item.id, visible=[])
        assert result.ok is False
        assert db.query(Disposition).count() == 0

    def test_a_non_string_id_is_refused(self, db):
        result = execute_tool(
            db,
            name="record_disposition",
            raw_arguments='{"item_id": 7, "outcome": "consumed"}',
            visible_ids=frozenset(),
        )
        assert result.ok is False

    def test_a_visible_id_whose_row_has_gone_is_refused(self, db):
        """Deleted between being shown to the model and being acted on."""
        item = add_item(db)
        item_id = item.id
        db.delete(item)
        db.commit()
        result = dispose(db, item_id, [item_id])
        assert result.ok is False
        assert "no longer exists" in result.summary

    def test_an_unknown_outcome_is_refused(self, db):
        item = add_item(db)
        result = dispose(db, item.id, [item.id], outcome="lost")
        assert result.ok is False

    def test_a_string_quantity_is_refused(self, db):
        item = add_item(db)
        result = dispose(db, item.id, [item.id], quantity="a lot")
        assert result.ok is False
        assert db.query(Disposition).count() == 0

    def test_a_boolean_quantity_is_refused(self, db):
        """`True` is an int in Python, so it needs rejecting on purpose."""
        item = add_item(db)
        result = dispose(db, item.id, [item.id], quantity=True)
        assert result.ok is False
        db.refresh(item)
        assert item.quantity == 1.0

    def test_disposing_more_than_remains_is_refused(self, db):
        item = add_item(db, quantity=1, unit="l")
        result = dispose(db, item.id, [item.id], quantity=5)
        assert result.ok is False
        db.refresh(item)
        assert item.quantity == 1

    def test_an_already_resolved_item_is_refused(self, db):
        item = add_item(db, quantity=1, unit="l")
        dispose(db, item.id, [item.id])
        result = dispose(db, item.id, [item.id])
        assert result.ok is False


class TestNamedItemGuard:
    """The model may only act on an item the user actually named.

    Measured failure: asked "I used up the paneer", the model sometimes recorded
    *Whole Wheat Bread* -- the most urgent item and the first one listed. Scoping
    the urgency advice in the prompt made it rarer but did not stop it, so the rule
    is enforced deterministically here.
    """

    def test_only_the_named_item_is_matched(self, db):
        paneer = add_item(db, name="Paneer", quantity=200, unit="g")
        bread = add_item(db, name="Whole Wheat Bread", quantity=1, unit="count")
        inventory = [
            {"id": paneer.id, "name": "Paneer"},
            {"id": bread.id, "name": "Whole Wheat Bread"},
        ]
        assert named_item_ids("I used up the paneer", inventory) == frozenset(
            {paneer.id}
        )

    def test_substituting_a_more_urgent_item_is_refused(self, db):
        paneer = add_item(db, name="Paneer", quantity=200, unit="g")
        bread = add_item(db, name="Whole Wheat Bread", quantity=1, unit="count")
        ledger = TurnLedger(named=frozenset({paneer.id}))

        result = dispose(db, bread.id, [paneer.id, bread.id], ledger=ledger)

        assert result.ok is False
        assert "did not mention" in result.summary
        db.refresh(bread)
        assert bread.quantity == 1

    def test_the_named_item_is_still_allowed(self, db):
        paneer = add_item(db, name="Paneer", quantity=200, unit="g")
        bread = add_item(db, name="Whole Wheat Bread", quantity=1, unit="count")
        ledger = TurnLedger(named=frozenset({paneer.id}))
        assert dispose(db, paneer.id, [paneer.id, bread.id], ledger=ledger).ok

    def test_naming_nothing_imposes_no_constraint(self, db):
        """"I finished it" must keep working after a previous turn."""
        item = add_item(db, name="Paneer", quantity=200, unit="g")
        inventory = [{"id": item.id, "name": "Paneer"}]
        assert named_item_ids("I finished it", inventory) == frozenset()
        ledger = TurnLedger(named=frozenset())
        assert dispose(db, item.id, [item.id], ledger=ledger).ok is True

    def test_matching_is_on_the_whole_name_not_shared_words(self, db):
        """Otherwise "the whole packet" would name "Whole Wheat Bread"."""
        bread = add_item(db, name="Whole Wheat Bread", quantity=1, unit="count")
        inventory = [{"id": bread.id, "name": "Whole Wheat Bread"}]
        assert named_item_ids("I used the whole packet", inventory) == frozenset()

    def test_matching_ignores_case(self, db):
        item = add_item(db, name="Paneer", quantity=200, unit="g")
        inventory = [{"id": item.id, "name": "Paneer"}]
        assert named_item_ids("I finished the PANEER", inventory) == frozenset(
            {item.id}
        )

    def test_naming_two_items_allows_both(self, db):
        milk = add_item(db, name="Milk", quantity=1, unit="l")
        bread = add_item(db, name="Bread", quantity=1, unit="count")
        inventory = [
            {"id": milk.id, "name": "Milk"},
            {"id": bread.id, "name": "Bread"},
        ]
        named = named_item_ids("I used the milk and the bread", inventory)
        assert named == frozenset({milk.id, bread.id})

        ledger = TurnLedger(named=named)
        assert dispose(db, milk.id, [milk.id, bread.id], ledger=ledger).ok
        assert dispose(db, bread.id, [milk.id, bread.id], ledger=ledger).ok

    def test_every_item_of_a_named_kind_is_permitted(self, db):
        """Two Paneers are both "named"; which one is a separate problem."""
        first = add_item(db, name="Paneer", quantity=200, unit="g")
        second = add_item(db, name="Paneer", quantity=500, unit="g")
        inventory = [
            {"id": first.id, "name": "Paneer"},
            {"id": second.id, "name": "Paneer"},
        ]
        named = named_item_ids("I used up the paneer", inventory)
        assert named == frozenset({first.id, second.id})

    def test_items_without_a_name_are_skipped(self):
        assert named_item_ids("anything", [{"id": "x", "name": None}]) == frozenset()


class TestSameNameGuard:
    """Two items sharing a name cannot both be recorded in one turn.

    Measured behaviour: asked "I used up the paneer" with a 200 g and a 500 g
    Paneer on the shelf, the model sometimes disposed *both* instead of asking.
    Rewording the prompt reduced it but could not remove it, so the rule is
    enforced here where it is deterministic.

    The guard is narrow on purpose. It does not try to decide *which* Paneer was
    meant -- that is genuinely unknowable from an id -- only that recording both
    is never a correct reading of one sentence.
    """

    def test_a_single_item_is_recorded_normally(self, db):
        first = add_item(db, name="Paneer", quantity=200, unit="g")
        second = add_item(db, name="Paneer", quantity=500, unit="g")
        ledger = TurnLedger()
        result = dispose(db, first.id, [first.id, second.id], ledger=ledger)
        assert result.ok is True

    def test_a_second_item_of_the_same_name_is_refused(self, db):
        first = add_item(db, name="Paneer", quantity=200, unit="g")
        second = add_item(db, name="Paneer", quantity=500, unit="g")
        visible = [first.id, second.id]
        ledger = TurnLedger()

        dispose(db, first.id, visible, ledger=ledger)
        result = dispose(db, second.id, visible, ledger=ledger)

        assert result.ok is False
        assert "already recorded" in result.summary
        db.refresh(second)
        assert second.quantity == 500, "the second item is left untouched"

    def test_the_refusal_tells_the_model_to_ask(self, db):
        first = add_item(db, name="Paneer", quantity=200, unit="g")
        second = add_item(db, name="Paneer", quantity=500, unit="g")
        visible = [first.id, second.id]
        ledger = TurnLedger()
        dispose(db, first.id, visible, ledger=ledger)
        result = dispose(db, second.id, visible, ledger=ledger)
        assert "ask" in result.summary.lower()

    def test_the_name_check_ignores_case_and_padding(self, db):
        first = add_item(db, name="Paneer", quantity=200, unit="g")
        second = add_item(db, name="  paneer ", quantity=500, unit="g")
        visible = [first.id, second.id]
        ledger = TurnLedger()
        dispose(db, first.id, visible, ledger=ledger)
        assert dispose(db, second.id, visible, ledger=ledger).ok is False

    def test_different_names_are_both_allowed(self, db):
        """"I used the milk and the bread" is an ordinary request."""
        milk = add_item(db, name="Milk", quantity=1, unit="l")
        bread = add_item(db, name="Bread", quantity=1, unit="count")
        visible = [milk.id, bread.id]
        ledger = TurnLedger()
        assert dispose(db, milk.id, visible, ledger=ledger).ok is True
        assert dispose(db, bread.id, visible, ledger=ledger).ok is True

    def test_the_same_item_can_be_recorded_twice(self, db):
        """"I used half and threw the rest away" is coherent."""
        item = add_item(db, name="Yogurt", quantity=400, unit="g")
        ledger = TurnLedger()
        assert dispose(db, item.id, [item.id], quantity=200, ledger=ledger).ok
        second = dispose(
            db, item.id, [item.id], outcome="wasted", quantity=200, ledger=ledger
        )
        assert second.ok is True
        db.refresh(item)
        assert item.quantity == 0

    def test_each_turn_starts_with_a_clean_ledger(self, db):
        """A refusal must not persist into the user's next message."""
        first = add_item(db, name="Paneer", quantity=200, unit="g")
        second = add_item(db, name="Paneer", quantity=500, unit="g")
        visible = [first.id, second.id]

        dispose(db, first.id, visible, ledger=TurnLedger())
        later_turn = dispose(db, second.id, visible, ledger=TurnLedger())
        assert later_turn.ok is True

    def test_the_wrong_choice_is_still_reversible(self, db):
        """The guard bounds the damage; undo is what repairs it."""
        from app.services.disposition import revert_disposition

        first = add_item(db, name="Paneer", quantity=200, unit="g")
        second = add_item(db, name="Paneer", quantity=500, unit="g")
        result = dispose(db, first.id, [first.id, second.id])

        event = db.get(Disposition, result.undo["disposition_id"])
        revert_disposition(db, event)
        db.commit()
        db.refresh(first)
        assert first.quantity == 200
        assert first.resolved_at is None


class TestAddItemTool:
    def test_an_item_is_created(self, db):
        result = add_via_tool(
            db, '{"name": "Paneer", "quantity": 200, "unit": "g"}'
        )
        assert result.ok is True
        item = db.query(InventoryItem).filter(InventoryItem.name == "Paneer").one()
        assert (item.quantity, item.unit) == (200, "g")
        assert item.user_id == db.info["user"].id

    def test_defaults_are_applied(self, db):
        result = add_via_tool(db, '{"name": "Bread"}')
        assert result.ok is True
        item = db.query(InventoryItem).filter(InventoryItem.name == "Bread").one()
        assert (item.quantity, item.unit) == (1.0, "count")

    def test_the_name_is_trimmed(self, db):
        add_via_tool(db, '{"name": "  Paneer  "}')
        assert db.query(InventoryItem).one().name == "Paneer"

    def test_a_blank_unit_falls_back_to_count(self, db):
        """A useless unit is corrected rather than made a reason to refuse."""
        add_via_tool(db, '{"name": "Bread", "unit": "   "}')
        assert db.query(InventoryItem).one().unit == "count"

    def test_a_non_string_unit_falls_back_to_count(self, db):
        add_via_tool(db, '{"name": "Bread", "unit": 5}')
        assert db.query(InventoryItem).one().unit == "count"

    def test_the_expiry_cascade_still_runs(self, db, monkeypatch):
        from app.services import inventory as inventory_service

        monkeypatch.setattr(
            inventory_service, "lookup_shelf_life_days", lambda name: (5, "dataset")
        )
        result = add_via_tool(db, '{"name": "Paneer"}')
        assert result.payload["expiration_date"] is not None
        assert result.payload["needs_expiry_date"] is False

    def test_adding_offers_no_undo(self, db):
        """Deleting a mistakenly added item is already a normal app action."""
        result = add_via_tool(db, '{"name": "Paneer"}')
        assert result.undo is None

    def test_an_unresolvable_expiry_is_reported_so_the_model_can_ask(self, db):
        """Otherwise the item silently never appears in a reminder."""
        result = add_via_tool(db, '{"name": "Mystery Jar"}')
        assert result.payload["needs_expiry_date"] is True


class TestAddItemRefusals:
    def test_a_missing_name_is_refused(self, db):
        result = add_via_tool(db, "{}")
        assert result.ok is False
        assert db.query(InventoryItem).count() == 0

    def test_a_blank_name_is_refused(self, db):
        result = add_via_tool(db, '{"name": "   "}')
        assert result.ok is False

    def test_a_non_string_name_is_refused(self, db):
        result = add_via_tool(db, '{"name": 7}')
        assert result.ok is False

    def test_a_string_quantity_is_refused(self, db):
        result = add_via_tool(db, '{"name": "Bread", "quantity": "two"}')
        assert result.ok is False
        assert db.query(InventoryItem).count() == 0

    def test_a_boolean_quantity_is_refused(self, db):
        result = add_via_tool(db, '{"name": "Bread", "quantity": true}')
        assert result.ok is False

    def test_zero_quantity_is_refused(self, db):
        result = add_via_tool(db, '{"name": "Bread", "quantity": 0}')
        assert result.ok is False

    def test_negative_quantity_is_refused(self, db):
        result = add_via_tool(db, '{"name": "Bread", "quantity": -3}')
        assert result.ok is False


class TestDispatch:
    def test_an_unknown_tool_is_refused(self, db):
        result = execute_tool(
            db, name="drop_database", raw_arguments="{}", visible_ids=frozenset()
        )
        assert result.ok is False
        assert "drop_database" in result.summary

    def test_malformed_json_is_refused(self, db):
        result = execute_tool(
            db, name="add_item", raw_arguments="{not json", visible_ids=frozenset()
        )
        assert result.ok is False

    def test_empty_arguments_are_treated_as_an_empty_object(self, db):
        result = execute_tool(
            db, name="add_item", raw_arguments="", visible_ids=frozenset()
        )
        assert result.ok is False
        assert "name is required" in result.summary

    def test_a_json_array_is_refused(self, db):
        """Valid JSON, wrong shape."""
        result = execute_tool(
            db, name="add_item", raw_arguments="[1, 2]", visible_ids=frozenset()
        )
        assert result.ok is False

    def test_a_failure_never_raises(self, db):
        """The model has to be able to explain the problem in its reply."""
        result = execute_tool(
            db,
            name="record_disposition",
            raw_arguments='{"item_id": "ghost", "outcome": "consumed"}',
            visible_ids=frozenset(),
        )
        assert result.ok is False
        assert result.payload["error"]


class TestStreamedToolCallReassembly:
    def test_fragments_are_concatenated(self):
        collected = {}
        _merge_tool_call_deltas(
            collected,
            [SimpleNamespace(index=0, id="c1", function=SimpleNamespace(
                name="add_item", arguments='{"name": '))],
        )
        _merge_tool_call_deltas(
            collected,
            [SimpleNamespace(index=0, id=None, function=SimpleNamespace(
                name=None, arguments='"Bread"}'))],
        )
        assert collected[0] == {
            "id": "c1",
            "name": "add_item",
            "arguments": '{"name": "Bread"}',
        }

    def test_parallel_calls_are_kept_apart_by_index(self):
        collected = {}
        _merge_tool_call_deltas(
            collected,
            [
                SimpleNamespace(index=0, id="c1", function=SimpleNamespace(
                    name="add_item", arguments="{}")),
                SimpleNamespace(index=1, id="c2", function=SimpleNamespace(
                    name="record_disposition", arguments="{}")),
            ],
        )
        assert collected[0]["name"] == "add_item"
        assert collected[1]["name"] == "record_disposition"

    def test_a_delta_without_a_function_is_skipped(self):
        collected = {}
        _merge_tool_call_deltas(collected, [SimpleNamespace(index=0, id="c1")])
        assert collected[0]["name"] == ""

    def test_a_name_only_delta_leaves_arguments_empty(self):
        collected = {}
        _merge_tool_call_deltas(
            collected,
            [SimpleNamespace(index=0, id="c1", function=SimpleNamespace(
                name="add_item", arguments=None))],
        )
        assert collected[0]["arguments"] == ""

    def test_a_missing_index_defaults_to_zero(self):
        collected = {}
        _merge_tool_call_deltas(
            collected,
            [SimpleNamespace(id="c1", function=SimpleNamespace(
                name="add_item", arguments="{}"))],
        )
        assert 0 in collected
