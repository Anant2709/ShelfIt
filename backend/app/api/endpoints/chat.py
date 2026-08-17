import json
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core import clock
from app.core.config import settings
from app.db.deps import get_db
from app.models.conversation import Conversation
from app.models.inventory import InventoryItem
from app.models.user import User
from app.schemas.chat import (
    ChatAction,
    ChatRequest,
    ChatResponse,
    ConversationOut,
    ConversationSummary,
)
from app.services import conversation as conversation_service
from app.services.chatbot import (
    ChatUnavailableError,
    DoneEvent,
    TokenEvent,
    ToolEvent,
    generate_chat_reply,
    stream_chat_turn,
    to_snapshot,
)

router = APIRouter()


def _current_inventory(db: Session, user: User) -> list[dict]:
    """What is actually on this user's shelf right now.

    Resolved items are excluded: something already eaten is not available to cook
    with, and offering it would also let the assistant try to dispose of it again.
    """
    items = (
        db.query(InventoryItem)
        .filter(
            InventoryItem.resolved_at.is_(None),
            InventoryItem.user_id == user.id,
        )
        .all()
    )
    return to_snapshot(items)


def _load_conversation(db: Session, conversation_id: str | None, user: User):
    try:
        return conversation_service.get_or_create(db, conversation_id, user.id)
    except conversation_service.ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc


@router.post("/", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = _load_conversation(db, payload.conversation_id, user)
    history = conversation_service.load_history(db, conversation)

    try:
        turn = generate_chat_reply(
            db,
            payload.message,
            history=history,
            inventory=_current_inventory(db, user),
            user_id=user.id,
            today=clock.today(user.timezone),
        )
    except ChatUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    conversation_service.append_turn(db, conversation, payload.message, turn.reply)
    return ChatResponse(
        reply=turn.reply,
        conversation_id=conversation.id,
        actions=[
            ChatAction(
                name=result.name,
                ok=result.ok,
                summary=result.summary,
                undo=result.undo,
            )
            for result in turn.tool_results
        ],
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/stream")
def chat_stream(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The same turn, streamed as server-sent events.

    Event types are `token`, `action`, `done`, and `error`. The status code has to
    be chosen before the body starts, so a missing key is rejected up front rather
    than surfacing as a 200 whose first event is an error.
    """
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "The assistant is not configured. Set OPENAI_API_KEY to enable chat."
            ),
        )

    conversation = _load_conversation(db, payload.conversation_id, user)
    history = conversation_service.load_history(db, conversation)
    inventory = _current_inventory(db, user)

    def events() -> Iterator[str]:
        reply = ""
        try:
            for event in stream_chat_turn(
                db,
                payload.message,
                history=history,
                inventory=inventory,
                user_id=user.id,
                today=clock.today(user.timezone),
            ):
                if isinstance(event, TokenEvent):
                    yield _sse({"type": "token", "text": event.text})
                elif isinstance(event, ToolEvent):
                    yield _sse(
                        {
                            "type": "action",
                            "name": event.result.name,
                            "ok": event.result.ok,
                            "summary": event.result.summary,
                            "undo": event.result.undo,
                        }
                    )
                else:
                    reply = event.reply
        except ChatUnavailableError as exc:
            # The response already began, so this cannot become a 503. It is
            # reported in-band and the turn is not stored, leaving nothing
            # half-written in the transcript.
            yield _sse({"type": "error", "detail": str(exc)})
            return

        conversation_service.append_turn(db, conversation, payload.message, reply)
        yield _sse(
            {"type": "done", "reply": reply, "conversation_id": conversation.id}
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every thread this user has actually spoken in, newest first."""
    conversations = conversation_service.list_for_user(db, user.id)
    return [
        ConversationSummary(
            id=conversation.id,
            title=conversation_service.title_for(conversation),
            updated_at=conversation.updated_at,
            message_count=len(conversation.messages),
        )
        for conversation in conversations
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conversation)
    db.commit()
    return {"status": "deleted"}
