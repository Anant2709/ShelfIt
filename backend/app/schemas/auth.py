from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    timezone: str = "UTC"


class LoginRequest(BaseModel):
    password: str
    # Username or email. `email` is accepted so older clients keep working.
    identifier: str | None = None
    email: str | None = None

    @model_validator(mode="after")
    def require_an_identifier(self):
        if not (self.identifier or self.email):
            raise ValueError("Username or email is required")
        return self

    @property
    def login_id(self) -> str:
        return (self.identifier or self.email or "").strip()


class AuthProvidersOut(BaseModel):
    google: bool
    demo: bool


class UserOut(BaseModel):
    id: str
    email: str
    username: str
    timezone: str
    created_at: datetime
    has_password: bool = True

    class Config:
        from_attributes = True
