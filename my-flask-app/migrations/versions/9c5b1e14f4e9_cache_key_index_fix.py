"""ensure cache_key column exists and is indexed

Revision ID: 9c5b1e14f4e9
Revises: 7b2efc1c5f0f
Create Date: 2026-01-03 18:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c5b1e14f4e9'
down_revision = '7b2efc1c5f0f'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("search_history")}

    if "cache_key" not in columns:
        op.add_column("search_history", sa.Column("cache_key", sa.String(length=64), nullable=True))
    else:
        try:
            op.alter_column(
                "search_history",
                "cache_key",
                existing_type=sa.String(length=128),
                type_=sa.String(length=64),
                existing_nullable=True,
            )
        except Exception:
            # Best-effort; schema may already be correct
            pass

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_search_history_user_cache_ts "
        "ON search_history (user_id, cache_key, timestamp DESC);"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_search_history_user_cache_ts;")
    try:
        op.alter_column(
            "search_history",
            "cache_key",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=True,
        )
    except Exception:
        pass
