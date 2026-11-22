"""Export: re-encode stored renders (never resize), validate every produced file, bundle with a manifest. PNG/JPEG/WebP/SVG."""
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.etree import ElementTree

from PIL import Image
from sqlalchemy.orm import Session

from app.creative import formats
from app.creative.service import make_resolver
from app.models import Campaign, CreativeAsset, CreativeConcept, ExportJob, GeneratedCreative
from app.orchestration import factories
from app.rendering.spec import CreativeSpec
from app.rendering.svg import build_svg, render_spec
from app.repositories.org_repo import OrgRepo
from app.storage.base import Storage

SUPPORTED = {"png": ("image/png", "png"), "jpeg": ("image/jpeg", "jpg"), "webp": ("image/webp", "webp"), "svg": ("image/svg+xml", "svg")}
MAX_ITEMS = 120
TRANSPARENT_FORMATS = {"png", "webp", "svg"}   # JPEG has no alpha channel


@dataclass
class Encoded:
    data: bytes
    mime: str
    ext: str
    quality: int | None = None
    warnings: list[str] = field(default_factory=list)


def slug(text: str, fallback: str = "item") -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or fallback


def encode(png: bytes, fmt: str, quality: int, max_kb: int | None = None, svg: bytes | None = None, transparent: bool = False) -> Encoded:
    mime, ext = SUPPORTED[fmt]
    warnings: list[str] = []
    if fmt == "svg":
        data = svg or b""
        if max_kb and len(data) > max_kb * 1024:
            warnings.append(f"SVG is {len(data) // 1024} KB, above the {max_kb} KB limit")
        return Encoded(data, mime, ext, None, warnings)
    im = Image.open(io.BytesIO(png))
    im.load()
    if fmt == "png":
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        if max_kb and len(data) > max_kb * 1024:
            warnings.append(f"PNG is {len(data) // 1024} KB, above the {max_kb} KB limit; JPEG or WebP export can reduce it")
        return Encoded(data, mime, ext, None, warnings)
    rgb = im.convert("RGBA") if transparent else im.convert("RGB")
    q = quality
    while True:
        buf = io.BytesIO()
        if fmt == "jpeg":
            rgb.save(buf, "JPEG", quality=q, optimize=True, subsampling=0 if q >= 90 else 2)
        else:
            rgb.save(buf, "WEBP", quality=q, method=6, exact=transparent)
        data = buf.getvalue()
        if not max_kb or len(data) <= max_kb * 1024 or q <= 40:
            break
        q -= 5  # deterministic, safe correction: lower quality (never dimensions) until the platform size limit is met
    if q != quality:
        warnings.append(f"Quality reduced from {quality} to {q} to fit the {max_kb} KB limit")
    if max_kb and len(data) > max_kb * 1024:
        warnings.append(f"File is {len(data) // 1024} KB, still above the {max_kb} KB limit at minimum quality")
    return Encoded(data, mime, ext, q, warnings)


def validate_file(data: bytes, fmt: str, w: int, h: int, transparent: bool = False) -> list[str]:
    """Every produced file is decoded again: integrity, exact dimensions, expected container."""
    try:
        if fmt == "svg":
            root = ElementTree.fromstring(data)
            if not root.tag.endswith("svg"):
                return ["not an SVG document"]
            return [] if (int(float(root.get("width", 0))), int(float(root.get("height", 0)))) == (w, h) else ["SVG dimensions differ from canvas"]
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            want = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[fmt]
            out = []
            if im.format != want:
                out.append(f"format {im.format} != {want}")
            if im.size != (w, h):
                out.append(f"dimensions {im.size} != {(w, h)}")
            if transparent and "A" not in im.getbands():
                out.append("output has no alpha channel")
            return out
    except Exception as e:
        return [f"file unreadable: {type(e).__name__}"]


