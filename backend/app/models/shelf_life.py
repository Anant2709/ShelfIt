from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.models.base import Base


class LearnedShelfLife(Base):
    """A shelf life the system worked out, kept separate from curated data.

    Deliberately not written back into `data/shelf_life.json`. That file is
    human-authored and version-controlled, so mixing machine-derived values into
    it would destroy the ability to tell the two apart -- and every learned value
    would then be served with the authority of a curated one. Learned entries
    live here instead, are reported with their own provenance, and are promoted
    into the curated file only by a human running the review script.
    """

    __tablename__ = "learned_shelf_life"

    # Normalised item name, so lookups are exact and case-insensitive.
    name: Mapped[str] = mapped_column(String, primary_key=True)
    days: Mapped[int] = mapped_column(Integer, nullable=False)

    # The known item this answer was derived from, if any. Recording it turns a
    # bare number into a checkable claim: "baby spinach was treated as spinach"
    # is reviewable, whereas "baby spinach is 4" is not.
    anchor: Mapped[str | None] = mapped_column(String, nullable=True)
    # The anchor's value at the time of derivation. If the curated value later
    # changes, a mismatch identifies this entry as stale.
    anchor_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    model: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Set when a human has reviewed the entry and accepted it.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
