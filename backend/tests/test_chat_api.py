"""The chat endpoints: conversations, multi-turn history, and the event stream.

The interesting property tested here is that inventory is rebuilt every turn and
never stored in the transcript. Without that, an assistant on turn four reasons
about the fridge as it was on turn one and suggests recipes using milk the user
has already drunk.
"""

import json

import pytest

from app.core import config
from app.services import chatbot
from chat_doubles import (
    FakeClient,
    connection_error,
    text_chunks,
    tool_call_chunks,
)


@pytest.fixture
def openai(monkeypatch):
    """Point the service at a scripted client, as the endpoint constructs its own."""

    def _install(*scripts):
        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")
        client = FakeClient(list(scripts))
        monkeypatch.setattr(chatbot, "OpenAI", lambda api_key=None: client)
        return client

    return _install


@pytest.fixture
def failing_openai(monkeypatch):
    """A client whose every request fails at the provider."""

    def _install():
        monkeypatch.setattr(config.settings, "openai_api_key", "test-key")
        stub = FakeClient([], error=connection_error())
        monkeypatch.setattr(chatbot, "OpenAI", lambda api_key=None: stub)
        return stub

    return _install


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.setattr(config.settings, "openai_api_key", None)


def add_item(client, name="Milk", quantity=1.0, unit="l"):
    return client.post(
        "/api/inventory/",
        json={"name": name, "quantity": quantity, "unit": unit},
    ).json()


def sse_events(response):
    """Parse an SSE body into the JSON payloads it carried."""
    return [
        json.loads(block[len("data: ") :])
        for block in response.text.split("\n\n")
        if block.startswith("data: ")
    ]


