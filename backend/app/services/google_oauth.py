"""Google sign-in, as an identity source rather than a second session type.

The browser is sent to Google, comes back with an authorization code, and this
module exchanges that code for a verified email and a stable subject. The
application session is still our own cookie. Google never sees the fridge, and
we do not keep Google's access token after the exchange.

Unconfigured credentials disable the route rather than producing a half-working
button. Tests replace `fetch_google_identity` so a run never talks to Google.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import requests

from app.core.config import settings
from app.models.user import User
from app.services.auth import (
    AuthError,
    allocate_username,
    normalize_email,
    username_from_email,
)
from sqlalchemy.orm import Session as DbSession

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleOAuthError(AuthError):
    """The code was missing, expired, or Google would not vouch for the email."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str


def google_is_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def google_authorize_url(state: str) -> str:
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return f"{GOOGLE_AUTH_URL}?{query}"


def fetch_google_identity(code: str) -> GoogleIdentity:
    """Exchange an authorization code for a verified Google email.

    Replaced in tests. The live path is the only outbound call auth makes.
    """
    token_response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    if token_response.status_code != 200:
        raise GoogleOAuthError("Google sign-in failed")
    access_token = token_response.json().get("access_token")
    if not access_token:
        raise GoogleOAuthError("Google sign-in failed")

    info_response = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if info_response.status_code != 200:
        raise GoogleOAuthError("Google sign-in failed")
    info = info_response.json()
    subject = info.get("sub")
    email = info.get("email")
    if not subject or not email or not info.get("email_verified"):
        raise GoogleOAuthError("Google did not provide a verified email")
    return GoogleIdentity(subject=subject, email=normalize_email(email))


def user_from_google(db: DbSession, identity: GoogleIdentity) -> User:
    """Find or create the account this Google identity belongs to.

    Order matters. A returning Google user is found by subject. An existing
    password account with the same email is linked rather than duplicated, so
    signing in with Gmail later does not create a second empty kitchen.
    """
    existing = db.query(User).filter(User.google_id == identity.subject).one_or_none()
    if existing is not None:
        return existing

    by_email = db.query(User).filter(User.email == identity.email).one_or_none()
    if by_email is not None:
        if by_email.google_id and by_email.google_id != identity.subject:
            raise GoogleOAuthError("That email is already linked to another Google account")
        by_email.google_id = identity.subject
        db.add(by_email)
        db.commit()
        db.refresh(by_email)
        return by_email

    user = User(
        email=identity.email,
        username=allocate_username(db, username_from_email(identity.email)),
        password_hash=None,
        google_id=identity.subject,
        timezone="UTC",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
