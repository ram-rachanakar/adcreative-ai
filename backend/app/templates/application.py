"""Preview / apply / revert a template on a document (or on a freshly seeded one), and copy a layer between artboards.
Previews are non-destructive: the active document is only read; the candidate lives on a TemplateApplication row until the user commits it."""
import copy
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import OrgContext
from app.creative import formats
from app.creative.service import _brand, _persist_render, _store, get_product_visual, make_resolver
from app.editor import commands as C, preflight, service as editor
from app.models import (Campaign, CreativeAsset, CreativeConcept, CreativeDocument, GeneratedCreative, Product, ProductAsset, Template, TemplateApplication,
                        TemplateUsage, TemplateVersion)
from app.quality import service as quality
from app.rendering import fonts
from app.rendering.spec import CreativeSpec
from app.rendering.svg import render_spec
from app.repositories.org_repo import OrgRepo
from app.storage.base import Storage
from app.templates import mapping, schema as S, service as T
from app.templates.service import TemplateError

MAX_QUALITY_FINDINGS = 8


def _now():
    return datetime.now(timezone.utc)


def _usable(db: Session, ctx: OrgContext, tid: uuid.UUID, version: int | None) -> tuple[Template, TemplateVersion]:
    T.require_author(ctx)
    t = T.require_visible(db, ctx, tid)
    if t.status == "archived":
        raise TemplateError(409, "template_archived", "This template is archived and can no longer be applied (existing banners are unaffected)")
    if t.status == "draft" and t.owner_type != "system" and not T.can_manage(ctx, t):
        raise TemplateError(404, "not_found", "Template not found")
    return t, T.version_of(db, t, version)


def _zone(creative: GeneratedCreative | None, W: int, H: int) -> tuple[float, float] | None:
    keys = (creative.format_keys if creative else None) or [f.id for f in formats.for_size(W, H)]
    zs = [f.safe_zone for f in (formats.get(k) for k in keys) if f and f.safe_zone]
    return (max(z.top_pct for z in zs), max(z.bottom_pct for z in zs)) if zs else None


def _options(db, ctx, tv: TemplateVersion, campaign: Campaign, creative, W, H, mode, confirm_replace, slot_overrides, new_document=False) -> mapping.Options:
    if mode not in S.MODES:
        raise TemplateError(422, "invalid_mode", f"mode must be one of {list(S.MODES)}")
    if mode not in tv.application_modes:
        raise TemplateError(422, "mode_unsupported", f"This template does not offer the {mode} mode")
    bad = [r for r in confirm_replace if r not in S.SLOT_ROLES]
    if bad:
        raise TemplateError(422, "invalid_slot", f"Unknown slot(s): {bad}")
    return mapping.Options(mode=mode, confirm_replace=set(confirm_replace), slot_overrides=dict(slot_overrides), brand=_brand(db, ctx.org_id, campaign), brand_constraints=tv.brand_constraints or {},
                           safe_zone=_zone(creative, W, H), new_document=new_document)


def _map(db, ctx, t: Template, tv: TemplateVersion, spec: dict, opt: mapping.Options) -> tuple[mapping.Outcome, dict]:
    W, H = spec["canvas"]["width"], spec["canvas"]["height"]
    layout = T.layout_for_size(db, tv, W, H)
    if not layout:
        raise TemplateError(422, "format_unsupported", f"This template has no {W}x{H} layout, so it is not compatible with this banner (layouts are never stretched)",
                            supported=[f["format_id"] for f in T.supported_formats(db, tv)])
    try:
        out = mapping.map_template(spec, layout, opt)
    except mapping.MappingError as e:
        raise TemplateError(422, e.code, e.message) from e
    out.candidate["meta"] = {**out.candidate.get("meta", {}), "source_template_id": str(t.id), "source_template_version": tv.version}
    for aid in C.referenced_asset_ids(out.candidate) - C.referenced_asset_ids(spec):
        if not (OrgRepo(db, CreativeAsset, ctx.org_id).get(uuid.UUID(aid)) or OrgRepo(db, ProductAsset, ctx.org_id).get(uuid.UUID(aid))):
            raise TemplateError(422, "asset_not_found", f"Template asset {aid} is not available to this organization")
    return out, layout


