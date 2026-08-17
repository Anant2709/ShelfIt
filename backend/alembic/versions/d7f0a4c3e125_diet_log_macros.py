"""macros on diet logs

Plan-slot logs can carry protein/carbs/fat with a source label, matching extras.

Revision ID: d7f0a4c3e125
Revises: c6e9f3b2d014
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7f0a4c3e125"
down_revision: Union[str, None] = "c6e9f3b2d014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("diet_logs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("protein_g", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("carbs_g", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("fat_g", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("macros_source", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("diet_logs", schema=None) as batch_op:
        batch_op.drop_column("macros_source")
        batch_op.drop_column("fat_g")
        batch_op.drop_column("carbs_g")
        batch_op.drop_column("protein_g")
