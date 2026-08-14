from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models.inventory import InventoryItem
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chatbot import build_inventory_context, generate_chat_reply

router = APIRouter()


@router.post("/", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    items = db.query(InventoryItem).all()
    inventory_snapshot = []
    for item in items:
        inventory_snapshot.append(
            {
                "name": item.name,
                "quantity": item.quantity,
                "unit": item.unit,
                "expiration_date": item.expiration.expiration_date
                if item.expiration
                else None,
            }
        )
    context = build_inventory_context(inventory_snapshot)
    reply = generate_chat_reply(payload.message, context)
    return ChatResponse(reply=reply)
