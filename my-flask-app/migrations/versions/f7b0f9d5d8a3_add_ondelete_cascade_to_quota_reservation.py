"""add ON DELETE CASCADE to quota_reservation.user_id FK

Revision ID: f7b0f9d5d8a3
Revises: e2a93bb2c45f
Create Date: 2026-01-11 21:45:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f7b0f9d5d8a3"
down_revision = "e2a93bb2c45f"
branch_labels = None
depends_on = None


def _get_existing_fk_names(bind):
    inspector = sa.inspect(bind)
    names = []
    try:
        fks = inspector.get_foreign_keys("quota_reservation")
    except Exception:
        return names
    for fk in fks:
        if fk.get("referred_table") == "user" and fk.get("constrained_columns") == ["user_id"]:
            if fk.get("name"):
                names.append(fk.get("name"))
    return names


def _get_user_fk(inspector):
    fks = inspector.get_foreign_keys("quota_reservation")
    return next(
        (
            fk
            for fk in fks
            if fk.get("referred_table") == "user" and fk.get("constrained_columns") == ["user_id"]
        ),
        None,
    )


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect_name = getattr(bind.dialect, "name", "")
    if not inspector.has_table("quota_reservation"):
        print("[MIGRATION] quota_reservation missing; skipping FK update")
        return
    user_fk = _get_user_fk(inspector)
    ondelete = (user_fk.get("options") or {}).get("ondelete") if user_fk else None
    if ondelete == "CASCADE":
        print("[MIGRATION] quota_reservation ondelete already CASCADE; skipping FK update")
        return
    if dialect_name == "sqlite":
        print("[MIGRATION] SQLite detected; skipping quota_reservation FK alter")
        return
    fk_names = _get_existing_fk_names(bind)
    with op.batch_alter_table("quota_reservation") as batch_op:
        for fk_name in fk_names:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_quota_reservation_user_id_user"),
            "user",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
    print("[MIGRATION] quota_reservation ondelete set to CASCADE")


def downgrade():
    bind = op.get_bind()
    fk_names = _get_existing_fk_names(bind)
    with op.batch_alter_table("quota_reservation") as batch_op:
        for fk_name in fk_names:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_quota_reservation_user_id_user"),
            "user",
            ["user_id"],
            ["id"],
        )