def render_bytes(db: Session, org_id: uuid.UUID, cr: GeneratedCreative, storage: Storage) -> bytes:
    from sqlalchemy import select
    from app.editor import service as editor_service
    from app.models import CreativeDocument
    doc = db.scalar(select(CreativeDocument).where(CreativeDocument.creative_id == cr.id, CreativeDocument.organization_id == org_id))
    if doc:   # edited banners: export always renders the latest document revision, never a stale image
        editor_service.ensure_fresh_render(db, org_id, doc, cr, storage)
    a = OrgRepo(db, CreativeAsset, org_id).get(cr.render_asset_id)
    if not a:
        raise ValueError("Creative has no render")
    return storage.get(a.storage_key)


def encode_creative(db, org_id, cr: GeneratedCreative, storage: Storage, fmt: str, quality: int, max_kb: int | None = None, transparent: bool = False) -> Encoded:
    if transparent and fmt not in TRANSPARENT_FORMATS:
        raise ValueError(f"{fmt.upper()} cannot be transparent; use PNG, WebP or SVG")
    png = render_bytes(db, org_id, cr, storage)   # also refreshes cr.spec for edited banners
    resolve = make_resolver(db, org_id, storage)
    spec = CreativeSpec.model_validate(cr.spec)
    warnings: list[str] = []
    if transparent:
        res = render_spec(spec, resolve, transparent=True)
        png = res.png
        warnings.append("Transparent export: the background was removed; text and shapes keep their colours and may be hard to read on some backgrounds")
    svg = build_svg(spec, resolve, transparent)[0].encode() if fmt == "svg" else None
    if transparent and fmt != "svg" and Image.open(io.BytesIO(png)).convert("RGBA").getchannel("A").getextrema()[0] == 255:
        warnings.append("No pixel is transparent: a full-bleed image layer covers the whole canvas")
    enc = encode(png, fmt, quality, max_kb, svg, transparent)
    enc.warnings = warnings + enc.warnings
    problems = validate_file(enc.data, fmt, cr.width, cr.height, transparent and fmt != "svg")
    if problems:
        raise ValueError("Export validation failed: " + "; ".join(problems))
    return enc


def quality_summary(db: Session, org_id: uuid.UUID, cr: GeneratedCreative) -> dict:
    """Real scores from the quality engine, or an explicit 'not_assessed' (never a made-up number)."""
    from app.models import QualityReport
    rows = OrgRepo(db, QualityReport, org_id).list(creative_id=cr.id)
    base = {"render_ok": cr.render_report.get("ok"), "status": cr.status, "technical": cr.render_report.get("technical", []), "notes": (cr.spec.get("meta") or {}).get("notes", [])}
    if not rows:
        return {"scoring": "not_assessed", **base}
    r, rep = rows[0], rows[0].report
    v = rep.get("vision")
    return {"scoring": "assessed", "score": r.score, "quality_status": r.status, "stale": r.spec_version != cr.spec_version, "assessed_spec_version": r.spec_version,
            "deterministic_score": rep.get("deterministic_score"), "categories": rep.get("categories"), "blocking": rep.get("blocking", []),
            "warnings": sorted({f["code"] for f in rep.get("findings", []) if f["severity"] == "warn"}),
            "corrections_applied": sum(len(x["actions"]) for x in rep.get("corrections", []) if x["accepted"]),
            "vision": ({"assessed": v.get("assessed"), "counted_in_score": v.get("counted_in_score"), "score": v.get("score"), "provider": v.get("provider")} if v else None), **base}


