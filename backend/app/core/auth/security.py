"""
Password hashing helpers using passlib (bcrypt).
"""

from passlib.context import CryptContext

# Configure bcrypt—the current industry standard
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(plain_password: str) -> str:
    """Return bcrypt hash of the plaintext password."""
    return pwd_context.hash(plain_password)

def verify_password(plain_password: str, hashed: str) -> bool:
    """Safely compare plaintext against an existing hash."""
    return pwd_context.verify(plain_password, hashed)