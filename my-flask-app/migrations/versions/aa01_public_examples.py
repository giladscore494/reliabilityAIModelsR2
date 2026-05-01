"""add public example columns to search_history

Revision ID: aa01_public_examples
Revises: f1c3bcc69907
Create Date: 2026-04-07 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'aa01_public_examples'
down_revision = 'f1c3bcc69907'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('search_history', sa.Column('is_public_example', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('search_history', sa.Column('example_slug', sa.String(length=64), nullable=True))
    op.create_unique_constraint('uq_search_history_example_slug', 'search_history', ['example_slug'])
    op.create_index(
        'idx_search_history_public_examples',
        'search_history',
        ['example_slug'],
        postgresql_where=sa.text('is_public_example = true'),
    )


def downgrade():
    op.drop_index('idx_search_history_public_examples', table_name='search_history')
    op.drop_constraint('uq_search_history_example_slug', 'search_history', type_='unique')
    op.drop_column('search_history', 'example_slug')
    op.drop_column('search_history', 'is_public_example')