# ---------------------------------------------------------------- seeding a new banner from campaign content (Workflow A)
def seed_document(db: Session, ctx: OrgContext, storage: Storage, campaign: Campaign, W: int, H: int) -> tuple[dict, uuid.UUID]:
    concepts = OrgRepo(db, CreativeConcept, ctx.org_id).list(campaign_id=campaign.id)
    concept = next((c for c in concepts if c.selected), None) or (concepts[0] if concepts else None)
    if not concept:
        raise TemplateError(409, "no_concept", "Create marketing concepts for this campaign first: they provide the headline, supporting copy and CTA")
    prods = OrgRepo(db, Product, ctx.org_id).list(campaign_id=campaign.id)
    if not prods:
        raise TemplateError(409, "no_product", "Analyze the product first")
    assets = list(reversed(OrgRepo(db, ProductAsset, ctx.org_id).list(product_id=prods[0].id)))
    images = [a for a in assets if a.kind == "image"]
    logos = [a for a in assets if a.kind == "logo"]
    if not images:
        raise TemplateError(409, "no_product_image", "Upload a product image first")
    visual_id, _size, _info = get_product_visual(db, ctx.org_id, campaign, images[0], storage)
    brand = _brand(db, ctx.org_id, campaign)
    family, _ = fonts.resolve_family(brand["fonts"][0] if brand.get("fonts") else None)
    d = concept.data
    box = lambda fx, fy, fw, fh: {"x": round(fx * W, 2), "y": round(fy * H, 2), "w": max(1.0, round(fw * W, 2)), "h": max(1.0, round(fh * H, 2))}  # noqa: E731
    els = [
        {"id": "headline", "type": "text", "role": "headline", "slot": "headline_primary", "name": "Headline", **box(.05, .05, .9, .2), "text": str(d.get("headline", ""))[:300], "font_family": family, "font_weight": 700, "font_size": max(10, H * 0.08)},
        {"id": "body", "type": "text", "role": "body", "slot": "body_copy", "name": "Supporting copy", **box(.05, .27, .9, .12), "text": str(d.get("supporting_copy", ""))[:300], "font_family": family, "font_size": max(8, H * 0.04)},
        {"id": "product", "type": "image", "role": "product", "slot": "product_primary", "name": "Product", **box(.2, .4, .6, .4), "asset_id": str(visual_id), "fit": "contain"},
        {"id": "cta", "type": "button", "role": "cta", "slot": "primary_cta", "name": "CTA", **box(.3, .84, .4, .1), "label": str(d.get("cta", "Learn more"))[:30], "font_family": family, "font_size": max(8, H * 0.04)},
    ]
    if logos:
        els.append({"id": "logo", "type": "image", "role": "logo", "slot": "brand_logo", "name": "Logo", **box(.05, .02, .15, .06), "asset_id": str(logos[0].id), "fit": "contain"})
    spec = {"canvas": {"width": W, "height": H}, "background": {"type": "solid", "color": "#ffffff"}, "elements": els, "meta": {"concept_id": str(concept.id)}}
    return CreativeSpec.model_validate(spec).model_dump(mode="json"), concept.id


