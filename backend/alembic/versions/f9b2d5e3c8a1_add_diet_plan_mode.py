"""add diet plan mode and meal ingredients

`mode` is pantry (cook from the shelf) or ideal (recommend the diet, then
diff against the shelf). `ingredients_json` is the recipe's grocery names so
uses/missing can be recomputed after the fridge changes, including for meals
the language model wrote rather than ones in the curated fallback file.

Revision ID: f9b2d5e3c8a1
Revises: e8a1c4d2b7f0
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9b2d5e3c8a1"
down_revision: Union[str, None] = "e8a1c4d2b7f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("diet_plans", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mode", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE diet_plans SET mode = 'pantry' WHERE mode IS NULL"))
    with op.batch_alter_table("diet_plans", schema=None) as batch_op:
        batch_op.alter_column("mode", existing_type=sa.String(), nullable=False)

    with op.batch_alter_table("diet_plan_meals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ingredients_json", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE diet_plan_meals SET ingredients_json = '[]' "
            "WHERE ingredients_json IS NULL"
        )
    )
    with op.batch_alter_table("diet_plan_meals", schema=None) as batch_op:
        batch_op.alter_column(
            "ingredients_json", existing_type=sa.Text(), nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("diet_plan_meals", schema=None) as batch_op:
        batch_op.drop_column("ingredients_json")
    with op.batch_alter_table("diet_plans", schema=None) as batch_op:
        batch_op.drop_column("mode")
