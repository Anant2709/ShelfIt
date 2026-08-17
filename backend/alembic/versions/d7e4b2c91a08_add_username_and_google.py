"""add username and google identity

Password accounts get a unique username. Google-only accounts have no password
hash. Existing rows keep their password and receive a username derived from the
email, so the demo login still works as juhi / shelfit.

Revision ID: d7e4b2c91a08
Revises: c3f8a91d2e04
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e4b2c91a08"
down_revision: Union[str, None] = "c3f8a91d2e04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("username", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("google_id", sa.String(), nullable=True))
        batch_op.alter_column(
            "password_hash", existing_type=sa.String(), nullable=True
        )

    op.execute(
        sa.text(
            "UPDATE users SET username = lower(substr(email, 1, "
            "instr(email, '@') - 1)) WHERE username IS NULL"
        )
    )
    op.execute(sa.text("UPDATE users SET username = 'user' WHERE username IS NULL OR username = ''"))

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("username", existing_type=sa.String(), nullable=False)
        batch_op.create_index(
            batch_op.f("ix_users_username"), ["username"], unique=True
        )
        batch_op.create_index(
            batch_op.f("ix_users_google_id"), ["google_id"], unique=True
        )


def downgrade() -> None:
    """Drop the username and Google subject, restore a required hash."""

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_google_id"))
        batch_op.drop_index(batch_op.f("ix_users_username"))
        batch_op.drop_column("google_id")
        batch_op.drop_column("username")
        batch_op.alter_column(
            "password_hash", existing_type=sa.String(), nullable=False
        )
