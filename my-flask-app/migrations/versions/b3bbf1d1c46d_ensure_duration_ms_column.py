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

USER_FK_FALLBACK_NAME = "search_history_user_id_fkey"

# Safeguard environments where earlier revisions missed duration_ms or cascade settings.


def _find_search_history_user_fk(inspector):
    fks = inspector.get_foreign_keys("search_history")
    return next(
        (
            fk
            for fk in fks
            if fk.get("referred_table") == "user" and set(fk.get("referred_columns") or []) == {"id"}
        ),
        None,
    )


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect_name = getattr(bind.dialect, "name", "")
    if not inspector.has_table("search_history"):
        print("[MIGRATION] search_history missing; skipping duration_ms ensure")
        return
    columns = {col["name"] for col in inspector.get_columns("search_history")}
    if "duration_ms" not in columns:
        op.add_column("search_history", sa.Column("duration_ms", sa.Integer(), nullable=True))
        print("[MIGRATION] search_history.duration_ms added")
    else:
        print("[MIGRATION] search_history.duration_ms already exists; skipping add_column")

    user_fk = _find_search_history_user_fk(inspector)
    ondelete = (user_fk.get("options") or {}).get("ondelete") if user_fk else None
    if user_fk and ondelete != "CASCADE":
        if dialect_name == "sqlite":
            print("[MIGRATION] SQLite detected; skipping FK alter for search_history")
            return
        constraint_name = user_fk.get("name") or USER_FK_FALLBACK_NAME
        op.drop_constraint(constraint_name, "search_history", type_="foreignkey")
        op.create_foreign_key(
            constraint_name, "search_history", "user", ["user_id"], ["id"], ondelete="CASCADE"
        )
        print(f"[MIGRATION] search_history.{constraint_name} ondelete set to CASCADE")
    elif user_fk:
        print("[MIGRATION] search_history ondelete already CASCADE; skipping FK update")


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("search_history")}
    if "duration_ms" in columns:
        op.drop_column("search_history", "duration_ms")

    user_fk = _find_search_history_user_fk(inspector)
    ondelete = (user_fk.get("options") or {}).get("ondelete") if user_fk else None
    if user_fk and ondelete == "CASCADE":
        constraint_name = user_fk.get("name") or USER_FK_FALLBACK_NAME
        op.drop_constraint(constraint_name, "search_history", type_="foreignkey")
        op.create_foreign_key(
            constraint_name, "search_history", "user", ["user_id"], ["id"], ondelete=None
        )
