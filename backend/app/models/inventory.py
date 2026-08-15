import uuid
from datetime import datetime, date

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.models.base import Base


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=True)
    image_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str] = mapped_column(String, default="count")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    expiration: Mapped["Expiration"] = relationship(
        "Expiration",
        back_populates="item",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Expiration(Base):
    __tablename__ = "expirations"

    item_id: Mapped[str] = mapped_column(String, ForeignKey("inventory_items.id"), primary_key=True)
    expiration_date: Mapped[date] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="default")
    shelf_life_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    item: Mapped[InventoryItem] = relationship("InventoryItem", back_populates="expiration")
