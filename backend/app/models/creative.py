import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgOwned


class CreativeAsset(OrgOwned, Base):
    """Generated or derived image (background, cutout, render). Carries full provenance."""
    __tablename__ = "creative_assets"
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True, index=True)
    concept_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("creative_concepts.id", ondelete="SET NULL"), nullable=True)
    parent_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # e.g. cutout -> original product asset
    kind: Mapped[str] = mapped_column(String(20))  # background | cutout | render | candidate
    storage_key: Mapped[str] = mapped_column(String(512))
    content_hash: Mapped[str] = mapped_column(String(64))
    cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # org-scoped generation cache
    mime: Mapped[str] = mapped_column(String(50))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class GeneratedCreative(OrgOwned, Base):
    __tablename__ = "generated_creatives"
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    concept_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("creative_concepts.id", ondelete="CASCADE"), index=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSONB)          # the editable CreativeSpec
    spec_version: Mapped[int] = mapped_column(Integer, default=1)
    render_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    render_report: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="rendered")  # rendered | needs_attention
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    format_keys: Mapped[list] = mapped_column(JSONB, default=list)  # registry format ids this size serves
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)      # denormalised from the latest QualityReport
    quality_status: Mapped[str | None] = mapped_column(String(10), nullable=True)  # pass | warn | fail | None (never assessed)
    quality_spec_version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # spec_version the score refers to (stale if it differs)


class QualityReport(OrgOwned, Base):
    """Immutable history of quality assessments. One row per run; the latest is mirrored on the creative."""
    __tablename__ = "quality_reports"
    creative_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("generated_creatives.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    spec_version: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10))
    report: Mapped[dict] = mapped_column(JSONB)  # checks, category scores, vision, corrections, recommendations, policy used
    original_spec: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # spec before auto-correction (for one-click revert)