class TestChatEndpoint:
    def test_reply_is_returned(self, client, openai):
        openai(text_chunks("Make toast."))
        response = client.post("/api/chat/", json={"message": "ideas?"})
        assert response.status_code == 200
        assert response.json()["reply"] == "Make toast."

    def test_a_conversation_id_is_returned(self, client, openai):
        openai(text_chunks("hi"))
        body = client.post("/api/chat/", json={"message": "hello"}).json()
        assert body["conversation_id"]

    def test_prompt_is_grounded_in_current_inventory(self, client, openai):
        recorder = openai(text_chunks("ok"))
        add_item(client, name="Paneer", quantity=200, unit="g")
        client.post("/api/chat/", json={"message": "ideas?"})
        context = recorder.chat.completions.calls[0]["messages"][1]["content"]
        assert "Paneer" in context

    def test_resolved_items_are_not_offered(self, client, openai):
        """Something already eaten cannot be cooked with, or disposed of again."""
        recorder = openai(text_chunks("ok"))
        item = add_item(client, name="Paneer")
        client.post(
            f"/api/inventory/{item['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        client.post("/api/chat/", json={"message": "ideas?"})
        context = recorder.chat.completions.calls[0]["messages"][1]["content"]
        assert "Paneer" not in context

    def test_empty_message_is_rejected(self, client):
        assert client.post("/api/chat/", json={"message": ""}).status_code == 422

    def test_message_is_required(self, client):
        assert client.post("/api/chat/", json={}).status_code == 422

    def test_missing_key_is_a_503(self, client, no_key):
        response = client.post("/api/chat/", json={"message": "hi"})
        assert response.status_code == 503
        assert "OPENAI_API_KEY" in response.json()["detail"]

    def test_unknown_conversation_id_is_a_404(self, client, openai):
        openai(text_chunks("hi"))
        response = client.post(
            "/api/chat/", json={"message": "hi", "conversation_id": "nope"}
        )
        assert response.status_code == 404

    def test_actions_are_empty_when_nothing_changed(self, client, openai):
        openai(text_chunks("Just a suggestion."))
        body = client.post("/api/chat/", json={"message": "ideas?"}).json()
        assert body["actions"] == []


class TestMultiTurn:
    def test_second_turn_replays_the_first(self, client, openai):
        recorder = openai(text_chunks("Hello."), text_chunks("Toast."))
        first = client.post("/api/chat/", json={"message": "hi"}).json()
        client.post(
            "/api/chat/",
            json={"message": "ideas?", "conversation_id": first["conversation_id"]},
        )
        messages = recorder.chat.completions.calls[1]["messages"]
        contents = [message["content"] for message in messages]
        assert "hi" in contents
        assert "Hello." in contents
        assert contents[-1] == "ideas?"

    def test_the_conversation_id_is_stable(self, client, openai):
        openai(text_chunks("a"), text_chunks("b"))
        first = client.post("/api/chat/", json={"message": "hi"}).json()
        second = client.post(
            "/api/chat/",
            json={"message": "again", "conversation_id": first["conversation_id"]},
        ).json()
        assert second["conversation_id"] == first["conversation_id"]

    def test_omitting_the_id_starts_a_separate_thread(self, client, openai):
        recorder = openai(text_chunks("a"), text_chunks("b"))
        first = client.post("/api/chat/", json={"message": "hi"}).json()
        second = client.post("/api/chat/", json={"message": "hi again"}).json()
        assert second["conversation_id"] != first["conversation_id"]
        contents = [
            message["content"]
            for message in recorder.chat.completions.calls[1]["messages"]
        ]
        assert "a" not in contents

    def test_inventory_is_rebuilt_rather_than_replayed(self, client, openai):
        """A stale fridge in the history is worse than no history at all."""
        recorder = openai(text_chunks("ok"), text_chunks("ok"))
        item = add_item(client, name="Paneer")
        first = client.post("/api/chat/", json={"message": "ideas?"}).json()

        client.post(
            f"/api/inventory/{item['id']}/dispositions",
            json={"outcome": "consumed"},
        )
        client.post(
            "/api/chat/",
            json={"message": "ideas?", "conversation_id": first["conversation_id"]},
        )

        second = recorder.chat.completions.calls[1]["messages"]
        assert "Paneer" not in second[1]["content"]
        # And no earlier copy of the context survives in the replayed history.
        assert not any("Paneer" in (message["content"] or "") for message in second)

    def test_history_is_capped(self, client, openai, monkeypatch):
        """Prompt cost must not grow without bound as a thread gets longer."""
        monkeypatch.setattr(config.settings, "chat_history_messages", 2)
        recorder = openai(*[text_chunks("ok")] * 4)
        conversation_id = None
        for index in range(4):
            body = client.post(
                "/api/chat/",
                json={
                    "message": f"message-{index}",
                    "conversation_id": conversation_id,
                },
            ).json()
            conversation_id = body["conversation_id"]

        messages = recorder.chat.completions.calls[-1]["messages"]
        # Two system messages, two capped history messages, one new user message.
        assert len(messages) == 5
        assert "message-0" not in [message["content"] for message in messages]


class TestConversationRetrieval:
    def test_conversations_can_be_listed(self, client, openai):
        openai(text_chunks("Hello."), text_chunks("Later."))
        first = client.post("/api/chat/", json={"message": "what can I cook"}).json()
        second = client.post("/api/chat/", json={"message": "something else"}).json()
        listed = client.get("/api/chat/conversations").json()
        assert [row["id"] for row in listed] == [
            second["conversation_id"],
            first["conversation_id"],
        ]
        assert [row["title"] for row in listed] == [
            "something else",
            "what can I cook",
        ]
        assert all(row["message_count"] == 2 for row in listed)

    def test_empty_conversations_are_omitted(self, client, db, user):
        from app.models.conversation import Conversation

        db.add(Conversation(user_id=user.id))
        db.commit()
        assert client.get("/api/chat/conversations").json() == []

    def test_list_does_not_include_another_users_threads(
        self, client, db, openai
    ):
        from app.models.conversation import ChatMessage, Conversation
        from app.services.auth import create_user

        openai(text_chunks("mine"))
        client.post("/api/chat/", json={"message": "my thread"})
        other = create_user(db, email="other@local", password="password1")
        theirs = Conversation(user_id=other.id)
        db.add(theirs)
        db.flush()
        db.add(ChatMessage(conversation_id=theirs.id, role="user", content="secret"))
        db.commit()
        listed = client.get("/api/chat/conversations").json()
        assert [row["title"] for row in listed] == ["my thread"]

    def test_list_requires_a_session(self, anonymous_client):
        assert anonymous_client.get("/api/chat/conversations").status_code == 401

    def test_title_falls_back_without_user_text(self, db, user):
        from app.models.conversation import ChatMessage, Conversation
        from app.services.conversation import title_for

        conversation = Conversation(user_id=user.id)
        db.add(conversation)
        db.flush()
        db.add(
            ChatMessage(
                conversation_id=conversation.id, role="assistant", content="hi"
            )
        )
        db.commit()
        db.refresh(conversation)
        assert title_for(conversation) == "Conversation"

    def test_a_long_first_message_is_truncated(self, db, user):
        from app.models.conversation import ChatMessage, Conversation
        from app.services.conversation import title_for

        conversation = Conversation(user_id=user.id)
        db.add(conversation)
        db.flush()
        db.add(
            ChatMessage(
                conversation_id=conversation.id,
                role="user",
                content="x" * 90,
            )
        )
        db.commit()
        db.refresh(conversation)
        title = title_for(conversation)
        assert title.endswith("…")
        assert len(title) == 80

    def test_transcript_can_be_read_back(self, client, openai):
        openai(text_chunks("Hello."))
        body = client.post("/api/chat/", json={"message": "hi"}).json()
        response = client.get(f"/api/chat/conversations/{body['conversation_id']}")
        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [(m["role"], m["content"]) for m in messages] == [
            ("user", "hi"),
            ("assistant", "Hello."),
        ]

    def test_unknown_conversation_is_a_404(self, client):
        assert client.get("/api/chat/conversations/nope").status_code == 404

    def test_conversation_can_be_deleted(self, client, openai):
        openai(text_chunks("hi"))
        body = client.post("/api/chat/", json={"message": "hi"}).json()
        conversation_id = body["conversation_id"]
        assert (
            client.delete(f"/api/chat/conversations/{conversation_id}").status_code
            == 200
        )
        assert client.get(f"/api/chat/conversations/{conversation_id}").status_code == (
            404
        )

    def test_deleting_an_unknown_conversation_is_a_404(self, client):
        assert client.delete("/api/chat/conversations/nope").status_code == 404

    def test_deleting_a_conversation_leaves_no_orphan_messages(self, client, openai):
        from app.models.conversation import ChatMessage
        from app.db.session import SessionLocal

        openai(text_chunks("hi"))
        body = client.post("/api/chat/", json={"message": "hi"}).json()
        client.delete(f"/api/chat/conversations/{body['conversation_id']}")
        session = SessionLocal()
        try:
            assert session.query(ChatMessage).count() == 0
        finally:
            session.close()


class TestStreamEndpoint:
    def test_tokens_are_streamed_as_events(self, client, openai):
        openai(text_chunks("Make ", "toast."))
        response = client.post("/api/chat/stream", json={"message": "ideas?"})
        assert response.status_code == 200
        events = sse_events(response)
        tokens = [e["text"] for e in events if e["type"] == "token"]
        assert tokens == ["Make ", "toast."]

    def test_content_type_is_event_stream(self, client, openai):
        openai(text_chunks("hi"))
        response = client.post("/api/chat/stream", json={"message": "hi"})
        assert response.headers["content-type"].startswith("text/event-stream")

    def test_done_event_carries_reply_and_conversation_id(self, client, openai):
        openai(text_chunks("Make ", "toast."))
        response = client.post("/api/chat/stream", json={"message": "ideas?"})
        done = sse_events(response)[-1]
        assert done["type"] == "done"
        assert done["reply"] == "Make toast."
        assert done["conversation_id"]

    def test_streamed_turn_is_persisted(self, client, openai):
        openai(text_chunks("Hello."))
        response = client.post("/api/chat/stream", json={"message": "hi"})
        conversation_id = sse_events(response)[-1]["conversation_id"]
        transcript = client.get(f"/api/chat/conversations/{conversation_id}").json()
        assert [m["content"] for m in transcript["messages"]] == ["hi", "Hello."]

    def test_streaming_continues_a_conversation(self, client, openai):
        recorder = openai(text_chunks("a"), text_chunks("b"))
        first = sse_events(
            client.post("/api/chat/stream", json={"message": "hi"})
        )[-1]
        client.post(
            "/api/chat/stream",
            json={"message": "again", "conversation_id": first["conversation_id"]},
        )
        contents = [
            message["content"]
            for message in recorder.chat.completions.calls[1]["messages"]
        ]
        assert "hi" in contents and "a" in contents

    def test_tool_calls_surface_as_action_events(self, client, openai):
        item = add_item(client, name="Milk", quantity=1, unit="l")
        openai(
            tool_call_chunks(
                "record_disposition",
                f'{{"item_id": "{item["id"]}", "outcome": "consumed"}}',
            ),
            text_chunks("Marked it used."),
        )
        response = client.post(
            "/api/chat/stream", json={"message": "I finished the milk"}
        )
        actions = [e for e in sse_events(response) if e["type"] == "action"]
        assert len(actions) == 1
        assert actions[0]["ok"] is True
        assert "Milk" in actions[0]["summary"]
        assert actions[0]["undo"]["disposition_id"]

    def test_missing_key_is_a_503_not_a_stream_of_errors(self, client, no_key):
        """The status code has to be decided before the body starts."""
        response = client.post("/api/chat/stream", json={"message": "hi"})
        assert response.status_code == 503

    def test_unknown_conversation_id_is_a_404(self, client, openai):
        openai(text_chunks("hi"))
        response = client.post(
            "/api/chat/stream", json={"message": "hi", "conversation_id": "nope"}
        )
        assert response.status_code == 404

    def test_mid_stream_failure_is_reported_in_band(self, client, failing_openai):
        """The response already began, so this cannot become a 503."""
        failing_openai()
        response = client.post("/api/chat/stream", json={"message": "hi"})
        assert response.status_code == 200
        assert sse_events(response)[-1]["type"] == "error"

    def test_a_failed_turn_is_not_persisted(self, client, failing_openai):
        """Better no record than a question with no answer beneath it."""
        from app.models.conversation import ChatMessage
        from app.db.session import SessionLocal

        failing_openai()
        client.post("/api/chat/stream", json={"message": "hi"})

        session = SessionLocal()
        try:
            assert session.query(ChatMessage).count() == 0
        finally:
            session.close()


class TestAssistantChangesAreVisible:
    def test_buffered_endpoint_reports_what_it_changed(self, client, openai):
        item = add_item(client, name="Milk", quantity=1, unit="l")
        openai(
            tool_call_chunks(
                "record_disposition",
                f'{{"item_id": "{item["id"]}", "outcome": "wasted"}}',
            ),
            text_chunks("Binned it."),
        )
        body = client.post(
            "/api/chat/", json={"message": "the milk went off, threw it out"}
        ).json()
        assert len(body["actions"]) == 1
        assert body["actions"][0]["name"] == "record_disposition"
        assert body["actions"][0]["ok"] is True

    def test_an_assistant_action_can_be_undone_by_the_user(self, client, openai):
        """The safety net under letting a model write: anything it does, undo."""
        item = add_item(client, name="Milk", quantity=1, unit="l")
        openai(
            tool_call_chunks(
                "record_disposition",
                f'{{"item_id": "{item["id"]}", "outcome": "consumed"}}',
            ),
            text_chunks("Marked it used."),
        )
        body = client.post("/api/chat/", json={"message": "drank the milk"}).json()
        undo = body["actions"][0]["undo"]

        response = client.delete(
            f"/api/inventory/{undo['item_id']}/dispositions/"
            f"{undo['disposition_id']}"
        )
        assert response.status_code == 200
        assert response.json()["quantity"] == 1
        names = [entry["name"] for entry in client.get("/api/inventory/").json()]
        assert "Milk" in names

    def test_the_change_shows_up_in_the_waste_report(self, client, openai):
        """An assistant-recorded waste event is real data, not a chat artefact."""
        item = add_item(client, name="Milk", quantity=1, unit="l")
        openai(
            tool_call_chunks(
                "record_disposition",
                f'{{"item_id": "{item["id"]}", "outcome": "wasted"}}',
            ),
            text_chunks("Binned it."),
        )
        client.post("/api/chat/", json={"message": "threw out the milk"})
        report = client.get("/api/analytics/waste").json()
        assert report["wasted"]["events"] == 1

    def test_item_added_by_the_assistant_appears_in_the_inventory(
        self, client, openai
    ):
        openai(
            tool_call_chunks(
                "add_item", '{"name": "Paneer", "quantity": 200, "unit": "g"}'
            ),
            text_chunks("Added it."),
        )
        client.post("/api/chat/", json={"message": "I bought 200g of paneer"})
        names = [item["name"] for item in client.get("/api/inventory/").json()]
        assert "Paneer" in names
