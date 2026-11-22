import hashlib
import io
import uuid
from dataclasses import dataclass, field

from PIL import Image
from sqlalchemy.orm import Session

from app.ai.image import ImageGenerationProvider, ImageNotConfigured, ImageProviderError
from app.creative.cutout import BorderFloodCutout
from app.creative import formats
from app.creative.templates import ComposeInput, compose, family_for
from app.marketing.service import Conflict
from app.models import BrandProfile, Campaign, CreativeAsset, CreativeBrief, CreativeConcept, GeneratedCreative, Product, ProductAsset, UsageRecord
from app.rendering import fonts
from app.rendering.spec import CreativeSpec
from app.rendering.svg import ResolvedAsset, RenderResult, render_spec
from app.repositories.org_repo import OrgRepo
from app.scraping.service import ImageRejected, inspect_image
from app.storage.base import Storage

BG_PROMPT_VERSION = "background.v1"
MASTER_BG = (1024, 1024)  # one background per concept, cover-cropped to every banner size (no per-size regeneration)


def make_resolver(db: Session, org_id: uuid.UUID, storage: Storage):
    cache: dict[uuid.UUID, ResolvedAsset | None] = {}

    def resolve(aid: uuid.UUID) -> ResolvedAsset | None:
        if aid in cache:
            return cache[aid]
        row = OrgRepo(db, ProductAsset, org_id).get(aid) or OrgRepo(db, CreativeAsset, org_id).get(aid)  # tenant-scoped lookups only
        cache[aid] = ResolvedAsset(storage.get(row.storage_key), row.width, row.height) if row else None
        return cache[aid]
    return resolve


def _store(db, storage, org_id, data, mime, kind, key_stem, **fields) -> CreativeAsset:
    digest = hashlib.sha256(data).hexdigest()
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
    key = f"{org_id}/creative/{key_stem}/{digest[:16]}.{ext}"
    storage.put(key, data, mime)
    w, h = Image.open(io.BytesIO(data)).size
    return OrgRepo(db, CreativeAsset, org_id).add(kind=kind, storage_key=key, content_hash=digest, mime=mime, width=w, height=h, **fields)


def get_background(db, org_id, campaign, concept, brief_data, provider: ImageGenerationProvider, storage, palette, regenerate=False) -> tuple[CreativeAsset, bool]:
    prompt = brief_data["background_prompt_final"]
    model = getattr(provider, "model", "")
    cache_key = hashlib.sha256("|".join([str(org_id), provider.name, model, BG_PROMPT_VERSION, prompt, str(MASTER_BG), ",".join(palette)]).encode()).hexdigest()
    repo = OrgRepo(db, CreativeAsset, org_id)  # cache is per-organization by construction
    if not regenerate:
        for a in repo.list(kind="background", cache_key=cache_key):
            return a, True
    seed = None if not regenerate else uuid.uuid4().int % 10_000
    try:
        res = provider.generate_background(prompt, *MASTER_BG, seed=seed, palette=palette)
    except ImageProviderError:
        raise
    try:
        mime, _, _ = inspect_image(res.data)
    except ImageRejected as e:
        raise ImageProviderError(f"Provider returned an unusable image: {e}")
    asset = _store(db, storage, org_id, res.data, mime, "background", f"{campaign.id}/bg", campaign_id=campaign.id, concept_id=concept.id,
                   cache_key=cache_key, provider=res.provider, model=res.model, prompt_version=BG_PROMPT_VERSION,
                   params={**res.params, "prompt": prompt, "palette": palette}, cost_usd=res.cost_usd, duration_ms=res.duration_ms)
    OrgRepo(db, UsageRecord, org_id).add(campaign_id=campaign.id, kind="image_generation", provider=res.provider, model=res.model,
                                         cost_usd=res.cost_usd, priced=res.priced, meta={"concept_id": str(concept.id), "asset_id": str(asset.id), "ms": res.duration_ms})
    return asset, False


