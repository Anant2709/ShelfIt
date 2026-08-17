import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.models.base import Base


class User(Base):
    """Someone who owns a kitchen.

    Until this table existed every request read one global fridge. A user is what
    makes "my milk" distinguishable from someone else's, and what "today" means
    for expiry math -- UTC is consistent, but it is the wrong calendar for anyone
    not living in UTC.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    # Null when the account was created through Google and has never set a password.
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    # Google's stable subject. Null until they sign in with Gmail.
    google_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    # IANA name such as "America/New_York". Invalid values are rejected before
    # they reach this column, so `clock.today` can trust what it is given.
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None


class Session(Base):
    """A login, stored on the server.

    The browser only holds an opaque cookie. Revoking a row here is what logout
    is; a signed cookie that carried the user id would stay valid until it expired
    even after the user asked to leave.
    """

    __tablename__ = "sessions"

    # The value in the cookie. Random, unguessable, and the only secret here.
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="sessions")
