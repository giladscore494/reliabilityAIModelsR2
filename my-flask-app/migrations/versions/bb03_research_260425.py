"""Research refactor 2026-04-25: Add consent_given, ip_hash, etc.

Revision ID: bb03_research_260425
Revises: aa02_feedback_table
Create Date: 2026-04-25 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bb03_research_260425'
down_revision = 'aa02_feedback_table'
branch_labels = None
depends_on = None


def upgrade():
    # Use batch_alter_table for SQLite compatibility
    
    # Add columns to research_consent
    with op.batch_alter_table('research_consent', schema=None) as batch_op:
        batch_op.add_column(sa.Column('consent_given', sa.Boolean(), nullable=True, server_default='true'))
        batch_op.add_column(sa.Column('source_page', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('ip_hash', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('user_agent_hash', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('revoked_at', sa.DateTime(), nullable=True))
    
    # Add columns to research_response_session
    with op.batch_alter_table('research_response_session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('question_version', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('related_search_history_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('related_advisor_history_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('related_compare_history_id', sa.Integer(), nullable=True))
    
    # Create foreign keys for the new columns
    # Note: SQLite doesn't support adding FKs after table creation without recreate
    # So we add them in batch_alter_table which handles the recreation internally
    with op.batch_alter_table('research_response_session', schema=None) as batch_op:
        batch_op.create_foreign_key(
            'fk_research_session_search_history',
            'search_history',
            ['related_search_history_id'],
            ['id'],
            ondelete='SET NULL'
        )
        batch_op.create_foreign_key(
            'fk_research_session_advisor_history',
            'advisor_history',
            ['related_advisor_history_id'],
            ['id'],
            ondelete='SET NULL'
        )
        batch_op.create_foreign_key(
            'fk_research_session_compare_history',
            'comparison_history',
            ['related_compare_history_id'],
            ['id'],
            ondelete='SET NULL'
        )
    
    # Add column to research_response
    with op.batch_alter_table('research_response', schema=None) as batch_op:
        batch_op.add_column(sa.Column('answer_type', sa.String(length=32), nullable=True))


def downgrade():
    with op.batch_alter_table('research_response', schema=None) as batch_op:
        batch_op.drop_column('answer_type')
    
    with op.batch_alter_table('research_response_session', schema=None) as batch_op:
        batch_op.drop_constraint('fk_research_session_compare_history', type_='foreignkey')
        batch_op.drop_constraint('fk_research_session_advisor_history', type_='foreignkey')
        batch_op.drop_constraint('fk_research_session_search_history', type_='foreignkey')
        batch_op.drop_column('related_compare_history_id')
        batch_op.drop_column('related_advisor_history_id')
        batch_op.drop_column('related_search_history_id')
        batch_op.drop_column('question_version')
    
    with op.batch_alter_table('research_consent', schema=None) as batch_op:
        batch_op.drop_column('revoked_at')
        batch_op.drop_column('user_agent_hash')
        batch_op.drop_column('ip_hash')
        batch_op.drop_column('source_page')
        batch_op.drop_column('consent_given')