# ---------------------------------------------------------------- preview
def _evaluate(db, ctx, storage, creative: GeneratedCreative | None, spec: dict) -> tuple[bytes, dict | None]:
    """Render with the deterministic renderer; with a persisted creative also run the M6 deterministic checks (no AI, no vision)."""
    cs = CreativeSpec.model_validate(spec)
    if creative is None:
        return render_spec(cs, make_resolver(db, ctx.org_id, storage)).png, None
    try:
        qctx = quality._Ctx(db, ctx.org_id, creative, storage, quality.org_policy(db, ctx.org_id))
        res, _bd, full = quality._evaluate(qctx, cs)
        fs = [{"code": f.get("code"), "severity": f.get("severity"), "message": f.get("message"), "element_id": f.get("element_id")} for f in res["findings"] if f.get("severity") != "info"]
        return full.png, {"score": res["score"], "status": res["status"], "findings": fs[:MAX_QUALITY_FINDINGS]}
    except Exception as e:  # noqa: BLE001 - the preview must still work if M6 cannot run
        return render_spec(cs, make_resolver(db, ctx.org_id, storage)).png, {"error": f"{type(e).__name__}"}


def _store_preview(db, ctx, storage, png: bytes, app_id: uuid.UUID, which: str, campaign_id) -> CreativeAsset:
    a = _store(db, storage, ctx.org_id, png, "image/png", "candidate", f"templates/applications/{app_id}", campaign_id=campaign_id, provider="local", model="resvg",
               params={"template_application": str(app_id), "which": which})
    return a


def _merge_warnings(out: mapping.Outcome, pre: list[dict]) -> None:
    for p in pre:
        if p["code"] in ("text_shrunk", "cta_missing"):
            continue
        mapping.warn(out, p["code"], p["message"], p.get("layer_id"), p["severity"])


def preview(db: Session, ctx: OrgContext, storage: Storage, tid: uuid.UUID, *, version: int | None, document_id: uuid.UUID | None, campaign_id: uuid.UUID | None,
            format_id: str | None, mode: str, confirm_replace: list[str], slot_overrides: dict[str, str]) -> dict:
    t, tv = _usable(db, ctx, tid, version)
    if bool(document_id) == bool(campaign_id):
        raise TemplateError(422, "invalid_target", "Provide exactly one of document_id (restyle an existing banner) or campaign_id (start a new banner)")
    if document_id:
        doc = db.scalar(select(CreativeDocument).where(CreativeDocument.id == document_id, CreativeDocument.organization_id == ctx.org_id))
        creative = OrgRepo(db, GeneratedCreative, ctx.org_id).get(doc.creative_id) if doc else None
        if not doc or not creative:
            raise TemplateError(404, "not_found", "Document not found")
        campaign = OrgRepo(db, Campaign, ctx.org_id).get(doc.campaign_id)
        spec, W, H, rev = copy.deepcopy(doc.document), doc.width, doc.height, doc.revision
        opt = _options(db, ctx, tv, campaign, creative, W, H, mode, confirm_replace, slot_overrides)
        concept_id = None
    else:
        campaign = OrgRepo(db, Campaign, ctx.org_id).get(campaign_id)
        if not campaign:
            raise TemplateError(404, "not_found", "Campaign not found")
        if not format_id or "x" not in format_id:
            raise TemplateError(422, "invalid_format", "format_id (WxH) is required when starting a new banner")
        try:
            W, H = (int(x) for x in format_id.lower().split("x"))
        except ValueError:
            raise TemplateError(422, "invalid_format", f"Invalid format {format_id!r}")
        if not formats.valid_custom(W, H):
            raise TemplateError(422, "invalid_format", "Size out of range")
        doc, creative, rev = None, None, None
        spec, concept_id = seed_document(db, ctx, storage, campaign, W, H)
        opt = _options(db, ctx, tv, campaign, None, W, H, mode, confirm_replace, slot_overrides, new_document=True)
    out, layout = _map(db, ctx, t, tv, spec, opt)
    before_png, q_before = _evaluate(db, ctx, storage, creative, spec)
    after_png, q_after = _evaluate(db, ctx, storage, creative, out.candidate)
    try:
        _merge_warnings(out, preflight.run(db, ctx.org_id, out.candidate, storage))
    except Exception:  # noqa: BLE001
        pass
    app_id = uuid.uuid4()
    pb = _store_preview(db, ctx, storage, before_png, app_id, "before", campaign.id)
    pa = _store_preview(db, ctx, storage, after_png, app_id, "after", campaign.id)
    blocking = [w for w in out.warnings if w["severity"] == "error"]
    app = TemplateApplication(id=app_id, organization_id=ctx.org_id, template_id=t.id, template_version_id=tv.id, document_id=doc.id if doc else None, campaign_id=campaign.id,
                              format_id=f"{W}x{H}", source_revision=rev, application_mode=mode, status="previewed", mapping_result=out.result(), preserved_layer_ids=out.preserved,
                              replaced_layer_ids=out.replaced, created_layer_ids=out.created, warnings=out.warnings,
                              options={"confirm_replace": sorted(confirm_replace), "slot_overrides": slot_overrides, "concept_id": str(concept_id) if concept_id else None, "removed": out.removed},
                              source_document=spec, candidate_document=out.candidate, preview_before_asset_id=pb.id, preview_after_asset_id=pa.id, created_by=ctx.user.id)
    db.add(app)
    db.flush()
    return application_out(db, ctx, storage, app, t, tv, quality_before=q_before, quality_after=q_after, can_apply=not blocking)


