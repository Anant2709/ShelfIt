from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
import uuid

from app.models.base import Base

class Item(Base):
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(
        primary_key=True, default=lambda: str(uuid.uuid4())
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    user = relationship("User", backref="items")

    name: Mapped[str] = mapped_column(String(120))
    image_url: Mapped[str | None] = mapped_column(String(500))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    expiry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))