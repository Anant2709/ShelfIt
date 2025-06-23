from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
import uuid
from app.models.base import Base

class User(Base):
    __tablename__ = "users"

    # primary key – UUID string
    id: Mapped[str] = mapped_column(
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # unique email for login
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    # bcrypt-hashed password
    password_hash: Mapped[str] = mapped_column(String(255))

    # timestamp of creation
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )