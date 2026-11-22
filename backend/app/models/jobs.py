import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgOwned

JOB_STATUSES = ["pending", "analyzing", "briefing", "generating_assets", "composing", "rendering", "validating", "completed",
                "partially_completed", "failed", "cancelled"]
TERMINAL = {"completed", "partially_completed", "failed", "cancelled"}


class GenerationJob(OrgOwned, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key"),)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="banners")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage_detail: Mapped[str] = mapped_column(String(200), default="")
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    results: Mapped[list] = mapped_column(JSONB, default=list)   # one entry per (concept, size) unit
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    dead_lettered: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExportJob(OrgOwned, Base):
    __tablename__ = "export_jobs"
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | processing | completed | failed
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    manifest: Mapped[dict] = mapped_column(JSONB, default=dict)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class FormatPreset(OrgOwned, Base):
    __tablename__ = "format_presets"
    name: Mapped[str] = mapped_column(String(100))
    format_ids: Mapped[list] = mapped_column(JSONB, default=list)
    custom_sizes: Mapped[list] = mapped_column(JSONB, default=list)
