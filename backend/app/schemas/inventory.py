from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field


class ExpirationBase(BaseModel):
    expiration_date: date | None = None
    source: str | None = None
    shelf_life_days: int | None = None


class ExpirationCreate(BaseModel):
    expiration_date: date | None = None


class ExpirationOut(ExpirationBase):
    class Config:
        from_attributes = True


class InventoryItemBase(BaseModel):
    name: str
    category: str | None = None
    quantity: float = Field(default=1.0, ge=0.01)
    unit: str = Field(default="count")


class InventoryItemCreate(InventoryItemBase):
    expiration_date: date | None = None


class InventoryItemUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    quantity: float | None = Field(default=None, ge=0.01)
    unit: str | None = None


class InventoryItemOut(InventoryItemBase):
    id: str
    image_uri: str | None = None
    confidence: float | None = None
    created_at: datetime
    expiration: ExpirationOut | None = None

    class Config:
        from_attributes = True


class ScanCandidate(BaseModel):
    """A detection the model was not confident enough to add automatically."""

    label: str
    confidence: float
    box: list[float] | None = None


class InventoryScanResponse(BaseModel):
    """The outcome of one scan, which may cover several items.

    `status` summarises the scan for callers that only act on one outcome:
      created     at least one item was added automatically
      needs_label nothing was confident enough; the user must name it
      empty       nothing recognisable was found

    `item`, `suggested_label`, and `confidence` describe the first created item
    and the first candidate respectively. They are a convenience view over
    `created_items` and `candidates`, which are the authoritative lists.
    """

    status: Literal["created", "needs_label", "empty"]
    image_id: str | None = None
    created_items: list[InventoryItemOut] = Field(default_factory=list)
    candidates: list[ScanCandidate] = Field(default_factory=list)
    item: InventoryItemOut | None = None
    suggested_label: str | None = None
    confidence: float | None = None


class InventoryLabelRequest(BaseModel):
    image_id: str
    label: str
    quantity: float = Field(default=1.0, ge=0.01)
    unit: str = Field(default="count")
    expiration_date: date | None = None
