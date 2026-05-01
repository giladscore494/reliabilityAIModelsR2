"""add service_price_benchmark_item table

Revision ID: a1b2c3d4e5f6
Revises: 7f1b3b0a2c4d
Create Date: 2026-02-07 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = '7f1b3b0a2c4d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'service_price_benchmark_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('canonical_code', sa.String(length=64), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=True),
        sa.Column('price_ils', sa.Integer(), nullable=True),
        sa.Column('parts_ils', sa.Integer(), nullable=True),
        sa.Column('labor_ils', sa.Integer(), nullable=True),
        sa.Column('qty', sa.Integer(), nullable=True),
        sa.Column('make', sa.String(length=100), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=True),
        sa.Column('year_bucket', sa.String(length=16), nullable=True),
        sa.Column('mileage_bucket', sa.String(length=16), nullable=True),
        sa.Column('region', sa.String(length=64), nullable=True),
        sa.Column('garage_type', sa.String(length=16), nullable=True),
        sa.Column('invoice_month', sa.String(length=7), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_service_price_benchmark_item_canonical_code', 'service_price_benchmark_item', ['canonical_code'])
    op.create_index('ix_service_price_benchmark_item_category', 'service_price_benchmark_item', ['category'])
    op.create_index('ix_service_price_benchmark_item_price_ils', 'service_price_benchmark_item', ['price_ils'])
    op.create_index('ix_service_price_benchmark_item_make', 'service_price_benchmark_item', ['make'])
    op.create_index('ix_service_price_benchmark_item_model', 'service_price_benchmark_item', ['model'])
    op.create_index('ix_service_price_benchmark_item_year_bucket', 'service_price_benchmark_item', ['year_bucket'])
    op.create_index('ix_service_price_benchmark_item_mileage_bucket', 'service_price_benchmark_item', ['mileage_bucket'])
    op.create_index('ix_service_price_benchmark_item_region', 'service_price_benchmark_item', ['region'])
    op.create_index('ix_service_price_benchmark_item_garage_type', 'service_price_benchmark_item', ['garage_type'])
    op.create_index('ix_service_price_benchmark_item_invoice_month', 'service_price_benchmark_item', ['invoice_month'])
    op.create_index('ix_service_price_benchmark_item_created_at', 'service_price_benchmark_item', ['created_at'])
    op.create_index('ix_benchmark_code_make_model', 'service_price_benchmark_item', ['canonical_code', 'make', 'model'])


def downgrade():
    op.drop_index('ix_benchmark_code_make_model', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_created_at', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_invoice_month', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_garage_type', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_region', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_mileage_bucket', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_year_bucket', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_model', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_make', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_price_ils', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_category', table_name='service_price_benchmark_item')
    op.drop_index('ix_service_price_benchmark_item_canonical_code', table_name='service_price_benchmark_item')
    op.drop_table('service_price_benchmark_item')
