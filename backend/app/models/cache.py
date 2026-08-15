from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.models.base import Base


class CacheEntry(Base):
    """A persisted answer to an expensive lookup.

    Persisted rather than held in memory so that restarts -- which happen
    constantly under `uvicorn --reload` -- do not throw away paid API results.
    """

    __tablename__ = "cache_entries"

    # Composite primary key: one row per (namespace, key), so writing the same
    # key twice updates in place instead of accumulating duplicates.
    namespace: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
