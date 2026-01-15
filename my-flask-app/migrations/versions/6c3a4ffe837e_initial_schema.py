"""initial schema

Revision ID: 6c3a4ffe837e
Revises: 
Create Date: 2026-01-03 15:16:45.538489

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '6c3a4ffe837e'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    created_tables = set()

    def _log(message: str):
        print(f"[MIGRATION] {message}")

    def _ensure_table(name: str, create_fn):
        if name in tables:
            _log(f"{name} already exists; skipping create_table")
            return False
        create_fn()
        tables.add(name)
        created_tables.add(name)
        _log(f"{name} created")
        return True

    def _existing_indexes(table_name: str):
        if table_name in created_tables:
            return set()
        return {idx.get("name") for idx in inspector.get_indexes(table_name)}

    def _ensure_indexes(table_name: str, index_specs: list[tuple[str, list[str]]]):
        if table_name not in tables:
            return
        existing = _existing_indexes(table_name)
        for index_name, columns in index_specs:
            if index_name in existing:
                _log(f"{table_name}.{index_name} already exists; skipping index")
                continue
            op.create_index(index_name, table_name, columns, unique=False)
            _log(f"{table_name}.{index_name} created")

    _ensure_table(
        "ip_rate_limit",
        lambda: op.create_table(
            "ip_rate_limit",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("ip", sa.String(length=64), nullable=False),
            sa.Column("window_start", sa.DateTime(), nullable=False),
            sa.Column("count", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("ip", "window_start", name="uq_ip_window"),
        ),
    )
    _ensure_indexes(
        "ip_rate_limit",
        [
            (op.f("ix_ip_rate_limit_ip"), ["ip"]),
            (op.f("ix_ip_rate_limit_window_start"), ["window_start"]),
            ("ix_ip_window", ["ip", "window_start"]),
        ],
    )

    _ensure_table(
        "user",
        lambda: op.create_table(
            "user",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("google_id", sa.String(length=200), nullable=False),
            sa.Column("email", sa.String(length=120), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email"),
            sa.UniqueConstraint("google_id"),
        ),
    )
    _ensure_table(
        "advisor_history",
        lambda: op.create_table(
            "advisor_history",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("profile_json", sa.Text(), nullable=False),
            sa.Column("result_json", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        ),
    )
    _ensure_table(
        "daily_quota_usage",
        lambda: op.create_table(
            "daily_quota_usage",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("count", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "day", name="uq_user_day_quota_usage"),
        ),
    )
    _ensure_indexes(
        "daily_quota_usage",
        [
            (op.f("ix_daily_quota_usage_day"), ["day"]),
            ("ix_quota_day_user", ["day", "user_id"]),
        ],
    )

    _ensure_table(
        "quota_reservation",
        lambda: op.create_table(
            "quota_reservation",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        ),
    )
    _ensure_indexes(
        "quota_reservation",
        [
            (op.f("ix_quota_reservation_created_at"), ["created_at"]),
            (op.f("ix_quota_reservation_day"), ["day"]),
            (op.f("ix_quota_reservation_status"), ["status"]),
            ("ix_reservation_user_day_status", ["user_id", "day", "status"]),
        ],
    )

    _ensure_table(
        "search_history",
        lambda: op.create_table(
            "search_history",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("make", sa.String(length=100), nullable=True),
            sa.Column("model", sa.String(length=100), nullable=True),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("mileage_range", sa.String(length=100), nullable=True),
            sa.Column("fuel_type", sa.String(length=100), nullable=True),
            sa.Column("transmission", sa.String(length=100), nullable=True),
            sa.Column("result_json", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
        ),
    )


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('search_history')
    with op.batch_alter_table('quota_reservation', schema=None) as batch_op:
        batch_op.drop_index('ix_reservation_user_day_status')
        batch_op.drop_index(batch_op.f('ix_quota_reservation_status'))
        batch_op.drop_index(batch_op.f('ix_quota_reservation_day'))
        batch_op.drop_index(batch_op.f('ix_quota_reservation_created_at'))

    op.drop_table('quota_reservation')
    with op.batch_alter_table('daily_quota_usage', schema=None) as batch_op:
        batch_op.drop_index('ix_quota_day_user')
        batch_op.drop_index(batch_op.f('ix_daily_quota_usage_day'))

    op.drop_table('daily_quota_usage')
    op.drop_table('advisor_history')
    op.drop_table('user')
    with op.batch_alter_table('ip_rate_limit', schema=None) as batch_op:
        batch_op.drop_index('ix_ip_window')
        batch_op.drop_index(batch_op.f('ix_ip_rate_limit_window_start'))
        batch_op.drop_index(batch_op.f('ix_ip_rate_limit_ip'))

    op.drop_table('ip_rate_limit')
    # ### end Alembic commands ###
