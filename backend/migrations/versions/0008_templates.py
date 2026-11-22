"""M7 templates: templates, immutable versions/layouts/assets, applications, favorites, usage

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _base(org_nullable: bool):
    return [
        sa.Column('organization_id', sa.UUID(), nullable=org_nullable),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    ]


def _fk(col, table):
    return sa.ForeignKeyConstraint([col], [f'{table}.id'], ondelete='CASCADE')


def upgrade() -> None:
    op.create_table('templates',
        sa.Column('system_key', sa.String(80), nullable=True), sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''), sa.Column('owner_type', sa.String(12), nullable=False),
        sa.Column('owner_id', sa.UUID(), nullable=True), sa.Column('visibility', sa.String(12), nullable=False),
        sa.Column('category', sa.String(20), nullable=False, server_default='general'),
        sa.Column('style_tags', JSONB, nullable=False, server_default='[]'), sa.Column('layout_tags', JSONB, nullable=False, server_default='[]'),
        sa.Column('status', sa.String(12), nullable=False, server_default='draft'), sa.Column('current_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('thumbnail_asset_id', sa.UUID(), nullable=True), sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('system_key'),
        sa.CheckConstraint("owner_type in ('system','organization','personal')", name='ck_templates_owner_type'),
        sa.CheckConstraint("status in ('draft','published','archived')", name='ck_templates_status'),
        sa.CheckConstraint("(owner_type = 'system') = (organization_id is null)", name='ck_templates_system_has_no_org'),
        *_base(True))
    op.create_index('ix_templates_organization_id', 'templates', ['organization_id'])
    op.create_index('ix_templates_org_status', 'templates', ['organization_id', 'status'])
    op.create_index('ix_templates_owner', 'templates', ['owner_type', 'owner_id'])

    op.create_table('template_versions',
        sa.Column('template_id', sa.UUID(), nullable=False), sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False, server_default='1'), sa.Column('definition', JSONB, nullable=False),
        sa.Column('brand_constraints', JSONB, nullable=False, server_default='{}'), sa.Column('application_modes', JSONB, nullable=False, server_default='[]'),
        sa.Column('change_note', sa.String(400), nullable=False, server_default=''), sa.Column('created_by', sa.UUID(), nullable=True),
        _fk('template_id', 'templates'), sa.UniqueConstraint('template_id', 'version'), *_base(True))
    op.create_index('ix_template_versions_organization_id', 'template_versions', ['organization_id'])
    op.create_index('ix_template_versions_template_id', 'template_versions', ['template_id'])

    op.create_table('template_layouts',
        sa.Column('template_version_id', sa.UUID(), nullable=False), sa.Column('format_id', sa.String(80), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False), sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('aspect_ratio', sa.Float(), nullable=False), sa.Column('layout_definition', JSONB, nullable=False),
        _fk('template_version_id', 'template_versions'), sa.UniqueConstraint('template_version_id', 'format_id'), *_base(True))
    op.create_index('ix_template_layouts_organization_id', 'template_layouts', ['organization_id'])
    op.create_index('ix_template_layouts_version', 'template_layouts', ['template_version_id'])
    op.create_index('ix_template_layouts_size', 'template_layouts', ['width', 'height'])

    op.create_table('template_assets',
        sa.Column('template_version_id', sa.UUID(), nullable=False), sa.Column('asset_id', sa.UUID(), nullable=False),
        sa.Column('role', sa.String(30), nullable=False), sa.Column('asset_type', sa.String(20), nullable=False, server_default='image'),
        sa.Column('ownership', sa.String(20), nullable=False, server_default='organization'), sa.Column('reusable', sa.Boolean(), nullable=False, server_default=sa.true()),
        _fk('template_version_id', 'template_versions'), *_base(True))
    op.create_index('ix_template_assets_organization_id', 'template_assets', ['organization_id'])
    op.create_index('ix_template_assets_version', 'template_assets', ['template_version_id'])

    op.create_table('template_applications',
        sa.Column('template_id', sa.UUID(), nullable=False), sa.Column('template_version_id', sa.UUID(), nullable=False),
        sa.Column('document_id', sa.UUID(), nullable=True), sa.Column('campaign_id', sa.UUID(), nullable=True),
        sa.Column('format_id', sa.String(80), nullable=False), sa.Column('source_revision', sa.Integer(), nullable=True),
        sa.Column('application_mode', sa.String(14), nullable=False), sa.Column('status', sa.String(12), nullable=False, server_default='previewed'),
        sa.Column('mapping_result', JSONB, nullable=False, server_default='{}'), sa.Column('preserved_layer_ids', JSONB, nullable=False, server_default='[]'),
        sa.Column('replaced_layer_ids', JSONB, nullable=False, server_default='[]'), sa.Column('created_layer_ids', JSONB, nullable=False, server_default='[]'),
        sa.Column('warnings', JSONB, nullable=False, server_default='[]'), sa.Column('options', JSONB, nullable=False, server_default='{}'),
        sa.Column('source_document', JSONB, nullable=True), sa.Column('candidate_document', JSONB, nullable=False),
        sa.Column('preview_before_asset_id', sa.UUID(), nullable=True), sa.Column('preview_after_asset_id', sa.UUID(), nullable=True),
        sa.Column('applied_revision', sa.Integer(), nullable=True), sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        _fk('template_id', 'templates'), _fk('template_version_id', 'template_versions'), _fk('document_id', 'creative_documents'),
        sa.CheckConstraint("application_mode in ('layout_only','layout_style','full')", name='ck_template_applications_mode'),
        *_base(False))
    op.create_index('ix_template_applications_organization_id', 'template_applications', ['organization_id'])
    op.create_index('ix_template_applications_document', 'template_applications', ['document_id', 'created_at'])
    op.create_index('ix_template_applications_template', 'template_applications', ['template_id'])

    op.create_table('template_favorites',
        sa.Column('template_id', sa.UUID(), nullable=False), sa.Column('user_id', sa.UUID(), nullable=False),
        _fk('template_id', 'templates'), _fk('user_id', 'users'), sa.UniqueConstraint('template_id', 'user_id'), *_base(False))
    op.create_index('ix_template_favorites_organization_id', 'template_favorites', ['organization_id'])
    op.create_index('ix_template_favorites_user', 'template_favorites', ['user_id'])

    op.create_table('template_usages',
        sa.Column('template_id', sa.UUID(), nullable=False), sa.Column('user_id', sa.UUID(), nullable=True), sa.Column('document_id', sa.UUID(), nullable=True),
        sa.Column('application_mode', sa.String(14), nullable=False), _fk('template_id', 'templates'), *_base(False))
    op.create_index('ix_template_usages_organization_id', 'template_usages', ['organization_id'])
    op.create_index('ix_template_usages_template', 'template_usages', ['template_id', 'created_at'])

    cur = "current_setting('app.current_org', true)"
    sysadmin = "current_setting('app.system_admin', true) = 'on'"
    for t in ('templates', 'template_versions', 'template_layouts', 'template_assets'):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        # system rows (organization_id NULL) are readable by everyone, but only writable when the seeder raises app.system_admin
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING (organization_id IS NULL OR organization_id::text = {cur}) "
                   f"WITH CHECK (organization_id::text = {cur} OR (organization_id IS NULL AND {sysadmin}))")
    for t in ('template_applications', 'template_favorites', 'template_usages'):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING (organization_id::text = {cur}) WITH CHECK (organization_id::text = {cur})")
    # versions, layouts and template assets are immutable
    op.execute("REVOKE UPDATE, DELETE ON template_versions, template_layouts, template_assets FROM adcreative_app")
    for t in ('template_versions', 'template_layouts', 'template_assets'):
        op.execute(f"CREATE TRIGGER {t}_immutable BEFORE UPDATE ON {t} FOR EACH ROW EXECUTE FUNCTION forbid_update()")


def downgrade() -> None:
    for t in ('template_usages', 'template_favorites', 'template_applications', 'template_assets', 'template_layouts', 'template_versions', 'templates'):
        op.drop_table(t)
