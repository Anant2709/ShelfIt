from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.models.base import Base


class LearnedCategory(Base):
    """A name-to-category mapping the system worked out for itself.

    Same separation as `LearnedShelfLife`: machine-derived values stay out of the
    human-authored curated file so the two can always be told apart, and a human
    promotes entries rather than the app writing to its own source of truth.

    Unlike shelf life there is no anchor column. An anchor existed there to stop
    unbounded numbers from fragmenting -- two spellings of one vegetable getting
    different day counts. A category is drawn from a closed set, so that failure
    cannot happen and the anchor would record nothing worth reviewing.
    """

    __tablename__ = "learned_categories"

    # Normalised item name, so lookups are exact and case-insensitive.
    name: Mapped[str] = mapped_column(String, primary_key=True)
    # Always a `Category` value; anything off the closed set is rejected before
    # it reaches this table.
    category: Mapped[str] = mapped_column(String, nullable=False)

    model: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Set when a human has reviewed the entry and accepted it.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
