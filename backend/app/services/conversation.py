"""Conversation persistence.

The transcript is kept server-side and identified by an opaque id. The client
sends only the id and the new message, never the prior turns, because a
client-supplied transcript is editable -- and an assistant that can change the
inventory must not be steerable by a forged "you already agreed to this" turn.

History is also capped. Prompt cost grows with every turn, and a conversation left
open for an afternoon would otherwise quietly get more expensive per message.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.models.conversation import ChatMessage, Conversation


class ConversationNotFoundError(LookupError):
    """The supplied conversation id does not exist."""


def get_or_create(
    db: Session, conversation_id: str | None, user_id: str
) -> Conversation:
    """Fetch a conversation, or start one when no id was given.

    An id that does not exist is an error rather than an invitation to create one
    under that id: accepting client-chosen ids would let one client read another's
    thread by guessing. An id that exists but belongs to someone else is the same
    error, so a guess cannot tell the two apart.
    """
    if conversation_id is None:
        conversation = Conversation(user_id=user_id)
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return conversation

    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise ConversationNotFoundError(conversation_id)
    return conversation


def load_history(db: Session, conversation: Conversation) -> list[dict[str, str]]:
    """The most recent turns, oldest first, capped by configuration."""
    recent = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(settings.chat_history_messages)
        .all()
    )
    return [
        {"role": message.role, "content": message.content}
        for message in reversed(recent)
    ]


def list_for_user(db: Session, user_id: str) -> list[Conversation]:
    """This user's threads that actually have messages, newest first.

    Empty rows are leftover from a turn that started and never finished, or from
    earlier testing. They are not conversations a person would recognise.
    """
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .filter(Conversation.messages.any())
        .order_by(Conversation.updated_at.desc())
        .all()
    )


def title_for(conversation: Conversation) -> str:
    """The first thing the user said, which is what they will recognise."""
    for message in conversation.messages:
        if message.role == "user" and message.content.strip():
            text = message.content.strip()
            return text if len(text) <= 80 else f"{text[:79]}…"
    return "Conversation"


def append_turn(
    db: Session,
    conversation: Conversation,
    user_message: str,
    assistant_reply: str,
) -> None:
    """Store one exchange.

    Written after the reply is known, so a turn that failed part-way through does
    not leave a question in the transcript with no answer.
    """
    db.add(
        ChatMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_message,
        )
    )
    db.add(
        ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=assistant_reply,
        )
    )
    conversation.updated_at = utcnow()
    db.add(conversation)
    db.commit()
