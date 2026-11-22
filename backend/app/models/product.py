import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgOwned


class Product(OrgOwned, Base):
    __tablename__ = "products"
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), unique=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)             # current (possibly user-edited) fields
    extracted: Mapped[dict] = mapped_column(JSONB, default=dict)        # original machine extraction, kept for audit
    extraction_meta: Mapped[dict] = mapped_column(JSONB, default=dict)  # final url, fetched_at, field sources, warnings
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False)


class ProductAsset(OrgOwned, Base):
    __tablename__ = "product_assets"
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # image | logo
    storage_key: Mapped[str] = mapped_column(String(512))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    mime: Mapped[str] = mapped_column(String(50))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    origin: Mapped[str] = mapped_column(String(20), default="extracted")  # extracted | upload


class BrandProfile(OrgOwned, Base):
    __tablename__ = "brand_profiles"
    name: Mapped[str] = mapped_column(String(200))
    detected: Mapped[dict] = mapped_column(JSONB, default=dict)   # AI/deterministic detection
    overrides: Mapped[dict] = mapped_column(JSONB, default=dict)  # user-defined wins over detected
    guidelines: Mapped[str] = mapped_column(Text, default="")