def get_product_visual(db, org_id, campaign, product_asset: ProductAsset, storage) -> tuple[uuid.UUID, tuple[int, int], dict]:
    """Returns (asset id to place, its size, info). The original is never modified; a cut-out is a derived asset."""
    repo = OrgRepo(db, CreativeAsset, org_id)
    for a in repo.list(kind="cutout", parent_asset_id=product_asset.id):
        return a.id, (a.width, a.height), a.params
    res = BorderFloodCutout().cutout(storage.get(product_asset.storage_key))
    info = {"method": res.method, "foreground_fraction": round(res.foreground_fraction, 3), "note": res.note}
    if res.png is None:
        return product_asset.id, (product_asset.width, product_asset.height), info
    a = _store(db, storage, org_id, res.png, "image/png", "cutout", f"{campaign.id}/cutout", campaign_id=campaign.id, parent_asset_id=product_asset.id,
               provider="local", model="border_flood", params=info)
    return a.id, (a.width, a.height), info


def _brand(db, org_id, campaign) -> dict:
    bid = (campaign.brief or {}).get("brand_profile_id")
    bp = OrgRepo(db, BrandProfile, org_id).get(uuid.UUID(bid)) if bid else None
    return {**bp.detected, **bp.overrides} if bp else {}


def _persist_render(db, storage, org_id, creative: GeneratedCreative, res: RenderResult) -> None:
    a = _store(db, storage, org_id, res.png, "image/png", "render", f"{creative.campaign_id}/renders/{creative.id}/v{creative.spec_version}",
               campaign_id=creative.campaign_id, concept_id=creative.concept_id, provider="local", model="resvg", params={"spec_version": creative.spec_version})
    rep = res.report
    creative.render_asset_id = a.id
    creative.render_report = {"ok": rep.ok, "errors": rep.errors, "missing_assets": rep.missing_assets,
                              "elements": [{"id": e.id, "type": e.type, "font_size": e.font_size, "lines": e.lines, "text_overflow": e.text_overflow,
                                            "out_of_bounds": e.out_of_bounds, "shrunk": e.shrunk} for e in rep.elements]}
    creative.status = "rendered" if rep.ok else "needs_attention"


@dataclass
class SizeReq:
    width: int
    height: int
    format_keys: list[str] = field(default_factory=list)


@dataclass
class Prepared:
    concepts: list
    briefs: dict
    product_asset: ProductAsset
    logo: ProductAsset | None
    brand: dict
    font_family: str
    font_note: str | None


def prepare(db: Session, org_id: uuid.UUID, campaign: Campaign, sizes: list[SizeReq], product_asset_id: uuid.UUID | None = None,
            concept_ids: list[uuid.UUID] | None = None) -> Prepared:
    """All preconditions, checked BEFORE any paid work starts (also used by the API to reject a job up front)."""
    concepts = [c for c in OrgRepo(db, CreativeConcept, org_id).list(campaign_id=campaign.id) if c.selected and (not concept_ids or c.id in concept_ids)]
    if not concepts:
        raise Conflict("Select at least one concept first")
    if any(c.status == "flagged" for c in concepts):
        raise Conflict("A selected concept has unresolved unsupported claims")
    briefs = {b.concept_id: b for b in OrgRepo(db, CreativeBrief, org_id).list()}
    if any(c.id not in briefs for c in concepts):
        raise Conflict("Create briefs for the selected concepts first")
    prod = OrgRepo(db, Product, org_id).list(campaign_id=campaign.id)
    if not prod:
        raise Conflict("Analyze the product first")
    assets = list(reversed(OrgRepo(db, ProductAsset, org_id).list(product_id=prod[0].id)))  # oldest first
    images = [a for a in assets if a.kind == "image" and (not product_asset_id or a.id == product_asset_id)]
    logos = [a for a in assets if a.kind == "logo"]
    if not images:
        raise Conflict("No product image available: upload one first")
    if not sizes:
        raise Conflict("Choose at least one banner size")
    brand = _brand(db, org_id, campaign)
    family, note = fonts.resolve_family(brand["fonts"][0] if brand.get("fonts") else None)
    return Prepared(concepts, briefs, images[0], logos[0] if logos else None, brand, family, note)


def unit_key(concept_id, w, h) -> str:
    return f"{concept_id}:{w}x{h}"


