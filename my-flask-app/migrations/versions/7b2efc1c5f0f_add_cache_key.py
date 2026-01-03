"""add cache_key to search_history

Revision ID: 7b2efc1c5f0f
Revises: 6c3a4ffe837e
Create Date: 2026-01-03 15:50:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7b2efc1c5f0f'
down_revision = '6c3a4ffe837e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cache_key', sa.String(length=128), nullable=True))
        batch_op.create_index(batch_op.f('ix_search_history_cache_key'), ['cache_key'], unique=False)


def downgrade():
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_search_history_cache_key'))
        batch_op.drop_column('cache_key')
