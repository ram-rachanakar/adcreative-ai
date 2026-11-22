"""M7 editor persistence. One CreativeDocument per GeneratedCreative (= one per banner format), so formats are independent by construction."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgOwned

EDITOR_SCHEMA_VERSION = 2


class CreativeDocument(OrgOwned, Base):
    __tablename__ = "creative_documents"
    __table_args__ = (UniqueConstraint("creative_id"),)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    creative_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("generated_creatives.id", ondelete="CASCADE"))
    format_id: Mapped[str] = mapped_column(String(80))            # registry id if known, else "WxH"
    name: Mapped[str] = mapped_column(String(200), default="")
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=EDITOR_SCHEMA_VERSION)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    rendered_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)   # revision the creative's render image reflects
    status: Mapped[str] = mapped_column(String(20), default="draft")
    document: Mapped[dict] = mapped_column(JSONB)                  # CreativeSpec dump (canvas/background/elements/meta)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_version_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CreativeVersion(OrgOwned, Base):
    """Immutable snapshot (no UPDATE/DELETE grant, UPDATE trigger)."""
    __tablename__ = "creative_versions"
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("creative_documents.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    version_name: Mapped[str] = mapped_column(String(200), default="")
    change_summary: Mapped[str] = mapped_column(String(400), default="")
    kind: Mapped[str] = mapped_column(String(20), default="manual")   # manual | auto | ai_accept | restore_point | initial
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class EditOperation(OrgOwned, Base):
    """Append-only operation log. before/after hold only the affected parts (layers by id, order, background)."""
    __tablename__ = "edit_operations"
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("creative_documents.id", ondelete="CASCADE"), index=True)
    revision_before: Mapped[int] = mapped_column(Integer)
    revision_after: Mapped[int] = mapped_column(Integer)
    operation_type: Mapped[str] = mapped_column(String(40))
    target_layer_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    before: Mapped[dict] = mapped_column(JSONB)
    after: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # server-side undo bookkeeping: an undo/redo op points at the op it reverses/reapplies
    reverts_operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class RegenerationJob(OrgOwned, Base):
    __tablename__ = "regeneration_jobs"
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("creative_documents.id", ondelete="CASCADE"), index=True)
    target_layer_id: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str] = mapped_column(String(20))          # background | image | text | shape
    prompt: Mapped[str] = mapped_column(Text)
    source_revision: Mapped[int] = mapped_column(Integer)
    source_layer: Mapped[dict] = mapped_column(JSONB, default=dict)   # layer as it was when requested (for staleness checks)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|ready|accepted|rejected|failed|cancelled
    candidates: Mapped[list] = mapped_column(JSONB, default=list)     # [{asset_id}] or [{text}]
    candidate_asset_ids: Mapped[list] = mapped_column(JSONB, default=list)
    accepted_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    usage_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RenderJob(OrgOwned, Base):
    __tablename__ = "render_jobs"
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("creative_documents.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    output_format: Mapped[str] = mapped_column(String(10), default="png")
    quality: Mapped[int] = mapped_column(Integer, default=92)
    transparent: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|processing|completed|failed
    output_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
