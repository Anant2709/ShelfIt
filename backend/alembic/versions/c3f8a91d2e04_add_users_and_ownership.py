"""add users and ownership

The first real migration: not a baseline of an empty schema, but a change to one
that already has rows. Existing inventory and conversations are assigned to the
demo user so the fridge that was already there becomes someone's kitchen rather
than being deleted.

Revision ID: c3f8a91d2e04
Revises: e94e1828fb01
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.config import settings
from app.services.auth import DEMO_USER_ID, hash_password


revision: str = "c3f8a91d2e04"
down_revision: Union[str, None] = "e94e1828fb01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sessions_user_id"), ["user_id"], unique=False)

    users = sa.table(
        "users",
        sa.column("id", sa.String),
        sa.column("email", sa.String),
        sa.column("password_hash", sa.String),
        sa.column("timezone", sa.String),
        sa.column("created_at", sa.DateTime),
    )
    op.execute(
        users.insert().values(
            id=DEMO_USER_ID,
            email=settings.demo_email.strip().lower(),
            password_hash=hash_password(settings.demo_password),
            timezone=settings.demo_timezone,
            created_at=sa.func.current_timestamp(),
        )
    )

    with op.batch_alter_table("inventory_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.String(), nullable=True))

    op.execute(
        sa.text("UPDATE inventory_items SET user_id = :uid").bindparams(
            uid=DEMO_USER_ID
        )
    )

    with op.batch_alter_table("inventory_items", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(), nullable=False)
        batch_op.create_index(
            batch_op.f("ix_inventory_items_user_id"), ["user_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_inventory_items_user_id", "users", ["user_id"], ["id"]
        )

    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.String(), nullable=True))

    op.execute(
        sa.text("UPDATE conversations SET user_id = :uid").bindparams(
            uid=DEMO_USER_ID
        )
    )

    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(), nullable=False)
        batch_op.create_index(
            batch_op.f("ix_conversations_user_id"), ["user_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_conversations_user_id", "users", ["user_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.drop_constraint("fk_conversations_user_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_conversations_user_id"))
        batch_op.drop_column("user_id")

    with op.batch_alter_table("inventory_items", schema=None) as batch_op:
        batch_op.drop_constraint("fk_inventory_items_user_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_inventory_items_user_id"))
        batch_op.drop_column("user_id")

    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sessions_user_id"))
    op.drop_table("sessions")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_email"))
    op.drop_table("users")
