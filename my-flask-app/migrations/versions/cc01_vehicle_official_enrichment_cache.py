"""Comparison V2: per-vehicle official Level 2 enrichment cache.

Revision ID: cc01_vehicle_enrichment_cache
Revises: bb03_research_260425
Create Date: 2026-09-30 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'cc01_vehicle_enrichment_cache'
down_revision = 'bb03_research_260425'
branch_labels = None
depends_on = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        return postgresql.JSONB()
    return sa.Text()


def upgrade():
    op.create_table(
        'vehicle_official_enrichment_cache',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('cache_key', sa.String(length=64), nullable=False),
        sa.Column('variant_identity_key', sa.String(length=64), nullable=False),
        sa.Column('enrichment_contract_version', sa.String(length=64), nullable=False),
        sa.Column('source_registry_version', sa.String(length=64), nullable=False),
        sa.Column('enrichment_model', sa.String(length=64), nullable=False),
        sa.Column('payload', _json_type(), nullable=False),
        sa.Column('technical_observed_at', sa.DateTime(), nullable=True),
        sa.Column('price_observed_at', sa.DateTime(), nullable=True),
        sa.Column('warranty_observed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('cache_key', name='uq_vehicle_official_enrichment_cache_key'),
    )
    op.create_index(
        'ix_vehicle_official_enrichment_cache_variant',
        'vehicle_official_enrichment_cache',
        ['variant_identity_key'],
    )


def downgrade():
    op.drop_index('ix_vehicle_official_enrichment_cache_variant', table_name='vehicle_official_enrichment_cache')
    op.drop_table('vehicle_official_enrichment_cache')