def generate_creatives(db: Session, org_id: uuid.UUID, campaign: Campaign, provider: ImageGenerationProvider, storage: Storage,
                       sizes: list[SizeReq], product_asset_id: uuid.UUID | None = None, regenerate_background: bool = False, *,
                       concept_ids: list[uuid.UUID] | None = None, job_id: uuid.UUID | None = None, done: set[str] | None = None, on_stage=lambda stage, detail, pct: None,
                       should_cancel=lambda: False, on_unit=lambda result: None, retry_sleep=lambda s: None, max_attempts: int = 3,
                       on_quality=None) -> dict:
    """Resumable: units already in `done` are skipped (idempotent retries). A failure of one concept/size never aborts the others.
    Returns {"cost": float, "cancelled": bool}."""
    prep = prepare(db, org_id, campaign, sizes, product_asset_id, concept_ids)
    done = done or set()
    repo = OrgRepo(db, GeneratedCreative, org_id)
    resolve = make_resolver(db, org_id, storage)
    cost, total = 0.0, len(prep.concepts) * len(sizes)
    for sz in sizes:
        family_for(sz.width, sz.height)  # unsupported sizes fail fast, before spending anything
    on_stage("generating_assets", "Preparing product visual", 2)
    visual_id, visual_size, cut_info = get_product_visual(db, org_id, campaign, prep.product_asset, storage)
    db.commit()
    finished = 0
    for ci, concept in enumerate(prep.concepts):
        pending = [sz for sz in sizes if unit_key(concept.id, sz.width, sz.height) not in done]
        if not pending:
            finished += len(sizes)
            continue
        if should_cancel():
            return {"cost": cost, "cancelled": True}
        palette = list(dict.fromkeys((prep.brand.get("colors") or []) + concept.data["color_palette"]))
        on_stage("generating_assets", f"Background {ci + 1}/{len(prep.concepts)}", 5 + int(25 * ci / len(prep.concepts)))
        bg, cached, bg_err = None, False, None
        for attempt in range(max_attempts):  # exponential backoff on provider errors
            try:
                bg, cached = get_background(db, org_id, campaign, concept, prep.briefs[concept.id].data, provider, storage, palette[:4], regenerate_background)
                break
            except ImageNotConfigured:
                raise
            except ImageProviderError as e:
                bg_err = str(e)
                if attempt < max_attempts - 1:
                    retry_sleep(2 ** attempt)
        db.commit()  # paid generation work is persisted immediately, whatever happens to later units
        if bg is None:  # quality-critical provider failed: no silent substitution, the concept's units are reported as failed
            for sz in pending:
                on_unit({"unit": unit_key(concept.id, sz.width, sz.height), "concept_id": str(concept.id), "width": sz.width, "height": sz.height,
                         "status": "failed", "error": f"Background generation failed: {bg_err}"})
                finished += 1
            continue
        if not cached:
            cost += float(bg.cost_usd or 0)
        bg_bytes = storage.get(bg.storage_key)
        for sz in pending:
            if should_cancel():
                return {"cost": cost, "cancelled": True}
            key = unit_key(concept.id, sz.width, sz.height)
            on_stage("composing", f"{sz.width}x{sz.height}", 30 + int(65 * finished / max(total, 1)))
            try:
                zone = [formats.get(k).safe_zone for k in sz.format_keys if formats.get(k) and formats.get(k).safe_zone]
                notes = [f"Product cut-out: {cut_info['method']} ({cut_info['note']})"] + ([prep.font_note] if prep.font_note else []) + (["Background reused from cache"] if cached else [])
                inp = ComposeInput(headline=concept.data["headline"], supporting=concept.data["supporting_copy"], cta=concept.data["cta"], palette=palette,
                                   bg_asset_id=bg.id, bg_bytes=bg_bytes, product_asset_id=visual_id, product_size=visual_size,
                                   alternatives=concept.data.get("alternative_headlines", []), logo_asset_id=prep.logo.id if prep.logo else None,
                                   logo_size=(prep.logo.width, prep.logo.height) if prep.logo else None, font_family=prep.font_family,
                                   concept_id=str(concept.id), notes=notes, safe_top_pct=max((z.top_pct for z in zone), default=0),
                                   safe_bottom_pct=max((z.bottom_pct for z in zone), default=0))
                spec = compose(inp, sz.width, sz.height)
                for old in [c for c in repo.list(concept_id=concept.id) if (c.width, c.height) == (sz.width, sz.height)]:
                    repo.soft_delete(old)
                cr = repo.add(campaign_id=campaign.id, concept_id=concept.id, width=sz.width, height=sz.height, spec=spec.model_dump(mode="json"),
                              spec_version=1, format_keys=sz.format_keys, job_id=job_id)
                on_stage("rendering", f"{sz.width}x{sz.height}", 30 + int(65 * finished / max(total, 1)))
                _persist_render(db, storage, org_id, cr, render_spec(spec, resolve))
                on_stage("validating", f"{sz.width}x{sz.height}", 30 + int(65 * finished / max(total, 1)))
                problems = technical_validation(storage, db, org_id, cr)
                if problems:
                    cr.status = "needs_attention"
                    cr.render_report = {**cr.render_report, "technical": problems}
                fatal = any(p.startswith("dimension") or p.startswith("corrupt") for p in problems)
                q: dict = {}
                if on_quality and not fatal:   # the quality gate runs per banner, right after render; a failure here never loses the banner
                    on_stage("validating", f"Quality check {sz.width}x{sz.height}", 30 + int(65 * finished / max(total, 1)))
                    try:
                        q = on_quality(cr) or {}
                    except Exception as e:
                        q = {"quality_error": f"{type(e).__name__}: {str(e)[:150]}"}
                on_unit({"unit": key, "concept_id": str(concept.id), "width": sz.width, "height": sz.height, "format_keys": sz.format_keys,
                         "status": "failed" if fatal else "ok",
                         "creative_id": str(cr.id), "needs_attention": cr.status == "needs_attention", "error": "; ".join(problems) or None, **q})
            except Exception as e:  # one bad format must not sink the whole job
                db.rollback()  # drops only this unit's uncommitted rows
                on_unit({"unit": key, "concept_id": str(concept.id), "width": sz.width, "height": sz.height, "status": "failed", "error": f"{type(e).__name__}: {str(e)[:200]}"})
            finished += 1
    campaign.status = "creatives_ready"
    db.flush()
    return {"cost": cost, "cancelled": False}


