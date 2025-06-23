from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from pydantic_settings import BaseSettings

# Read DB creds from env – avoids hard-coding secrets
class Settings(BaseSettings):
    db_user: str = "grocery"
    db_pass: str = "grocerypw"
    db_host: str = "localhost"   # <-- keep as string
    db_port: int = 5433          # <-- new
    db_name: str = "grocery"

    class Config:
        env_file = ".env"

settings = Settings()

# Create async SQLAlchemy engine ->  postgresql+asyncpg://user:pass@host/db
DATABASE_URL = (
    f"postgresql+asyncpg://{settings.db_user}:{settings.db_pass}"
    f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,         # True prints SQL – useful while debugging
    pool_size=5,
    max_overflow=0,
)

# sessionmaker factory yields AsyncSession objects
async_session_maker = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

# FastAPI dependency – inject into route handlers
async def get_session() -> AsyncSession:
    async with async_session_maker() as session:
        yield session