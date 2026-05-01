"""add leasing_advisor_history table

Revision ID: d1e2f3a4b5c6
Revises: 13ce1cc01779
Create Date: 2026-02-20 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = '13ce1cc01779'
branch_labels = None
depends_on = None


def upgrade():
    # Guard: only create if table doesn't already exist (idempotent)
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'leasing_advisor_history' not in inspector.get_table_names():
        op.create_table(
            'leasing_advisor_history',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
            sa.Column('frame_input_json', sa.Text(), nullable=False),
            sa.Column('candidates_json', sa.Text(), nullable=False),
            sa.Column('prefs_json', sa.Text(), nullable=False),
            sa.Column('gemini_response_json', sa.Text(), nullable=False),
            sa.Column('request_id', sa.String(64), nullable=True),
            sa.Column('duration_ms', sa.Integer(), nullable=True),
        )
        op.create_index(
            'ix_leasing_history_user_created',
            'leasing_advisor_history',
            ['user_id', sa.text('created_at DESC')],
            unique=False,
        )


def downgrade():
    op.drop_index('ix_leasing_history_user_created', table_name='leasing_advisor_history')
    op.drop_table('leasing_advisor_history')
