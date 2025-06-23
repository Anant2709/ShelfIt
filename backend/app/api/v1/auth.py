# app/api/v1/auth.py
from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_session
from app.models.user import User
from app.core.auth.security import hash_password, verify_password
from app.core.auth.jwt import create_access_token, decode_token, JWTError
from app.schemas.user import UserCreate, UserLogin, UserOut
from app.schemas.token import Token

router = APIRouter()
bearer_scheme = HTTPBearer()

# ---------- helper dependency ----------
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    """
    • Extracts the Bearer token from the Authorization header
    • Decodes it
    • Fetches the matching user
    """
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = payload.sub
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# ---------- routes ----------
@router.post("/register", response_model=Token, status_code=201)
async def register(data: UserCreate, db: AsyncSession = Depends(get_session)):
    # check email uniqueness
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(409, "Email already registered")

    user = User(email=data.email, password_hash=hash_password(data.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return Token(access_token=create_access_token(user.id))

@router.post("/login", response_model=Token)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(User).where(User.email == credentials.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return Token(access_token=create_access_token(user.id))

@router.get("/me", response_model=UserOut)
async def me(current: User = Depends(get_current_user)):
    return current