def application_out(db, ctx, storage, app: TemplateApplication, t: Template, tv: TemplateVersion, *, quality_before=None, quality_after=None, can_apply: bool = True) -> dict:
    def url(aid):
        a = OrgRepo(db, CreativeAsset, ctx.org_id).get(aid) if aid else None
        return storage.signed_url(a.storage_key, 3600) if a else None
    return {"application_id": str(app.id), "status": app.status, "mode": app.application_mode, "format_id": app.format_id, "template": {"id": str(t.id), "name": t.name, "version": tv.version},
            "document_id": str(app.document_id) if app.document_id else None, "campaign_id": str(app.campaign_id) if app.campaign_id else None, "source_revision": app.source_revision,
            "before_url": url(app.preview_before_asset_id), "after_url": url(app.preview_after_asset_id), "mapping": app.mapping_result, "warnings": app.warnings,
            "requires_confirmation": app.application_mode == "full", "needs_confirmation": app.mapping_result.get("needs_confirmation", []),
            "can_apply": can_apply and app.status == "previewed", "quality": {"before": quality_before, "after": quality_after},
            "applied_revision": app.applied_revision, "created_at": app.created_at.isoformat() if app.created_at else None}


def get_application(db: Session, ctx: OrgContext, aid: uuid.UUID) -> TemplateApplication:
    a = db.scalar(select(TemplateApplication).where(TemplateApplication.id == aid, TemplateApplication.organization_id == ctx.org_id))
    if not a or a.created_by != ctx.user.id and not T.is_manager(ctx):
        raise TemplateError(404, "not_found", "Application not found")
    return a


