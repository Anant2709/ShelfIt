"""Model package.

Importing every model here guarantees each table is registered on the shared
metadata before `Base.metadata.create_all()` runs. Without this, a model that
nothing else imports would silently never get a table.
"""

from app.models.base import Base
from app.models.cache import CacheEntry
from app.models.inventory import Expiration, InventoryItem

__all__ = ["Base", "CacheEntry", "Expiration", "InventoryItem"]
