"""add_duration_ms_to_search_history

Revision ID: 0f9270bc1e04
Revises: 9c5b1e14f4e9
Create Date: 2026-01-11 14:35:24.218862

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '0f9270bc1e04'
down_revision = '9c5b1e14f4e9'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("search_history"):
        print("[MIGRATION] search_history missing; skipping duration_ms add")
        return
    columns = {col["name"] for col in inspector.get_columns("search_history")}
    if "duration_ms" in columns:
        print("[MIGRATION] search_history.duration_ms already exists; skipping add_column")
        return
    op.add_column("search_history", sa.Column("duration_ms", sa.Integer(), nullable=True))
    print("[MIGRATION] search_history.duration_ms added")


def downgrade():
    # Remove duration_ms column from search_history table
    op.drop_column('search_history', 'duration_ms')