# ---------------------------------------------------------------- apply / reject / revert
def apply_existing(db: Session, ctx: OrgContext, storage: Storage, app: TemplateApplication, expected_revision: int | None, confirm: bool) -> tuple[CreativeDocument, list]:
    doc = editor.lock(db, ctx.org_id, app.document_id)
    creative = OrgRepo(db, GeneratedCreative, ctx.org_id).get(doc.creative_id) if doc else None
    if not doc or not creative:
        raise TemplateError(404, "not_found", "Document not found")
    if app.status == "applied":                 # idempotent retry of a committed apply
        return doc, []
    if app.status != "previewed":
        raise TemplateError(409, "application_closed", f"This preview was already {app.status}")
    if app.application_mode == "full" and not confirm:
        raise TemplateError(422, "confirmation_required", "Applying a full template needs explicit confirmation of the listed changes")
    if doc.revision != app.source_revision or (expected_revision is not None and expected_revision != doc.revision):
        raise editor.RevisionConflict(doc.revision)
    t = db.get(Template, app.template_id)
    if not t or t.status == "archived":
        raise TemplateError(409, "template_archived", "This template was archived; the preview can no longer be applied")
    tv = db.get(TemplateVersion, app.template_version_id)
    editor.make_version(db, doc, "Before template", f"State before applying “{t.name}”", "restore_point", ctx.user.id)
    cmd = {"type": "APPLY_TEMPLATE", "payload": {"candidate": app.candidate_document, "application_id": str(app.id)}}
    try:
        ops, _dup = editor.apply_batch(db, ctx.org_id, doc, creative, ctx.user.id, [cmd], doc.revision, batch_id=app.id.hex,
                                       version=(f"Template: {t.name}", f"Applied “{t.name}” v{tv.version} ({app.application_mode.replace('_', ' ')})", "template"))
    except C.CommandError as e:
        raise TemplateError(409 if e.code == "layer_locked" else 422, e.code, e.message) from e
    if not ops:
        raise TemplateError(409, "no_change", "The template would not change this banner")
    app.status, app.applied_revision, app.applied_at = "applied", doc.revision, _now()
    db.add(TemplateUsage(organization_id=ctx.org_id, template_id=t.id, user_id=ctx.user.id, document_id=doc.id, application_mode=app.application_mode))
    T.audit(db, ctx, "template.applied", "creative_document", template_id=str(t.id), document_id=str(doc.id), mode=app.application_mode, version=tv.version)
    db.flush()
    return doc, ops


def create_from_application(db: Session, ctx: OrgContext, storage: Storage, app: TemplateApplication) -> CreativeDocument:
    """Workflow A commit: build the GeneratedCreative + editable document from the previewed candidate (still no AI call)."""
    if app.document_id is not None and app.status == "applied":
        return db.get(CreativeDocument, app.document_id)
    if app.status != "previewed" or app.document_id is not None:
        raise TemplateError(409, "application_closed", f"This preview was already {app.status}")
    t = db.get(Template, app.template_id)
    if not t or t.status == "archived":
        raise TemplateError(409, "template_archived", "This template was archived")
    W, H = (int(x) for x in app.format_id.split("x"))
    concept_id = uuid.UUID(app.options["concept_id"])
    spec = app.candidate_document
    for aid in C.referenced_asset_ids(spec):
        editor._check_assets(db, ctx.org_id, {aid})
    cr = GeneratedCreative(organization_id=ctx.org_id, campaign_id=app.campaign_id, concept_id=concept_id, width=W, height=H, spec=spec, spec_version=1,
                           format_keys=[f.id for f in formats.for_size(W, H)], status="rendered")
    db.add(cr)
    db.flush()
    _persist_render(db, storage, ctx.org_id, cr, render_spec(CreativeSpec.model_validate(spec), make_resolver(db, ctx.org_id, storage)))
    doc = editor.get_or_create(db, ctx.org_id, cr, ctx.user.id)
    campaign = OrgRepo(db, Campaign, ctx.org_id).get(app.campaign_id)
    doc.name = f"{campaign.name} {W}x{H}"
    doc.rendered_revision = 0
    app.document_id, app.status, app.applied_revision, app.applied_at, app.source_revision = doc.id, "applied", 0, _now(), None
    db.add(TemplateUsage(organization_id=ctx.org_id, template_id=t.id, user_id=ctx.user.id, document_id=doc.id, application_mode=app.application_mode))
    T.audit(db, ctx, "template.banner_created", "creative_document", template_id=str(t.id), document_id=str(doc.id), mode=app.application_mode)
    db.flush()
    return doc


def reject(db: Session, ctx: OrgContext, app: TemplateApplication) -> TemplateApplication:
    if app.status != "previewed":
        raise TemplateError(409, "application_closed", f"This preview was already {app.status}")
    app.status = "rejected"            # the candidate is discarded; the document was never touched
    db.flush()
    return app


