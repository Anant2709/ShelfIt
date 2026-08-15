"""Tests for the inventory-grounded chatbot."""

from datetime import date, timedelta

import pytest

from app.services import chatbot
from app.services.chatbot import (
    ChatUnavailableError,
    build_inventory_context,
    generate_chat_reply,
)


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeCompletion:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, reply="Try a paneer curry.", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return FakeCompletion(self.reply)


class FakeOpenAI:
    """Stands in for the OpenAI SDK client."""

    last_instance = None

    def __init__(self, api_key=None, reply="Try a paneer curry.", error=None):
        self.api_key = api_key
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(reply=reply, error=error)
        FakeOpenAI.last_instance = self


@pytest.fixture
def fake_openai(monkeypatch):
    def _install(reply="Try a paneer curry.", error=None):
        monkeypatch.setattr(chatbot.settings, "openai_api_key", "test-key")

        def _factory(api_key=None):
            return FakeOpenAI(api_key=api_key, reply=reply, error=error)

        monkeypatch.setattr(chatbot, "OpenAI", _factory)
        return FakeOpenAI

    return _install


class TestInventoryContext:
    def test_empty_inventory_is_stated_explicitly(self):
        assert build_inventory_context([]) == "Inventory is empty."

    def test_countable_items_use_multiplier_notation(self):
        context = build_inventory_context(
            [{"name": "eggs", "quantity": 12, "unit": "count"}]
        )
        assert context == "- eggs x12"

    def test_measured_items_include_the_unit(self):
        context = build_inventory_context(
            [{"name": "milk", "quantity": 1.5, "unit": "l"}]
        )
        assert context == "- milk 1.5 l"

    def test_missing_unit_defaults_to_count(self):
        context = build_inventory_context([{"name": "apples", "quantity": 3}])
        assert context == "- apples x3"

    def test_expiration_is_appended_when_known(self):
        context = build_inventory_context(
            [
                {
                    "name": "milk",
                    "quantity": 1,
                    "unit": "l",
                    "expiration_date": "2026-09-01",
                }
            ]
        )
        assert context == "- milk 1 l (expires 2026-09-01)"

    def test_absent_expiration_is_omitted_rather_than_shown_as_none(self):
        context = build_inventory_context(
            [{"name": "salt", "quantity": 1, "unit": "kg", "expiration_date": None}]
        )
        assert "None" not in context
        assert context == "- salt 1 kg"

    def test_each_item_is_on_its_own_line(self):
        context = build_inventory_context(
            [
                {"name": "milk", "quantity": 1, "unit": "l"},
                {"name": "eggs", "quantity": 6, "unit": "count"},
            ]
        )
        assert context.splitlines() == ["- milk 1 l", "- eggs x6"]


class TestReplyGeneration:
    def test_reply_is_returned_and_stripped(self, fake_openai):
        fake_openai(reply="  Make a sandwich.  ")
        assert generate_chat_reply("what can I cook?", "- bread x1") == (
            "Make a sandwich."
        )

    def test_inventory_is_sent_as_a_separate_system_message(self, fake_openai):
        """Keeping context out of the user turn limits prompt-injection surface."""
        fake = fake_openai()
        generate_chat_reply("what can I cook?", "- bread x1")
        messages = fake.last_instance.chat.completions.calls[0]["messages"]
        roles = [message["role"] for message in messages]
        assert roles == ["system", "system", "user"]
        assert "- bread x1" in messages[1]["content"]
        assert messages[2]["content"] == "what can I cook?"

    def test_configured_model_is_used(self, fake_openai, monkeypatch):
        fake = fake_openai()
        monkeypatch.setattr(chatbot.settings, "openai_model", "gpt-4o")
        generate_chat_reply("hi", "- bread x1")
        assert fake.last_instance.chat.completions.calls[0]["model"] == "gpt-4o"

    def test_missing_key_raises_unavailable_rather_than_returning_prose(
        self, monkeypatch
    ):
        """A configuration failure is an error, not a chat answer."""
        monkeypatch.setattr(chatbot.settings, "openai_api_key", None)
        with pytest.raises(ChatUnavailableError):
            generate_chat_reply("hi", "- bread x1")


class TestUpstreamFailures:
    def test_authentication_error_is_translated(self, fake_openai):
        import httpx
        from openai import AuthenticationError

        error = AuthenticationError(
            message="Incorrect API key provided",
            response=httpx.Response(
                status_code=401, request=httpx.Request("POST", "https://api.openai.com")
            ),
            body=None,
        )
        fake_openai(error=error)
        with pytest.raises(ChatUnavailableError):
            generate_chat_reply("hi", "- bread x1")

    def test_connection_error_is_translated(self, fake_openai):
        import httpx
        from openai import APIConnectionError

        error = APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com")
        )
        fake_openai(error=error)
        with pytest.raises(ChatUnavailableError):
            generate_chat_reply("hi", "- bread x1")


class TestChatEndpoint:
    def test_endpoint_returns_the_reply(self, client, fake_openai):
        fake_openai(reply="Make toast.")
        response = client.post("/api/chat/", json={"message": "ideas?"})
        assert response.status_code == 200
        assert response.json() == {"reply": "Make toast."}

    def test_endpoint_grounds_the_prompt_in_current_inventory(
        self, client, db, fake_openai
    ):
        fake = fake_openai()
        client.post(
            "/api/inventory/",
            json={
                "name": "Paneer",
                "quantity": 200,
                "unit": "g",
                "expiration_date": str(date.today() + timedelta(days=2)),
            },
        )
        client.post("/api/chat/", json={"message": "ideas?"})
        messages = fake.last_instance.chat.completions.calls[0]["messages"]
        assert "Paneer 200.0 g" in messages[1]["content"]

    def test_empty_inventory_still_produces_a_grounded_prompt(
        self, client, fake_openai
    ):
        fake = fake_openai()
        client.post("/api/chat/", json={"message": "ideas?"})
        messages = fake.last_instance.chat.completions.calls[0]["messages"]
        assert "Inventory is empty." in messages[1]["content"]

    def test_unconfigured_assistant_returns_503_not_500(self, client, monkeypatch):
        """An expired or missing key must not surface as an unhandled crash."""
        monkeypatch.setattr(chatbot.settings, "openai_api_key", None)
        response = client.post("/api/chat/", json={"message": "ideas?"})
        assert response.status_code == 503
        assert "detail" in response.json()

    def test_upstream_auth_failure_returns_503_not_500(self, client, fake_openai):
        import httpx
        from openai import AuthenticationError

        fake_openai(
            error=AuthenticationError(
                message="Incorrect API key provided",
                response=httpx.Response(
                    status_code=401,
                    request=httpx.Request("POST", "https://api.openai.com"),
                ),
                body=None,
            )
        )
        response = client.post("/api/chat/", json={"message": "ideas?"})
        assert response.status_code == 503

    def test_message_is_required(self, client):
        assert client.post("/api/chat/", json={}).status_code == 422
