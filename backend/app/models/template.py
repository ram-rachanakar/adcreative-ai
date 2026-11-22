"""M7 templates. Templates are reusable *blueprints* (semantic slots per format), never flattened banners.
System templates have organization_id NULL (readable by every org, writable only by the seeder via the app.system_admin flag);
personal/organization templates are tenant-owned. Versions are immutable (UPDATE trigger + no UPDATE/DELETE grant)."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin

TEMPLATE_TABLES = ["templates", "template_versions", "template_layouts", "template_assets", "template_applications", "template_favorites", "template_usages"]


class _TplBase(IdMixin):
    # NULL organization = system template (or rows hanging off one)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)


class Template(_TplBase, Base):
    __tablename__ = "templates"
    system_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)     # stable id of a built-in template (idempotent seeding)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_type: Mapped[str] = mapped_column(String(12))              # system | organization | personal
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)   # user id for personal, org id for organization
    visibility: Mapped[str] = mapped_column(String(12))              # system | organization | personal
    category: Mapped[str] = mapped_column(String(20), default="general")   # industry
    style_tags: Mapped[list] = mapped_column(JSONB, default=list)
    layout_tags: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(12), default="draft")       # draft | published | archived
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    thumbnail_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TemplateVersion(_TplBase, Base):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    definition: Mapped[dict] = mapped_column(JSONB)                  # palette/fonts/background defaults/notes (format layouts live in template_layouts)
    brand_constraints: Mapped[dict] = mapped_column(JSONB, default=dict)
    application_modes: Mapped[list] = mapped_column(JSONB, default=list)   # which of layout_only | layout_style | full are offered
    change_note: Mapped[str] = mapped_column(String(400), default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class TemplateLayout(_TplBase, Base):
    """One explicit layout per exact canvas size. Slots live inside layout_definition (they are only meaningful within a layout)."""
    __tablename__ = "template_layouts"
    __table_args__ = (UniqueConstraint("template_version_id", "format_id"),)
    template_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("template_versions.id", ondelete="CASCADE"), index=True)
    format_id: Mapped[str] = mapped_column(String(80))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    aspect_ratio: Mapped[float] = mapped_column(Float)
    layout_definition: Mapped[dict] = mapped_column(JSONB)


class TemplateAsset(_TplBase, Base):
    __tablename__ = "template_assets"
    template_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("template_versions.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(30))                    # background | decorative | thumbnail
    asset_type: Mapped[str] = mapped_column(String(20), default="image")
    ownership: Mapped[str] = mapped_column(String(20), default="organization")     # organization | system
    reusable: Mapped[bool] = mapped_column(Boolean, default=True)


class TemplateApplication(_TplBase, Base):
    """A previewed/applied template on one document. The candidate and the source are stored so a preview can be committed atomically and reverted."""
    __tablename__ = "template_applications"
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    template_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("template_versions.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("creative_documents.id", ondelete="CASCADE"), nullable=True, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    format_id: Mapped[str] = mapped_column(String(80))
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    application_mode: Mapped[str] = mapped_column(String(14))        # layout_only | layout_style | full
    status: Mapped[str] = mapped_column(String(12), default="previewed")   # previewed | applied | rejected | reverted | expired
    mapping_result: Mapped[dict] = mapped_column(JSONB, default=dict)
    preserved_layer_ids: Mapped[list] = mapped_column(JSONB, default=list)
    replaced_layer_ids: Mapped[list] = mapped_column(JSONB, default=list)
    created_layer_ids: Mapped[list] = mapped_column(JSONB, default=list)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)       # confirm_replace, slot_overrides
    source_document: Mapped[dict | None] = mapped_column(JSONB, nullable=True)     # exact pre-application spec (recoverability)
    candidate_document: Mapped[dict] = mapped_column(JSONB)
    preview_before_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    preview_after_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TemplateFavorite(_TplBase, Base):
    __tablename__ = "template_favorites"
    __table_args__ = (UniqueConstraint("template_id", "user_id"),)
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)


class TemplateUsage(_TplBase, Base):
    __tablename__ = "template_usages"
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    application_mode: Mapped[str] = mapped_column(String(14))
