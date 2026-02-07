"""add service price check tables and user counter

Revision ID: c8f1a2b3d4e5
Revises: ab08d93dadb7
Create Date: 2026-02-07 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'c8f1a2b3d4e5'
down_revision = 'ab08d93dadb7'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    # Add service_price_checks_count to user table
    user_columns = {col["name"] for col in inspector.get_columns("user")}
    if "service_price_checks_count" not in user_columns:
        try:
            op.add_column('user', sa.Column('service_price_checks_count', sa.Integer(), nullable=False, server_default='0'))
            print("[MIGRATION] service_price_checks_count column added to user table")
        except Exception as e:
            print(f"[MIGRATION] service_price_checks_count column add failed: {e}")
    else:
        print("[MIGRATION] service_price_checks_count already exists; skipping")

    # Create service_invoice table
    if inspector.has_table("service_invoice"):
        print("[MIGRATION] service_invoice already exists; skipping create_table")
    else:
        op.create_table('service_invoice',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('make', sa.String(length=100), nullable=True),
            sa.Column('model', sa.String(length=100), nullable=True),
            sa.Column('year', sa.Integer(), nullable=True),
            sa.Column('mileage', sa.Integer(), nullable=True),
            sa.Column('region', sa.String(length=64), nullable=True),
            sa.Column('garage_type', sa.String(length=16), nullable=True),
            sa.Column('invoice_date', sa.Date(), nullable=True),
            sa.Column('total_price_ils', sa.Integer(), nullable=True),
            sa.Column('currency', sa.String(length=8), nullable=False, server_default=sa.text("'ILS'")),
            sa.Column('parsed_json', sa.Text(), nullable=False),
            sa.Column('report_json', sa.Text(), nullable=False),
            sa.Column('duration_ms', sa.Integer(), nullable=True),
            sa.Column('request_id', sa.String(length=64), nullable=True),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        print("[MIGRATION] service_invoice table created")

    # Create service_invoice indexes
    try:
        indexes = {idx.get("name") for idx in inspector.get_indexes("service_invoice")}
    except Exception:
        indexes = set()

    if "ix_service_invoice_created_at" not in indexes:
        try:
            op.create_index('ix_service_invoice_created_at', 'service_invoice', ['created_at'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_service_invoice_created_at creation failed: {e}")

    if "ix_service_invoice_user_id" not in indexes:
        try:
            op.create_index('ix_service_invoice_user_id', 'service_invoice', ['user_id'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_service_invoice_user_id creation failed: {e}")

    if "ix_service_invoice_make" not in indexes:
        try:
            op.create_index('ix_service_invoice_make', 'service_invoice', ['make'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_service_invoice_make creation failed: {e}")

    if "ix_service_invoice_model" not in indexes:
        try:
            op.create_index('ix_service_invoice_model', 'service_invoice', ['model'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_service_invoice_model creation failed: {e}")

    if "ix_service_invoice_year" not in indexes:
        try:
            op.create_index('ix_service_invoice_year', 'service_invoice', ['year'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_service_invoice_year creation failed: {e}")

    if "ix_service_invoice_cohort" not in indexes:
        try:
            op.create_index('ix_service_invoice_cohort', 'service_invoice', ['make', 'model', 'year', 'region', 'garage_type'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_service_invoice_cohort creation failed: {e}")

    # Create service_invoice_item table
    if inspector.has_table("service_invoice_item"):
        print("[MIGRATION] service_invoice_item already exists; skipping create_table")
    else:
        op.create_table('service_invoice_item',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('invoice_id', sa.Integer(), nullable=False),
            sa.Column('canonical_code', sa.String(length=64), nullable=False),
            sa.Column('category', sa.String(length=32), nullable=True),
            sa.Column('raw_description', sa.String(length=512), nullable=True),
            sa.Column('price_ils', sa.Integer(), nullable=True),
            sa.Column('labor_ils', sa.Integer(), nullable=True),
            sa.Column('parts_ils', sa.Integer(), nullable=True),
            sa.Column('qty', sa.Integer(), nullable=True),
            sa.Column('confidence', sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(['invoice_id'], ['service_invoice.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        print("[MIGRATION] service_invoice_item table created")

    # Create service_invoice_item indexes
    try:
        indexes = {idx.get("name") for idx in inspector.get_indexes("service_invoice_item")}
    except Exception:
        indexes = set()

    if "ix_service_invoice_item_invoice_id" not in indexes:
        try:
            op.create_index('ix_service_invoice_item_invoice_id', 'service_invoice_item', ['invoice_id'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_service_invoice_item_invoice_id creation failed: {e}")

    if "ix_service_invoice_item_canonical_code" not in indexes:
        try:
            op.create_index('ix_service_invoice_item_canonical_code', 'service_invoice_item', ['canonical_code'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_service_invoice_item_canonical_code creation failed: {e}")

    if "ix_service_invoice_item_code_price" not in indexes:
        try:
            op.create_index('ix_service_invoice_item_code_price', 'service_invoice_item', ['canonical_code', 'price_ils'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_service_invoice_item_code_price creation failed: {e}")

    # Create legal_feature_acceptance table
    if inspector.has_table("legal_feature_acceptance"):
        print("[MIGRATION] legal_feature_acceptance already exists; skipping create_table")
    else:
        op.create_table('legal_feature_acceptance',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('feature_key', sa.String(length=64), nullable=False),
            sa.Column('version', sa.String(length=32), nullable=False),
            sa.Column('accepted_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'feature_key', 'version', name='uq_feature_acceptance')
        )
        print("[MIGRATION] legal_feature_acceptance table created")

    # Create legal_feature_acceptance indexes
    try:
        indexes = {idx.get("name") for idx in inspector.get_indexes("legal_feature_acceptance")}
    except Exception:
        indexes = set()

    if "ix_legal_feature_acceptance_user_id" not in indexes:
        try:
            op.create_index('ix_legal_feature_acceptance_user_id', 'legal_feature_acceptance', ['user_id'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_legal_feature_acceptance_user_id creation failed: {e}")

    if "ix_legal_feature_acceptance_feature_key" not in indexes:
        try:
            op.create_index('ix_legal_feature_acceptance_feature_key', 'legal_feature_acceptance', ['feature_key'], unique=False)
        except Exception as e:
            print(f"[MIGRATION] ix_legal_feature_acceptance_feature_key creation failed: {e}")


def downgrade():
    # Drop legal_feature_acceptance
    try:
        op.drop_index('ix_legal_feature_acceptance_feature_key', table_name='legal_feature_acceptance')
    except Exception:
        pass
    try:
        op.drop_index('ix_legal_feature_acceptance_user_id', table_name='legal_feature_acceptance')
    except Exception:
        pass
    try:
        op.drop_table('legal_feature_acceptance')
    except Exception:
        pass

    # Drop service_invoice_item
    try:
        op.drop_index('ix_service_invoice_item_code_price', table_name='service_invoice_item')
    except Exception:
        pass
    try:
        op.drop_index('ix_service_invoice_item_canonical_code', table_name='service_invoice_item')
    except Exception:
        pass
    try:
        op.drop_index('ix_service_invoice_item_invoice_id', table_name='service_invoice_item')
    except Exception:
        pass
    try:
        op.drop_table('service_invoice_item')
    except Exception:
        pass

    # Drop service_invoice
    try:
        op.drop_index('ix_service_invoice_cohort', table_name='service_invoice')
    except Exception:
        pass
    try:
        op.drop_index('ix_service_invoice_year', table_name='service_invoice')
    except Exception:
        pass
    try:
        op.drop_index('ix_service_invoice_model', table_name='service_invoice')
    except Exception:
        pass
    try:
        op.drop_index('ix_service_invoice_make', table_name='service_invoice')
    except Exception:
        pass
    try:
        op.drop_index('ix_service_invoice_user_id', table_name='service_invoice')
    except Exception:
        pass
    try:
        op.drop_index('ix_service_invoice_created_at', table_name='service_invoice')
    except Exception:
        pass
    try:
        op.drop_table('service_invoice')
    except Exception:
        pass

    # Drop user column
    try:
        op.drop_column('user', 'service_price_checks_count')
    except Exception:
        pass
