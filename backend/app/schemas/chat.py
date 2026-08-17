from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    # Omit to start a new conversation; the response returns the new id.
    conversation_id: str | None = None


class UndoRef(BaseModel):
    """What the interface needs to reverse an action the assistant took."""

    item_id: str
    disposition_id: str


class ChatAction(BaseModel):
    """Something the assistant did to the inventory during a turn.

    Returned so the interface can show the user what was changed on their behalf
    rather than leaving them to notice it later. `ok` is false for a refused or
    failed call, which the assistant is expected to explain in its reply.

    `undo` is present where the change is reversible, which is how a plausible but
    wrong action stays correctable by the person it was done to.
    """

    name: str
    ok: bool
    summary: str
    undo: UndoRef | None = None


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    actions: list[ChatAction] = Field(default_factory=list)


class ChatMessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: datetime
    message_count: int


class ConversationOut(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    messages: list[ChatMessageOut] = Field(default_factory=list)

    class Config:
        from_attributes = True
