"""diet extra intake beyond planned slots

Snacks and restaurant food must not steal a breakfast/lunch/dinner slot.
Macros columns land now so progress only migrates once.

Revision ID: b5d8e2a1c903
Revises: a4c7e1f8b902
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5d8e2a1c903"
down_revision: Union[str, None] = "a4c7e1f8b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "diet_extra_intakes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("logged_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("calories_kcal", sa.Integer(), nullable=True),
        sa.Column("calories_source", sa.String(), nullable=True),
        sa.Column("protein_g", sa.Float(), nullable=True),
        sa.Column("carbs_g", sa.Float(), nullable=True),
        sa.Column("fat_g", sa.Float(), nullable=True),
        sa.Column("macros_source", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_diet_extra_intakes_user_id"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("diet_extra_intakes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_diet_extra_intakes_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_diet_extra_intakes_logged_date"),
            ["logged_date"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("diet_extra_intakes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_diet_extra_intakes_logged_date"))
        batch_op.drop_index(batch_op.f("ix_diet_extra_intakes_user_id"))
    op.drop_table("diet_extra_intakes")
