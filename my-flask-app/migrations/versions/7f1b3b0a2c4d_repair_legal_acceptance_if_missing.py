"""repair legal acceptance table if missing

Revision ID: 7f1b3b0a2c4d
Revises: 1e6a1e5f7a9b
Create Date: 2026-01-15 21:35:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "7f1b3b0a2c4d"
down_revision = "1e6a1e5f7a9b"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("legal_acceptance"):
        op.create_table(
            "legal_acceptance",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
            sa.Column("terms_version", sa.String(length=32), nullable=False),
            sa.Column("privacy_version", sa.String(length=32), nullable=False),
            sa.Column("accepted_at", sa.DateTime(), nullable=False),
            sa.Column("accepted_ip", sa.String(length=64), nullable=False),
            sa.Column("accepted_user_agent", sa.String(length=512), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False, server_default=sa.text("'web'")),
            sa.UniqueConstraint("user_id", "terms_version", "privacy_version", name="uq_legal_acceptance_user_version"),
        )
        op.create_index(
            "ix_legal_acceptance_user_version",
            "legal_acceptance",
            ["user_id", "terms_version", "privacy_version"],
        )


def downgrade():
    pass
