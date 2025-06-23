"""
JWT helpers: create and decode access tokens.
Uses python-jose because it's minimalist and async-friendly.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from pydantic import BaseModel

# 1  JWT configuration
SECRET_KEY = "CHANGE_THIS_LATER_TO_RANDOM_STRING"   # .env later
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MIN = 60 * 24  # 24 hours

class TokenPayload(BaseModel):
    sub: str  # subject = user id / email
    exp: int  # epoch seconds

def create_access_token(subject: str, expires_delta: int | None = None) -> str:
    """Return a signed JWT string."""
    if expires_delta is None:
        expires_delta = ACCESS_TOKEN_EXPIRE_MIN
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_delta)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> TokenPayload:
    """Return payload if token valid; raise JWTError otherwise."""
    data: dict[str, Any] = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    return TokenPayload(**data)