def technical_validation(storage: Storage, db: Session, org_id: uuid.UUID, cr: GeneratedCreative) -> list[str]:
    """Deterministic file checks on the stored render: integrity, exact dimensions, format."""
    a = OrgRepo(db, CreativeAsset, org_id).get(cr.render_asset_id) if cr.render_asset_id else None
    if not a:
        return ["corrupt: no render stored"]
    out = []
    try:
        data = storage.get(a.storage_key)
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "PNG":
                out.append(f"format: expected PNG, got {im.format}")
            if im.size != (cr.width, cr.height):
                out.append(f"dimension mismatch: {im.size} != {(cr.width, cr.height)}")
    except Exception as e:
        out.append(f"corrupt: {type(e).__name__}")
    for p in cr.render_report.get("errors", []):
        out.append(f"render: {p}")
    return out


def rerender(db: Session, org_id: uuid.UUID, creative: GeneratedCreative, spec: CreativeSpec, storage: Storage) -> GeneratedCreative:
    """Re-render from an edited spec. Never calls an AI provider: text/colour/layout edits are free and deterministic."""
    resolve = make_resolver(db, org_id, storage)
    for aid in spec.asset_ids():
        if resolve(aid) is None:  # also enforces tenant ownership of every referenced asset
            raise ValueError(f"Asset {aid} not found")
    if (spec.canvas.width, spec.canvas.height) != (creative.width, creative.height):
        raise ValueError("Canvas size cannot be changed on an existing creative")
    creative.spec = spec.model_dump(mode="json")
    creative.spec_version += 1
    _persist_render(db, storage, org_id, creative, render_spec(spec, resolve))
    return creative
