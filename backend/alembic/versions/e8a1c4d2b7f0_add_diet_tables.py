"""add diet profile, plan, and log tables

Diet is per-person health data, which is why accounts exist. These tables are
empty on upgrade: there is nothing to backfill, and a missing profile is a
real state (the questionnaire has not been answered) rather than a default.

Revision ID: e8a1c4d2b7f0
Revises: d7e4b2c91a08
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8a1c4d2b7f0"
down_revision: Union[str, None] = "d7e4b2c91a08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "diet_profiles",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("goal", sa.String(), nullable=False),
        sa.Column("eating_pattern", sa.String(), nullable=False),
        sa.Column("allergens", sa.Text(), nullable=False),
        sa.Column("meals_per_day", sa.Integer(), nullable=False),
        sa.Column("calorie_target", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_diet_profiles_user_id"
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "diet_plans",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("calorie_target", sa.Integer(), nullable=False),
        sa.Column("goal", sa.String(), nullable=False),
        sa.Column("eating_pattern", sa.String(), nullable=False),
        sa.Column("meals_per_day", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_diet_plans_user_id"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("diet_plans", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_diet_plans_user_id"), ["user_id"], unique=False
        )

    op.create_table(
        "diet_plan_meals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("day_offset", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(), nullable=False),
        sa.Column("recipe_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("uses_json", sa.Text(), nullable=False),
        sa.Column("missing_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["diet_plans.id"], name="fk_diet_plan_meals_plan_id"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("diet_plan_meals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_diet_plan_meals_plan_id"), ["plan_id"], unique=False
        )

    op.create_table(
        "diet_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("logged_date", sa.Date(), nullable=False),
        sa.Column("slot", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("recipe_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_diet_logs_user_id"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "logged_date",
            "slot",
            name="uq_diet_logs_user_date_slot",
        ),
    )
    with op.batch_alter_table("diet_logs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_diet_logs_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("diet_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_diet_logs_user_id"))
    op.drop_table("diet_logs")

    with op.batch_alter_table("diet_plan_meals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_diet_plan_meals_plan_id"))
    op.drop_table("diet_plan_meals")

    with op.batch_alter_table("diet_plans", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_diet_plans_user_id"))
    op.drop_table("diet_plans")

    op.drop_table("diet_profiles")
