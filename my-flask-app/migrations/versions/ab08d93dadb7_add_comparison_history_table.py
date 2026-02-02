"""add comparison_history table

Revision ID: ab08d93dadb7
Revises: 7f1b3b0a2c4d
Create Date: 2026-02-02 19:33:01.400451

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'ab08d93dadb7'
down_revision = '7f1b3b0a2c4d'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # Only create comparison_history if it doesn't exist
    if inspector.has_table("comparison_history"):
        print("[MIGRATION] comparison_history already exists; skipping create_table")
    else:
        op.create_table('comparison_history',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('session_id', sa.String(length=64), nullable=True),
            sa.Column('cars_selected', sa.Text(), nullable=False),  # JSON stored as Text
            sa.Column('model_json_raw', sa.Text(), nullable=True),  # JSON stored as Text
            sa.Column('computed_result', sa.Text(), nullable=True),  # JSON stored as Text
            sa.Column('sources_index', sa.Text(), nullable=True),  # JSON stored as Text
            sa.Column('model_name', sa.String(length=64), nullable=False, server_default=sa.text("'gemini-3-flash'")),
            sa.Column('grounding_enabled', sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column('prompt_version', sa.String(length=32), nullable=False, server_default=sa.text("'v1'")),
            sa.Column('request_hash', sa.String(length=64), nullable=True),
            sa.Column('duration_ms', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        print("[MIGRATION] comparison_history table created")

    # Create indexes
    try:
        indexes = {idx.get("name") for idx in inspector.get_indexes("comparison_history")}
    except Exception:
        indexes = set()

    if "ix_comparison_history_created_at" not in indexes:
        try:
            op.create_index('ix_comparison_history_created_at', 'comparison_history', ['created_at'], unique=False)
            print("[MIGRATION] ix_comparison_history_created_at created")
        except Exception as e:
            print(f"[MIGRATION] ix_comparison_history_created_at creation failed: {e}")

    if "ix_comparison_history_session_id" not in indexes:
        try:
            op.create_index('ix_comparison_history_session_id', 'comparison_history', ['session_id'], unique=False)
            print("[MIGRATION] ix_comparison_history_session_id created")
        except Exception as e:
            print(f"[MIGRATION] ix_comparison_history_session_id creation failed: {e}")

    if "ix_comparison_history_request_hash" not in indexes:
        try:
            op.create_index('ix_comparison_history_request_hash', 'comparison_history', ['request_hash'], unique=False)
            print("[MIGRATION] ix_comparison_history_request_hash created")
        except Exception as e:
            print(f"[MIGRATION] ix_comparison_history_request_hash creation failed: {e}")

    if "ix_comparison_history_user_created" not in indexes:
        try:
            op.create_index('ix_comparison_history_user_created', 'comparison_history', ['user_id', 'created_at'], unique=False)
            print("[MIGRATION] ix_comparison_history_user_created created")
        except Exception as e:
            print(f"[MIGRATION] ix_comparison_history_user_created creation failed: {e}")


def downgrade():
    try:
        op.drop_index('ix_comparison_history_user_created', table_name='comparison_history')
    except Exception:
        pass
    try:
        op.drop_index('ix_comparison_history_request_hash', table_name='comparison_history')
    except Exception:
        pass
    try:
        op.drop_index('ix_comparison_history_session_id', table_name='comparison_history')
    except Exception:
        pass
    try:
        op.drop_index('ix_comparison_history_created_at', table_name='comparison_history')
    except Exception:
        pass
    op.drop_table('comparison_history')
