from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.deps import get_db
from app.models.user import User
from app.schemas.auth import AuthProvidersOut, LoginRequest, RegisterRequest, UserOut
from app.services.auth import (
    COOKIE_NAME,
    OAUTH_STATE_COOKIE,
    AuthError,
    authenticate,
    create_session,
    create_user,
    ensure_demo_user,
    revoke_session,
)
from app.services.google_oauth import (
    GoogleOAuthError,
    fetch_google_identity,
    google_authorize_url,
    google_is_configured,
    user_from_google,
)
import secrets

router = APIRouter()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_days * 24 * 60 * 60,
        path="/",
    )


def _validated_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Unknown timezone") from exc
    return name


def _frontend(path: str = "/", query: dict[str, str] | None = None) -> str:
    url = settings.frontend_url.rstrip("/") + path
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


@router.get("/providers", response_model=AuthProvidersOut)
def auth_providers():
    """What the sign-in page is allowed to offer.

    Google is absent until both client id and secret are configured, so a button
    that cannot complete the flow is never shown.
    """
    return AuthProvidersOut(google=google_is_configured())


@router.post("/register", response_model=UserOut)
def register(
    payload: RegisterRequest, response: Response, db: Session = Depends(get_db)
):
    try:
        user = create_user(
            db,
            email=payload.email,
            password=payload.password,
            username=payload.username,
            timezone=_validated_timezone(payload.timezone),
        )
    except AuthError as exc:
        status = 409 if "already" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    token = create_session(db, user)
    _set_session_cookie(response, token)
    return user


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = authenticate(db, payload.login_id, payload.password)
    if user is None:
        ident = payload.login_id.strip().lower()
        demo_ids = {
            settings.demo_email.strip().lower(),
            settings.demo_username.strip().lower(),
        }
        if ident in demo_ids:
            # Register is 8+ characters; the published demo password is not.
            # Restoring the hash here means juhi / shelfit keeps working even
            # if something else rewrote the demo row.
            ensure_demo_user(db)
            user = authenticate(db, payload.login_id, payload.password)
    if user is None:
        # Same message for a missing account, a Google-only account, and a wrong
        # password, so a guess cannot tell which of the three it hit.
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_session(db, user)
    _set_session_cookie(response, token)
    return user


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    revoke_session(db, request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "signed_out"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/google")
def google_start():
    if not google_is_configured():
        raise HTTPException(
            status_code=503, detail="Google sign-in is not configured"
        )
    state = secrets.token_urlsafe(24)
    redirect = RedirectResponse(google_authorize_url(state), status_code=302)
    redirect.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=600,
        path="/",
    )
    return redirect


@router.get("/google/callback")
def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    expected = request.cookies.get(OAUTH_STATE_COOKIE)

    def _to_frontend(query: dict[str, str] | None = None) -> RedirectResponse:
        response = RedirectResponse(_frontend("/", query), status_code=302)
        response.delete_cookie(OAUTH_STATE_COOKIE, path="/")
        return response

    if error or not code or not state or not expected or state != expected:
        return _to_frontend({"auth_error": "Google sign-in was cancelled or failed"})

    try:
        identity = fetch_google_identity(code)
        user = user_from_google(db, identity)
    except GoogleOAuthError as exc:
        return _to_frontend({"auth_error": str(exc)})

    redirect = _to_frontend()
    token = create_session(db, user)
    _set_session_cookie(redirect, token)
    return redirect
