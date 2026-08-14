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


class InventoryScanResponse(BaseModel):
    status: Literal["created", "needs_label"]
    item: InventoryItemOut | None = None
    image_id: str | None = None
    suggested_label: str | None = None
    confidence: float | None = None


class InventoryLabelRequest(BaseModel):
    image_id: str
    label: str
    quantity: float = Field(default=1.0, ge=0.01)
    unit: str = Field(default="count")
    expiration_date: date | None = None
