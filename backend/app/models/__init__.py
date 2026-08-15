"""Model package.

Importing every model here guarantees each table is registered on the shared
metadata before `Base.metadata.create_all()` runs. Without this, a model that
nothing else imports would silently never get a table.
"""

from app.models.base import Base
from app.models.cache import CacheEntry
from app.models.category import LearnedCategory
from app.models.inventory import Disposition, Expiration, InventoryItem
from app.models.shelf_life import LearnedShelfLife

__all__ = [
    "Base",
    "CacheEntry",
    "Disposition",
    "Expiration",
    "InventoryItem",
    "LearnedCategory",
    "LearnedShelfLife",
]
