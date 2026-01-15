"""add cache_key to search_history

Revision ID: 7b2efc1c5f0f
Revises: 6c3a4ffe837e
Create Date: 2026-01-03 15:50:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '7b2efc1c5f0f'
down_revision = '6c3a4ffe837e'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("search_history"):
        print("[MIGRATION] search_history missing; skipping cache_key migration")
        return
    columns = {col["name"] for col in inspector.get_columns("search_history")}
    indexes = {idx.get("name") for idx in inspector.get_indexes("search_history")}
    with op.batch_alter_table("search_history", schema=None) as batch_op:
        if "cache_key" in columns:
            print("[MIGRATION] search_history.cache_key already exists; skipping add_column")
        else:
            batch_op.add_column(sa.Column("cache_key", sa.String(length=128), nullable=True))
            print("[MIGRATION] search_history.cache_key added")
        index_name = batch_op.f("ix_search_history_cache_key")
        if index_name in indexes:
            print("[MIGRATION] ix_search_history_cache_key already exists; skipping index")
        else:
            batch_op.create_index(index_name, ["cache_key"], unique=False)
            print("[MIGRATION] ix_search_history_cache_key created")


def downgrade():
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_search_history_cache_key'))
        batch_op.drop_column('cache_key')