def build_zip(db: Session, org_id: uuid.UUID, campaign: Campaign, creatives: list[GeneratedCreative], fmt: str, quality: int, storage: Storage,
              skipped: list[dict] | None = None, quality_gate: str = "off", transparent: bool = False):
    concepts = {c.id: c for c in OrgRepo(db, CreativeConcept, org_id).list(campaign_id=campaign.id)}
    buf, items, used = io.BytesIO(), [], set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for cr in creatives:
            concept = concepts.get(cr.concept_id)
            cname = concept.data["name"] if concept else "concept"
            targets = [formats.get(k) for k in (cr.format_keys or [])]
            targets = [t for t in targets if t] or [None]
            thumb_done = False
            for t in targets:
                max_kb = t.max_file_kb if t else None
                if t and fmt not in t.file_types and fmt != "svg":
                    warn_type = [f"{fmt} is not an accepted file type for {t.platform} {t.placement} (accepts {', '.join(t.file_types)})"]
                else:
                    warn_type = []
                enc = encode_creative(db, org_id, cr, storage, fmt, quality, max_kb, transparent)
                platform, placement = (t.platform, t.placement) if t else ("custom", "custom")
                name = f"{slug(cname)}_{slug(placement)}_{cr.width}x{cr.height}.{enc.ext}"
                path, n = f"{platform}/{name}", 1
                while path in used:
                    n += 1
                    path = f"{platform}/{n}_{name}"
                used.add(path)
                z.writestr(path, enc.data)
                items.append({
                    "campaign_id": str(campaign.id), "creative_id": str(cr.id), "concept_id": str(cr.concept_id), "concept": cname,
                    "platform": platform, "placement": placement, "format_id": t.id if t else None, "width": cr.width, "height": cr.height,
                    "format": fmt, "transparent": transparent, "file": path, "file_size_bytes": len(enc.data), "max_file_kb": max_kb,
                    "within_size_limit": (len(enc.data) <= max_kb * 1024) if max_kb else None, "quality_used": enc.quality,
                    "spec_version": cr.spec_version, "created_at": cr.created_at.isoformat() if cr.created_at else None,
                    "format_verification": t.verification if t else None,
                    "quality_report": quality_summary(db, org_id, cr),
                    "warnings": enc.warnings + warn_type})
            if not thumb_done:
                im = Image.open(io.BytesIO(render_bytes(db, org_id, cr, storage))).convert("RGB")
                im.thumbnail((320, 320))
                tb = io.BytesIO()
                im.save(tb, "JPEG", quality=80)
                z.writestr(f"thumbnails/{cr.id}.jpg", tb.getvalue())
        manifest = {"campaign_id": str(campaign.id), "campaign_name": campaign.name, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "export_format": fmt, "transparent": transparent, "quality_setting": quality if fmt in ("jpeg", "webp") else None, "item_count": len(items), "quality_gate": quality_gate, "skipped": skipped or [], "items": items}
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
    return buf.getvalue(), manifest


def run_export(db: Session, org_id: uuid.UUID, export_id: uuid.UUID) -> None:
    job = OrgRepo(db, ExportJob, org_id).get(export_id)
    if not job or job.status not in ("pending",):
        return
    job.status = "processing"
    db.commit()
    try:
        campaign = OrgRepo(db, Campaign, org_id).get(job.campaign_id)
        storage = factories.make_storage()
        repo = OrgRepo(db, GeneratedCreative, org_id)
        ids = {uuid.UUID(i) for i in job.params.get("creative_ids") or []}
        creatives = [c for c in reversed(repo.list(campaign_id=campaign.id)) if (not ids or c.id in ids) and c.render_asset_id]
        skipped: list[dict] = []
        gate = job.params.get("quality_gate", "off")
        if gate == "block_failed":   # configurable gate: banners that failed their quality check are left out, and the manifest says which and why
            keep = []
            for c in creatives:
                if c.quality_status == "fail":
                    skipped.append({"creative_id": str(c.id), "width": c.width, "height": c.height, "reason": "failed quality gate", "quality_score": c.quality_score})
                else:
                    keep.append(c)
            creatives = keep
        if not creatives:
            raise ValueError("Nothing to export" if not skipped else f"All {len(skipped)} selected banners failed the quality gate")
        if len(creatives) > MAX_ITEMS:
            raise ValueError(f"Too many banners for one export (max {MAX_ITEMS})")
        data, manifest = build_zip(db, org_id, campaign, creatives, job.params["format"], job.params["quality"], storage, skipped, gate, bool(job.params.get("transparent")))
        key = f"{org_id}/exports/{job.id}.zip"
        storage.put(key, data, "application/zip")
        job.storage_key, job.manifest, job.size_bytes, job.file_count, job.status = key, manifest, len(data), manifest["item_count"], "completed"
        db.commit()
    except Exception as e:
        db.rollback()
        job = OrgRepo(db, ExportJob, org_id).get(export_id)
        job.status, job.error = "failed", f"{type(e).__name__}: {str(e)[:300]}"
        db.commit()
