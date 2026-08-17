"""heavy recipe card fields on diet_plan_meals

Stores servings, times, detailed ingredients, steps, and macros as one JSON
blob. Flat ingredients_json stays for pantry matching.

Revision ID: c6e9f3b2d014
Revises: b5d8e2a1c903
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6e9f3b2d014"
down_revision: Union[str, None] = "b5d8e2a1c903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("diet_plan_meals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("recipe_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("diet_plan_meals", schema=None) as batch_op:
        batch_op.drop_column("recipe_json")
