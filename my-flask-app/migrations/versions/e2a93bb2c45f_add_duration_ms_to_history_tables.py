"""add duration_ms columns to history tables

Revision ID: e2a93bb2c45f
Revises: b3bbf1d1c46d
Create Date: 2026-01-11 18:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2a93bb2c45f'
down_revision = 'b3bbf1d1c46d'
branch_labels = None
depends_on = None


def _ensure_duration_column(inspector, table_name: str):
    if not inspector.has_table(table_name):
        print(f"[MIGRATION] {table_name} missing; skipping duration_ms add")
        return
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    if "duration_ms" not in columns:
        op.add_column(table_name, sa.Column("duration_ms", sa.Integer(), nullable=True))
        print(f"[MIGRATION] {table_name}.duration_ms added")
    else:
        print(f"[MIGRATION] {table_name}.duration_ms already exists; skipping add_column")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("search_history", "advisor_history"):
        _ensure_duration_column(inspector, table)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("search_history", "advisor_history"):
        if not inspector.has_table(table):
            continue
        columns = {col["name"] for col in inspector.get_columns(table)}
        if "duration_ms" in columns:
            op.drop_column(table, "duration_ms")
