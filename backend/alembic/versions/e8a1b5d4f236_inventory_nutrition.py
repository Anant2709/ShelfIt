"""nutrition columns on inventory items

Packaged products may carry kcal/macros from Open Food Facts or Exa, always with
a source label. Unreadable labels stay none and skip network lookups.

Revision ID: e8a1b5d4f236
Revises: d7f0a4c3e125
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8a1b5d4f236"
down_revision: Union[str, None] = "d7f0a4c3e125"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("brand", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("product_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("calories_kcal", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("protein_g", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("carbs_g", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("fat_g", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("nutrition_source", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("inventory_items", schema=None) as batch_op:
        batch_op.drop_column("nutrition_source")
        batch_op.drop_column("fat_g")
        batch_op.drop_column("carbs_g")
        batch_op.drop_column("protein_g")
        batch_op.drop_column("calories_kcal")
        batch_op.drop_column("product_name")
        batch_op.drop_column("brand")
