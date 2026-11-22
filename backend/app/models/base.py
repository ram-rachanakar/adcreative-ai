import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrgOwned(IdMixin):
    """Mixin for every tenant-owned table. RLS policies are attached in migrations."""
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


ORG_OWNED_TABLES = ["workspaces", "projects", "campaigns", "audit_logs", "products", "product_assets", "brand_profiles", "creative_concepts", "creative_briefs", "usage_records", "creative_assets", "generated_creatives", "quality_reports", "generation_jobs", "export_jobs", "format_presets", "creative_documents", "creative_versions", "edit_operations", "regeneration_jobs", "render_jobs"]
