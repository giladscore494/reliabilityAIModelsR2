"""ensure duration_ms column exists on search_history

Revision ID: b3bbf1d1c46d
Revises: 0f9270bc1e04
Create Date: 2026-01-11 17:55:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3bbf1d1c46d'
down_revision = '0f9270bc1e04'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("search_history")}
    if "duration_ms" not in columns:
        op.add_column("search_history", sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("search_history")}
    if "duration_ms" in columns:
        op.drop_column("search_history", "duration_ms")
