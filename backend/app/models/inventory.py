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
    # NULL means no category could be established. `category_source` still says
    # what was tried, so "asked and could not tell" is distinguishable from
    # "never asked".
    category: Mapped[str] = mapped_column(String, nullable=True, index=True)
    category_source: Mapped[str | None] = mapped_column(String, nullable=True)
    image_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str] = mapped_column(String, default="count")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Set when remaining quantity hits zero after consume/waste. Distinct from
    # DELETE, which erases the row: a resolved item is history, not a mistake.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    expiration: Mapped["Expiration"] = relationship(
        "Expiration",
        back_populates="item",
        cascade="all, delete-orphan",
        uselist=False,
    )
    dispositions: Mapped[list["Disposition"]] = relationship(
        "Disposition",
        back_populates="item",
        cascade="all, delete-orphan",
    )

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None


class Expiration(Base):
    __tablename__ = "expirations"

    item_id: Mapped[str] = mapped_column(String, ForeignKey("inventory_items.id"), primary_key=True)
    expiration_date: Mapped[date] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="default")
    shelf_life_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    item: Mapped[InventoryItem] = relationship("InventoryItem", back_populates="expiration")


class Disposition(Base):
    """One consume or waste event against an item.

    Inventory quantity is "what is on the shelf now". This table is "what
    happened to it". Analytics read this, not the live fridge, so throwing
    something out is a recorded outcome rather than a vanished row.
    """

    __tablename__ = "dispositions"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    item_id: Mapped[str] = mapped_column(
        String, ForeignKey("inventory_items.id"), nullable=False, index=True
    )
    # "consumed" or "wasted". Constrained in the service layer, not as an enum
    # column, so SQLite and Postgres stay interchangeable without a native enum.
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Snapshots so later edits to the item cannot rewrite history.
    item_name: Mapped[str] = mapped_column(String, nullable=False)
    item_category: Mapped[str | None] = mapped_column(String, nullable=True)
    days_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    item: Mapped[InventoryItem] = relationship(
        "InventoryItem", back_populates="dispositions"
    )
