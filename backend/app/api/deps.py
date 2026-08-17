"""Request-scoped identities.

`get_db` stays in `app.db.deps` because the rest of the app already imports it
from there. Auth lives here so endpoint modules depend on one place for "who is
this request?" rather than each re-reading the cookie.
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models.user import User
from app.services.auth import COOKIE_NAME, user_for_token


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    user = user_for_token(db, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user
