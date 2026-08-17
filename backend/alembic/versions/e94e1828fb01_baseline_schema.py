"""baseline schema

The schema as it stood when migrations were introduced, replacing
`Base.metadata.create_all()`. Generated against an empty database on purpose: run
against the existing one it would have compared equal and produced nothing.

A database that predates this revision already has these tables but no
`alembic_version` row, so it must be *stamped* at this revision rather than
upgraded to it -- `alembic stamp e94e1828fb01`. Running the upgrade would fail on
the first `CREATE TABLE`. Fresh databases run it normally.

Revision ID: e94e1828fb01
Revises:
Create Date: 2026-08-15 19:58:20.190716

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e94e1828fb01'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('cache_entries',
    sa.Column('namespace', sa.String(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('value_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('namespace', 'key')
    )
    op.create_table('conversations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('inventory_items',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('category', sa.String(), nullable=True),
    sa.Column('category_source', sa.String(), nullable=True),
    sa.Column('image_uri', sa.String(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('unit', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_inventory_items_category'), ['category'], unique=False)

    op.create_table('learned_categories',
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('category', sa.String(), nullable=False),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_table('learned_shelf_life',
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('days', sa.Integer(), nullable=False),
    sa.Column('anchor', sa.String(), nullable=True),
    sa.Column('anchor_days', sa.Integer(), nullable=True),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_table('chat_messages',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('conversation_id', sa.String(), nullable=False),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_messages_conversation_id'), ['conversation_id'], unique=False)

    op.create_table('dispositions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('outcome', sa.String(), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('unit', sa.String(), nullable=False),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.Column('item_name', sa.String(), nullable=False),
    sa.Column('item_category', sa.String(), nullable=True),
    sa.Column('days_remaining', sa.Integer(), nullable=True),
    sa.Column('expiration_date', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('dispositions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dispositions_item_id'), ['item_id'], unique=False)

    op.create_table('expirations',
    sa.Column('item_id', sa.String(), nullable=False),
    sa.Column('expiration_date', sa.Date(), nullable=True),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('shelf_life_days', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ),
    sa.PrimaryKeyConstraint('item_id')
    )


def downgrade() -> None:
    op.drop_table('expirations')
    with op.batch_alter_table('dispositions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dispositions_item_id'))

    op.drop_table('dispositions')
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chat_messages_conversation_id'))

    op.drop_table('chat_messages')
    op.drop_table('learned_shelf_life')
    op.drop_table('learned_categories')
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inventory_items_category'))

    op.drop_table('inventory_items')
    op.drop_table('conversations')
    op.drop_table('cache_entries')