def revert(db: Session, ctx: OrgContext, storage: Storage, app: TemplateApplication, force: bool) -> tuple[CreativeDocument, list]:
    if app.document_id is None or app.status != "applied" or not app.source_document or app.source_revision is None:
        raise TemplateError(409, "not_revertable", "Only an applied template on an existing banner can be reverted")
    doc = editor.lock(db, ctx.org_id, app.document_id)
    creative = OrgRepo(db, GeneratedCreative, ctx.org_id).get(doc.creative_id)
    if doc.revision != app.applied_revision and not force:
        raise TemplateError(409, "later_edits", f"The banner was edited {doc.revision - (app.applied_revision or 0)} time(s) after this template. Use undo for those, or revert with force to discard them",
                            current_revision=doc.revision)
    editor.make_version(db, doc, "Before revert", "State before reverting the template", "restore_point", ctx.user.id)
    cmd = {"type": "REVERT_TEMPLATE_APPLICATION", "payload": {"snapshot": app.source_document, "application_id": str(app.id)}}
    try:
        ops, _ = editor.apply_batch(db, ctx.org_id, doc, creative, ctx.user.id, [cmd], doc.revision, batch_id=uuid.uuid4().hex,
                                    version=("Template reverted", "Restored the design from before the template", "template"))
    except C.CommandError as e:
        raise TemplateError(409 if e.code == "layer_locked" else 422, e.code, e.message) from e
    if not ops:
        raise TemplateError(409, "no_change", "The banner already matches the original design")
    app.status = "reverted"
    T.audit(db, ctx, "template.reverted", "creative_document", template_id=str(app.template_id), document_id=str(doc.id))
    db.flush()
    return doc, ops


def history(db: Session, ctx: OrgContext, doc_id: uuid.UUID) -> list[dict]:
    rows = db.scalars(select(TemplateApplication).where(TemplateApplication.document_id == doc_id, TemplateApplication.organization_id == ctx.org_id,
                                                         TemplateApplication.status.in_(["applied", "reverted"])).order_by(TemplateApplication.created_at.desc()))
    out = []
    for a in rows:
        t = db.get(Template, a.template_id)
        v = db.get(TemplateVersion, a.template_version_id)
        out.append({"application_id": str(a.id), "status": a.status, "mode": a.application_mode, "template_id": str(a.template_id), "template_name": t.name if t else "",
                    "template_version": v.version if v else None, "applied_revision": a.applied_revision, "applied_at": a.applied_at.isoformat() if a.applied_at else None})
    return out


# ---------------------------------------------------------------- COPY_LAYER_TO_ARTBOARD
def copy_layer(db: Session, ctx: OrgContext, doc: CreativeDocument, creative: GeneratedCreative, source_doc_id: uuid.UUID, layer_id: str, expected_revision: int):
    src = db.scalar(select(CreativeDocument).where(CreativeDocument.id == source_doc_id, CreativeDocument.organization_id == ctx.org_id))
    if not src:
        raise TemplateError(404, "not_found", "Source document not found")
    layer = next((e for e in src.document["elements"] if e["id"] == layer_id), None)
    if not layer:
        raise TemplateError(404, "layer_not_found", "Layer not found in the source document")
    mini = {"version": 1, "canvas": src.document["canvas"], "background": {"type": "solid", "color": "#ffffff"}, "elements": [layer], "meta": {}}
    scaled = editor.adapt_spec(mini, doc.width, doc.height)["elements"][0] if (src.width, src.height) != (doc.width, doc.height) else dict(layer)
    cmd = {"type": "COPY_LAYER_TO_ARTBOARD", "payload": {"layer": scaled, "source_document_id": str(src.id)}}
    return editor.apply_batch(db, ctx.org_id, doc, creative, ctx.user.id, [cmd], expected_revision)
