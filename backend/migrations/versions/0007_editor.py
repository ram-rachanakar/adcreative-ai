"""M7 editor: documents, versions, operations, regeneration + render jobs

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _base():
    return [
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    ]


def _doc_fk():
    return sa.ForeignKeyConstraint(['document_id'], ['creative_documents.id'], ondelete='CASCADE')


def upgrade() -> None:
    op.create_table('creative_documents',
        sa.Column('campaign_id', sa.UUID(), nullable=False), sa.Column('creative_id', sa.UUID(), nullable=False),
        sa.Column('format_id', sa.String(80), nullable=False), sa.Column('name', sa.String(200), nullable=False, server_default=''),
        sa.Column('width', sa.Integer(), nullable=False), sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False, server_default='2'), sa.Column('revision', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('rendered_revision', sa.Integer(), nullable=True), sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
        sa.Column('document', JSONB, nullable=False), sa.Column('created_by', sa.UUID(), nullable=True), sa.Column('updated_by', sa.UUID(), nullable=True),
        sa.Column('last_version_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['creative_id'], ['generated_creatives.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('creative_id'), *_base())
    op.create_index('ix_creative_documents_organization_id', 'creative_documents', ['organization_id'])
    op.create_index('ix_creative_documents_campaign_id', 'creative_documents', ['campaign_id'])
    op.create_index('ix_creative_documents_org_campaign_format', 'creative_documents', ['organization_id', 'campaign_id', 'format_id'])

    op.create_table('creative_versions',
        sa.Column('document_id', sa.UUID(), nullable=False), sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('snapshot', JSONB, nullable=False), sa.Column('version_name', sa.String(200), nullable=False, server_default=''),
        sa.Column('change_summary', sa.String(400), nullable=False, server_default=''), sa.Column('kind', sa.String(20), nullable=False, server_default='manual'),
        sa.Column('created_by', sa.UUID(), nullable=True), _doc_fk(), *_base())
    op.create_index('ix_creative_versions_organization_id', 'creative_versions', ['organization_id'])
    op.create_index('ix_creative_versions_document_created', 'creative_versions', ['document_id', 'created_at'])

    op.create_table('edit_operations',
        sa.Column('document_id', sa.UUID(), nullable=False), sa.Column('revision_before', sa.Integer(), nullable=False),
        sa.Column('revision_after', sa.Integer(), nullable=False), sa.Column('operation_type', sa.String(40), nullable=False),
        sa.Column('target_layer_id', sa.String(60), nullable=True), sa.Column('before', JSONB, nullable=False), sa.Column('after', JSONB, nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True), sa.Column('reverts_operation_id', sa.UUID(), nullable=True), _doc_fk(), *_base())
    op.create_index('ix_edit_operations_organization_id', 'edit_operations', ['organization_id'])
    op.create_index('ix_edit_operations_document_revision', 'edit_operations', ['document_id', 'revision_after'])

    op.create_table('regeneration_jobs',
        sa.Column('document_id', sa.UUID(), nullable=False), sa.Column('target_layer_id', sa.String(60), nullable=False),
        sa.Column('target_type', sa.String(20), nullable=False), sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('source_revision', sa.Integer(), nullable=False), sa.Column('source_layer', JSONB, nullable=False, server_default='{}'),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'), sa.Column('candidates', JSONB, nullable=False, server_default='[]'),
        sa.Column('candidate_asset_ids', JSONB, nullable=False, server_default='[]'), sa.Column('accepted_asset_id', sa.UUID(), nullable=True),
        sa.Column('provider', sa.String(40), nullable=True), sa.Column('model', sa.String(100), nullable=True),
        sa.Column('usage_metadata', JSONB, nullable=False, server_default='{}'), sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column('requested_by', sa.UUID(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True), _doc_fk(), *_base())
    op.create_index('ix_regeneration_jobs_organization_id', 'regeneration_jobs', ['organization_id'])
    op.create_index('ix_regeneration_jobs_document_id', 'regeneration_jobs', ['document_id'])

    op.create_table('render_jobs',
        sa.Column('document_id', sa.UUID(), nullable=False), sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('output_format', sa.String(10), nullable=False, server_default='png'), sa.Column('quality', sa.Integer(), nullable=False, server_default='92'),
        sa.Column('transparent', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'), sa.Column('output_asset_id', sa.UUID(), nullable=True),
        sa.Column('warnings', JSONB, nullable=False, server_default='[]'), sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True), _doc_fk(), *_base())
    op.create_index('ix_render_jobs_organization_id', 'render_jobs', ['organization_id'])
    op.create_index('ix_render_jobs_document_id', 'render_jobs', ['document_id'])

    for t in ('creative_documents', 'creative_versions', 'edit_operations', 'regeneration_jobs', 'render_jobs'):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING (organization_id::text = current_setting('app.current_org', true)) "
                   f"WITH CHECK (organization_id::text = current_setting('app.current_org', true))")
    # immutability: the runtime role can never change or delete history; a trigger blocks UPDATE for every role
    op.execute("REVOKE UPDATE, DELETE ON creative_versions, edit_operations FROM adcreative_app")
    op.execute("CREATE FUNCTION forbid_update() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME; END $$ LANGUAGE plpgsql")
    for t in ('creative_versions', 'edit_operations'):
        op.execute(f"CREATE TRIGGER {t}_immutable BEFORE UPDATE ON {t} FOR EACH ROW EXECUTE FUNCTION forbid_update()")


def downgrade() -> None:
    for t in ('render_jobs', 'regeneration_jobs', 'edit_operations', 'creative_versions', 'creative_documents'):
        op.drop_table(t)
    op.execute("DROP FUNCTION IF EXISTS forbid_update()")
