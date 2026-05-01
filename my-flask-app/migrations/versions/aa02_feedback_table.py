"""create feedback table

Revision ID: aa02_feedback_table
Revises: aa01_public_examples
Create Date: 2026-04-07 16:01:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'aa02_feedback_table'
down_revision = 'aa01_public_examples'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'feedback',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('search_history_id', sa.BigInteger(), nullable=True),
        sa.Column('is_positive', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['search_history_id'], ['search_history.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_feedback_user_created', 'feedback', ['user_id', 'created_at'])
    op.create_index('ix_feedback_search_history', 'feedback', ['search_history_id'])
    op.create_unique_constraint('uq_feedback_user_search', 'feedback', ['user_id', 'search_history_id'])


def downgrade():
    op.drop_constraint('uq_feedback_user_search', 'feedback', type_='unique')
    op.drop_index('ix_feedback_search_history', table_name='feedback')
    op.drop_index('ix_feedback_user_created', table_name='feedback')
    op.drop_table('feedback')
