import hashlib
import io
import uuid
from datetime import datetime, timezone

from PIL import Image
from sqlalchemy.orm import Session

from app.brand.analysis import dominant_colors
from app.models import BrandProfile, Campaign, Product, ProductAsset
from app.repositories.org_repo import OrgRepo
from app.scraping.extractor import extract_product
from app.scraping.fetcher import FetchError, SafeFetcher, UnsafeURLError
from app.storage.base import Storage

HTML_MIME = ("text/html", "application/xhtml+xml")
IMAGE_MIME = ("image/jpeg", "image/png", "image/webp")
MAX_HTML, MAX_IMAGE = 3_000_000, 8_000_000
MAX_PIXELS, MIN_SIDE = 40_000_000, 32
FORMAT_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # decompression-bomb guard (Pillow raises above 2x this)


class ImageRejected(Exception):
    pass


def make_fetcher() -> SafeFetcher:  # indirection so tests can inject a mock transport
    return SafeFetcher()


def inspect_image(data: bytes) -> tuple[str, int, int]:
    """Validate by decoding, never by trusting extension/header. Returns (mime, w, h)."""
    if len(data) > MAX_IMAGE:
        raise ImageRejected("Image too large")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt, (w, h) = im.format, im.size
            if fmt not in FORMAT_MIME:
                raise ImageRejected(f"Unsupported image format {fmt}")
            if w * h > MAX_PIXELS:
                raise ImageRejected("Image dimensions too large")
            im.load()
    except ImageRejected:
        raise
    except Exception as e:
        raise ImageRejected("Not a valid image") from e
    if min(w, h) < MIN_SIDE:
        raise ImageRejected("Image too small")
    return FORMAT_MIME[fmt], w, h


def store_asset(db: Session, storage: Storage, org_id: uuid.UUID, product: Product, data: bytes, kind: str,
                source_url: str | None, origin: str) -> ProductAsset | None:
    mime, w, h = inspect_image(data)
    digest = hashlib.sha256(data).hexdigest()
    repo = OrgRepo(db, ProductAsset, org_id)
    for a in repo.list(product_id=product.id):
        if a.content_hash == digest and a.kind == kind:
            return None  # duplicate
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime]
    key = f"{org_id}/products/{product.id}/{digest[:16]}.{ext}"
    storage.put(key, data, mime)
    return repo.add(product_id=product.id, kind=kind, storage_key=key, content_hash=digest, mime=mime,
                    width=w, height=h, source_url=source_url, origin=origin)


def analyze_campaign(db: Session, org_id: uuid.UUID, campaign: Campaign, fetcher: SafeFetcher, storage: Storage) -> Product:
    if not campaign.product_url:
        raise ValueError("Campaign has no product URL")
    warnings: list[str] = []
    page = fetcher.fetch(campaign.product_url, allowed_mime=HTML_MIME, max_bytes=MAX_HTML)
    ex = extract_product(page.body.decode("utf-8", errors="replace"), page.url)

    products = OrgRepo(db, Product, org_id)
    product = products.list(campaign_id=campaign.id)
    product = product[0] if product else products.add(campaign_id=campaign.id)
    data = ex.model_dump()
    data["user_description"] = campaign.description  # user-provided text is kept separate from scraped text
    product.source_url, product.data, product.extracted, product.user_edited = campaign.product_url, data, ex.model_dump(), False
    db.flush()

    logo_bytes = first_bytes = None
    for url in ex.image_urls[:5]:
        try:
            r = fetcher.fetch(url, allowed_mime=IMAGE_MIME, max_bytes=MAX_IMAGE)
            if store_asset(db, storage, org_id, product, r.body, "image", url, "extracted") is not None:
                first_bytes = first_bytes or r.body
        except (FetchError, UnsafeURLError, ImageRejected) as e:
            warnings.append(f"image skipped ({url[:80]}): {e}")
    if ex.logo_url:
        try:
            r = fetcher.fetch(ex.logo_url, allowed_mime=IMAGE_MIME, max_bytes=MAX_IMAGE)
            store_asset(db, storage, org_id, product, r.body, "logo", ex.logo_url, "extracted")
            logo_bytes = r.body
        except (FetchError, UnsafeURLError, ImageRejected) as e:
            warnings.append(f"logo skipped: {e}")

    palette = dominant_colors(logo_bytes or first_bytes) if (logo_bytes or first_bytes) else []
    detected = {"name": ex.brand_name, "colors": palette,
                "color_source": "logo" if logo_bytes else ("product-image" if first_bytes else None),
                "theme_color": ex.theme_color}
    brand_id = campaign.brief.get("brand_profile_id")
    brand = OrgRepo(db, BrandProfile, org_id).get(uuid.UUID(brand_id)) if brand_id else None
    if brand:
        brand.detected = detected
    else:
        brand = OrgRepo(db, BrandProfile, org_id).add(name=ex.brand_name or ex.title or "Untitled brand", detected=detected)
        campaign.brief = {**campaign.brief, "brand_profile_id": str(brand.id)}
    if not ex.title:
        warnings.append("No product title found; please fill it in")
    if not ex.image_urls:
        warnings.append("No product images found; upload one to continue")
    product.extraction_meta = {"final_url": page.url, "fetched_at": datetime.now(timezone.utc).isoformat(),
                               "sources": ex.sources, "warnings": warnings}
    campaign.status = "analyzed"
    db.flush()
    return product
