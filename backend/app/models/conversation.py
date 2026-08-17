import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.models.base import Base


class Conversation(Base):
    """One ongoing chat thread.

    Held server-side rather than reassembled from whatever the client sends back.
    A client-supplied transcript is untrusted input: it can be edited to put words
    in the assistant's mouth, and since the assistant can call tools that change
    the inventory, a forged prior turn is a way to talk it into acting.
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", name="fk_conversations_user_id"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    """One durable turn in a conversation.

    Only what a person said and what the assistant finally replied is stored. The
    tool calls in between are deliberately not persisted: their effect is already
    recorded in the inventory itself, which is rebuilt into the prompt on every
    turn. Replaying stale tool traffic would tell the model about a fridge that no
    longer exists, and the domain tables are the honest record of what happened.
    """

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversations.id"), nullable=False, index=True
    )
    # "user" or "assistant". System messages are not stored because they are
    # regenerated per turn from current state.
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    conversation: Mapped[Conversation] = relationship(
        "Conversation", back_populates="messages"
    )
