"""quality engine

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('organizations', sa.Column('quality_policy', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'))
    op.add_column('generated_creatives', sa.Column('quality_score', sa.Integer(), nullable=True))
    op.add_column('generated_creatives', sa.Column('quality_status', sa.String(length=10), nullable=True))
    op.add_column('generated_creatives', sa.Column('quality_spec_version', sa.Integer(), nullable=True))
    op.create_table('quality_reports',
        sa.Column('creative_id', sa.UUID(), nullable=False),
        sa.Column('campaign_id', sa.UUID(), nullable=False),
        sa.Column('spec_version', sa.Integer(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('report', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('original_spec', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['creative_id'], ['generated_creatives.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index(op.f('ix_quality_reports_campaign_id'), 'quality_reports', ['campaign_id'])
    op.create_index(op.f('ix_quality_reports_creative_id'), 'quality_reports', ['creative_id'])
    op.create_index(op.f('ix_quality_reports_organization_id'), 'quality_reports', ['organization_id'])
    op.execute("ALTER TABLE quality_reports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE quality_reports FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON quality_reports USING (organization_id::text = current_setting('app.current_org', true)) "
               "WITH CHECK (organization_id::text = current_setting('app.current_org', true))")


def downgrade() -> None:
    op.drop_table('quality_reports')
    op.drop_column('generated_creatives', 'quality_spec_version')
    op.drop_column('generated_creatives', 'quality_status')
    op.drop_column('generated_creatives', 'quality_score')
    op.drop_column('organizations', 'quality_policy')
