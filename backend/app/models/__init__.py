"""Model package.

Importing every model here guarantees each table is registered on the shared
metadata. Autogenerate and the test-time `create_all` both read that metadata;
a model that nothing else imports would silently never get a table or a
migration.
"""

from app.models.base import Base
from app.models.cache import CacheEntry
from app.models.category import LearnedCategory
from app.models.conversation import ChatMessage, Conversation
from app.models.diet import (
    DietExtraIntake,
    DietLog,
    DietPlan,
    DietPlanMeal,
    DietProfile,
    DietWeighIn,
)
from app.models.inventory import Disposition, Expiration, InventoryItem
from app.models.shelf_life import LearnedShelfLife
from app.models.user import Session, User

__all__ = [
    "Base",
    "CacheEntry",
    "ChatMessage",
    "Conversation",
    "DietExtraIntake",
    "DietLog",
    "DietPlan",
    "DietPlanMeal",
    "DietProfile",
    "DietWeighIn",
    "Disposition",
    "Expiration",
    "InventoryItem",
    "LearnedCategory",
    "LearnedShelfLife",
    "Session",
    "User",
]
