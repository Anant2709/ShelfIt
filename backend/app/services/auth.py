"""Passwords, sessions, and the identities a person can sign in with.

A password is never stored. What is stored is a bcrypt hash, which cannot be
reversed into the password and is slow on purpose so guessing is expensive.

Google-only accounts have no hash. They cannot be reached through the password
form, and a guess at that email cannot tell them apart from a missing account.

A session is a random string kept in an httpOnly cookie and a row in the
database. Logout deletes the row. The cookie is useless without it, which is why
the session is not a signed blob that would stay valid after the user left.
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta

import bcrypt
from sqlalchemy.orm import Session as DbSession

from app.core.clock import utcnow
from app.core.config import settings
from app.models.user import Session, User

COOKIE_NAME = "shelfit_session"
OAUTH_STATE_COOKIE = "shelfit_oauth_state"

# Stable id so the migration and the seed script talk about the same person.
DEMO_USER_ID = "00000000-0000-4000-8000-000000000001"

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{2,31}$")


class AuthError(ValueError):
    """A client-correctable auth failure: bad credentials, taken email, etc."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    username = normalize_username(username)
    if not USERNAME_PATTERN.fullmatch(username):
        raise AuthError(
            "Username must be 3–32 characters, start with a letter or number, "
            "and use only letters, numbers, and underscores"
        )
    return username


def username_from_email(email: str) -> str:
    local = re.sub(r"[^a-z0-9_]", "", email.split("@")[0].lower())
    if not local or not local[0].isalnum():
        local = f"user{local}"
    if len(local) < 3:
        local = f"{local}user"[:8]
    return local[:32]


def allocate_username(db: DbSession, desired: str) -> str:
    """A free username derived from `desired`, adding a suffix if taken."""
    base = desired[:32]
    candidate = base
    suffix = 2
    while db.query(User).filter(User.username == candidate).one_or_none():
        extra = str(suffix)
        candidate = f"{base[: 32 - len(extra)]}{extra}"
        suffix += 1
    return candidate


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("utf-8")
        )
    except ValueError:
        return False


def create_user(
    db: DbSession,
    email: str,
    password: str,
    timezone: str = "UTC",
    user_id: str | None = None,
    username: str | None = None,
) -> User:
    email = normalize_email(email)
    if not email or "@" not in email:
        raise AuthError("A valid email is required")
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters")
    if db.query(User).filter(User.email == email).one_or_none() is not None:
        raise AuthError("An account with that email already exists")

    if username is None:
        username = allocate_username(db, username_from_email(email))
    else:
        username = validate_username(username)
        if db.query(User).filter(User.username == username).one_or_none() is not None:
            raise AuthError("That username is already taken")

    user = User(
        email=email,
        username=username,
        password_hash=hash_password(password),
        timezone=timezone,
    )
    if user_id is not None:
        user.id = user_id
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: DbSession, identifier: str, password: str) -> User | None:
    identifier = identifier.strip()
    if "@" in identifier:
        user = (
            db.query(User)
            .filter(User.email == normalize_email(identifier))
            .one_or_none()
        )
    else:
        user = (
            db.query(User)
            .filter(User.username == normalize_username(identifier))
            .one_or_none()
        )
    if user is None or not user.password_hash:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_session(db: DbSession, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        Session(
            id=token,
            user_id=user.id,
            expires_at=utcnow() + timedelta(days=settings.session_days),
        )
    )
    db.commit()
    return token


def user_for_token(db: DbSession, token: str | None) -> User | None:
    if not token:
        return None
    row = db.get(Session, token)
    if row is None or row.expires_at <= utcnow():
        return None
    return row.user


def revoke_session(db: DbSession, token: str | None) -> None:
    if not token:
        return
    row = db.get(Session, token)
    if row is None:
        return
    db.delete(row)
    db.commit()


def ensure_demo_user(db: DbSession) -> User:
    """The account the existing fridge is assigned to.

    Created once, with a known id, so the migration that labels old rows and the
    seed script that adds new ones agree on who owns the demo kitchen.
    """
    existing = db.get(User, DEMO_USER_ID)
    if existing is not None:
        # The published password is 7 characters and is created outside register.
        # If the hash ever drifts from settings, the demo kitchen would silently
        # refuse the password printed in the README.
        if not (
            existing.password_hash
            and verify_password(settings.demo_password, existing.password_hash)
        ):
            existing.password_hash = hash_password(settings.demo_password)
            db.commit()
            db.refresh(existing)
        return existing
    by_email = (
        db.query(User)
        .filter(User.email == normalize_email(settings.demo_email))
        .one_or_none()
    )
    if by_email is not None:
        return by_email
    # Built directly so the published demo password (`shelfit`) does not have to
    # satisfy the register-time length rule. That rule still applies to new
    # accounts; this one is created by the migration and the seed script.
    user = User(
        id=DEMO_USER_ID,
        email=normalize_email(settings.demo_email),
        username=settings.demo_username,
        password_hash=hash_password(settings.demo_password),
        timezone=settings.demo_timezone,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
