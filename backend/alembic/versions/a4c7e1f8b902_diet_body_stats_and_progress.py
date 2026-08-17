"""diet body stats, meal calories, weigh-ins, skip substitutes

Starting weight and target weight are required for a profile. Calorie targets
are then Mifflin-St Jeor, labeled as an estimate. Meal kcal and skip substitutes
feed progress analytics. Weigh-ins are the history; the profile holds current.

Revision ID: a4c7e1f8b902
Revises: f9b2d5e3c8a1
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4c7e1f8b902"
down_revision: Union[str, None] = "f9b2d5e3c8a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("diet_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sex", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("age", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("height_cm", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("weight_kg", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("target_weight_kg", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("activity", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("cooking_time", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("preferences", sa.Text(), nullable=True))

    op.execute(
        sa.text(
            "UPDATE diet_profiles SET "
            "sex = 'prefer_not', age = 30, height_cm = 165, weight_kg = 65, "
            "target_weight_kg = 65, activity = 'moderate', "
            "cooking_time = 'about_30', preferences = '[]' "
            "WHERE sex IS NULL"
        )
    )

    with op.batch_alter_table("diet_profiles", schema=None) as batch_op:
        batch_op.alter_column("sex", existing_type=sa.String(), nullable=False)
        batch_op.alter_column("age", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("height_cm", existing_type=sa.Float(), nullable=False)
        batch_op.alter_column("weight_kg", existing_type=sa.Float(), nullable=False)
        batch_op.alter_column(
            "target_weight_kg", existing_type=sa.Float(), nullable=False
        )
        batch_op.alter_column("activity", existing_type=sa.String(), nullable=False)
        batch_op.alter_column(
            "cooking_time", existing_type=sa.String(), nullable=False
        )
        batch_op.alter_column("preferences", existing_type=sa.Text(), nullable=False)

    with op.batch_alter_table("diet_plan_meals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("kcal", sa.Integer(), nullable=True))

    with op.batch_alter_table("diet_logs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("substitute_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("calories_kcal", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("calories_source", sa.String(), nullable=True))

    op.create_table(
        "diet_weigh_ins",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("logged_date", sa.Date(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_diet_weigh_ins_user_id"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "logged_date", name="uq_diet_weigh_ins_user_date"
        ),
    )
    with op.batch_alter_table("diet_weigh_ins", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_diet_weigh_ins_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("diet_weigh_ins", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_diet_weigh_ins_user_id"))
    op.drop_table("diet_weigh_ins")

    with op.batch_alter_table("diet_logs", schema=None) as batch_op:
        batch_op.drop_column("calories_source")
        batch_op.drop_column("calories_kcal")
        batch_op.drop_column("substitute_text")

    with op.batch_alter_table("diet_plan_meals", schema=None) as batch_op:
        batch_op.drop_column("kcal")

    with op.batch_alter_table("diet_profiles", schema=None) as batch_op:
        batch_op.drop_column("preferences")
        batch_op.drop_column("cooking_time")
        batch_op.drop_column("activity")
        batch_op.drop_column("target_weight_kg")
        batch_op.drop_column("weight_kg")
        batch_op.drop_column("height_cm")
        batch_op.drop_column("age")
        batch_op.drop_column("sex")
