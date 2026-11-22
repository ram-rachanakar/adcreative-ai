"""Template library: visibility/permissions, queries, serialisation, thumbnails, authoring (save-as, duplicate, versions, lifecycle)."""
import hashlib
import math
import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session

from app.api.deps import OrgContext
from app.creative import formats
from app.creative.service import _store, make_resolver
from app.models import AuditLog, CreativeAsset, ProductAsset, Role, Template, TemplateAsset, TemplateFavorite, TemplateLayout, TemplateUsage, TemplateVersion
from app.models.core import ROLE_RANK
from app.rendering.spec import CreativeSpec
from app.rendering.svg import render_spec
from app.repositories.org_repo import OrgRepo
from app.storage.base import Storage
from app.templates import library, mapping, schema as S

THUMB_VERSION = "thumb.v1"


class TemplateError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def audit(db: Session, ctx: OrgContext, action: str, resource: str, **detail) -> None:
    OrgRepo(db, AuditLog, ctx.org_id).add(user_id=ctx.user.id, action=action, resource=resource, detail=detail)


# ---------------------------------------------------------------- access control (never trusts a body-supplied organisation)
def is_manager(ctx: OrgContext) -> bool:
    return ROLE_RANK[ctx.role] >= ROLE_RANK[Role.admin]


def visible_filter(ctx: OrgContext):
    """SQL predicate for templates this user may see: system, the organisation's own, and only their OWN personal templates.
    Drafts are visible to their creator and to managers only. Defence in depth on top of RLS."""
    draft_ok = true() if is_manager(ctx) else or_(Template.status != "draft", Template.created_by == ctx.user.id)
    return (Template.deleted_at.is_(None)
            & or_(Template.owner_type == "system", Template.organization_id == ctx.org_id)
            & or_(Template.owner_type != "personal", Template.owner_id == ctx.user.id)
            & or_(Template.owner_type == "system", draft_ok))


def get_visible(db: Session, ctx: OrgContext, tid: uuid.UUID) -> Template | None:
    library.ensure_system_templates(db)
    return db.scalar(select(Template).where(Template.id == tid, visible_filter(ctx)))


def require_visible(db: Session, ctx: OrgContext, tid: uuid.UUID) -> Template:
    t = get_visible(db, ctx, tid)
    if not t:
        raise TemplateError(404, "not_found", "Template not found")     # foreign / private / unknown all look the same
    return t


def can_manage(ctx: OrgContext, t: Template) -> bool:
    if t.owner_type == "system":
        return False                      # built-in templates are read-only through the API (duplicate to customise)
    if t.owner_type == "personal":
        return t.owner_id == ctx.user.id
    return is_manager(ctx) or (t.created_by == ctx.user.id and ROLE_RANK[ctx.role] >= ROLE_RANK[Role.designer])


def require_manage(ctx: OrgContext, t: Template) -> None:
    if t.owner_type == "system":
        raise TemplateError(403, "system_template_readonly", "Built-in templates cannot be modified; duplicate it to make your own")
    if not can_manage(ctx, t):
        raise TemplateError(403, "forbidden", "You are not allowed to change this template")


def require_author(ctx: OrgContext) -> None:
    if ROLE_RANK[ctx.role] < ROLE_RANK[Role.designer]:
        raise TemplateError(403, "forbidden", "Your role cannot create or apply templates")


# ---------------------------------------------------------------- loading
def version_of(db: Session, t: Template, version: int | None = None) -> TemplateVersion:
    v = db.scalar(select(TemplateVersion).where(TemplateVersion.template_id == t.id, TemplateVersion.version == (version or t.current_version)))
    if not v:
        raise TemplateError(404, "version_not_found", "Template version not found")
    return v


def raw_layouts(db: Session, tv: TemplateVersion) -> dict[str, dict]:
    rows = db.scalars(select(TemplateLayout).where(TemplateLayout.template_version_id == tv.id).order_by(TemplateLayout.width, TemplateLayout.height))
    return {r.format_id: r.layout_definition for r in rows}


def layout_for_size(db: Session, tv: TemplateVersion, w: int, h: int) -> dict | None:
    """Fully resolved layout for an EXACT canvas size, or None (the template is then unsupported there: no stretching)."""
    raws = raw_layouts(db, tv)
    fid = f"{w}x{h}"
    if fid not in raws:
        return None
    r = S.resolve_layout(raws, fid)
    return {"format_id": fid, "width": w, "height": h, "definition": r}


def aspect_class(w: int, h: int) -> str:
    r = w / h
    return "wide" if r >= 3 else "landscape" if r >= 1.15 else "square" if r >= 0.9 else "portrait" if r >= 0.6 else "tall"


def supported_formats(db: Session, tv: TemplateVersion) -> list[dict]:
    out = []
    for r in db.scalars(select(TemplateLayout).where(TemplateLayout.template_version_id == tv.id).order_by(TemplateLayout.width, TemplateLayout.height)):
        reg = formats.for_size(r.width, r.height)
        out.append({"format_id": r.format_id, "width": r.width, "height": r.height, "aspect_ratio": round(r.aspect_ratio, 4), "aspect_class": aspect_class(r.width, r.height),
                    "inherits": r.layout_definition.get("extends"), "placements": [f.id for f in reg]})
    return out


def owner_label(t: Template, ctx: OrgContext) -> str:
    return "System" if t.owner_type == "system" else "You" if t.owner_type == "personal" else "Organization"


def template_out(db: Session, ctx: OrgContext, t: Template, tv: TemplateVersion | None = None, favs: set | None = None, compat: tuple[int, int] | None = None) -> dict:
    tv = tv or version_of(db, t)
    sup = supported_formats(db, tv)
    favs = favs if favs is not None else set(db.scalars(select(TemplateFavorite.template_id).where(TemplateFavorite.user_id == ctx.user.id, TemplateFavorite.organization_id == ctx.org_id)))
    d = {"id": str(t.id), "name": t.name, "description": t.description, "owner_type": t.owner_type, "owner": owner_label(t, ctx), "visibility": t.visibility,
         "category": t.category, "style_tags": t.style_tags, "layout_tags": t.layout_tags, "status": t.status, "current_version": t.current_version,
         "source": tv.definition.get("source", "manual"), "application_modes": tv.application_modes, "supported_formats": sup, "favorite": t.id in favs,
         "can_edit": can_manage(ctx, t), "thumbnail_asset_id": str(t.thumbnail_asset_id) if t.thumbnail_asset_id else None,
         "created_at": t.created_at.isoformat() if t.created_at else None, "updated_at": t.updated_at.isoformat() if t.updated_at else None}
    if compat:
        d["compatible"] = any((f["width"], f["height"]) == compat for f in sup)
    return d


# ---------------------------------------------------------------- list / search / filter
def search(db: Session, ctx: OrgContext, *, q: str | None = None, tab: str = "all", industry: str | None = None, style: str | None = None, layout: str | None = None,
           aspect: str | None = None, width: int | None = None, height: int | None = None, owner: str | None = None, source: str | None = None,
           compatible_with: tuple[int, int] | None = None, favorites: bool = False, status: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    library.ensure_system_templates(db)
    rows = list(db.scalars(select(Template).where(visible_filter(ctx)).order_by(Template.owner_type != "system", Template.name)))
    favs = set(db.scalars(select(TemplateFavorite.template_id).where(TemplateFavorite.user_id == ctx.user.id, TemplateFavorite.organization_id == ctx.org_id)))
    versions = {v.template_id: v for v in db.scalars(select(TemplateVersion).where(TemplateVersion.template_id.in_([t.id for t in rows])))
                if any(t.id == v.template_id and t.current_version == v.version for t in rows)} if rows else {}
    out = []
    ql = (q or "").strip().lower()
    for t in rows:
        tv = versions.get(t.id)
        if not tv:
            continue
        if (status or "published") != "all" and t.status != (status or "published"):
            continue
        if tab == "mine" and not (t.owner_type != "system" and (t.owner_type == "personal" or t.created_by == ctx.user.id)):
            continue
        if tab == "layouts" and not t.layout_tags:
            continue
        if tab == "styles" and not t.style_tags:
            continue
        if tab == "industry" and t.category == "general":
            continue
        if industry and t.category != industry:
            continue
        if style and style not in t.style_tags:
            continue
        if layout and layout not in t.layout_tags:
            continue
        if owner and t.owner_type != owner:
            continue
        if source and tv.definition.get("source", "manual") != source:
            continue
        if favorites and t.id not in favs:
            continue
        d = template_out(db, ctx, t, tv, favs, compatible_with)
        sup = d["supported_formats"]
        if aspect and not any(f["aspect_class"] == aspect for f in sup):
            continue
        if width and height and not any((f["width"], f["height"]) == (width, height) for f in sup):
            continue
        if compatible_with and not d["compatible"]:
            continue
        if ql:
            hay = " ".join([t.name, t.description, t.category, *t.style_tags, *t.layout_tags, *(f["format_id"] for f in sup), *(f["aspect_class"] for f in sup)]).lower()
            if not all(tok in hay for tok in ql.split()):
                continue
        out.append(d)
    return {"total": len(out), "items": out[offset:offset + limit], "filters": {"industries": S.INDUSTRIES, "styles": S.STYLES, "layouts": S.LAYOUTS}}


# ---------------------------------------------------------------- previews / thumbnails (deterministic render of the blueprint itself)
def layout_preview_spec(layout_def: dict, definition: dict, W: int, H: int) -> dict:
    sample = {**library.SAMPLE, **definition.get("sample", {})}
    bg = layout_def.get("background") or {"type": "solid", "color": "#ffffff"}
    els = []
    for s in sorted(layout_def["slots"], key=lambda s: s["z"]):
        t = s["transform"]
        box = {"x": round(t["x"] * W, 2), "y": round(t["y"] * H, 2), "w": max(1.0, round(t["w"] * W, 2)), "h": max(1.0, round(t["h"] * H, 2)), "rotation": t.get("rotation", 0)}
        st = {k: v for k, v in s["default_style"].items() if k in S.STYLE_KEYS[s["kind"]]}
        base = {"id": s["id"][:40], "slot": s["role"], "name": s["role"], **box}
        if s["kind"] == "text":
            txt = (s["content_policy"].get("text") if s["content_policy"]["mode"] == "static" else sample.get(s["role"], "Sample text")) or "Sample text"
            els.append({**base, "type": "text", "role": "headline" if "headline" in s["role"] else "body", "text": txt, **st})
        elif s["kind"] == "button":
            els.append({**base, "type": "button", "role": "cta", "label": sample.get(s["role"], "Shop now")[:30], "font_size": st.pop("font_size", 14), **st})
        elif s["kind"] == "shape":
            els.append({**base, "type": "shape", "role": "badge" if s["role"] == "promotion_badge" else "decor", **st})
        elif s["asset_policy"]["mode"] == "static":
            els.append({**base, "type": "image", "role": "decor", "asset_id": s["asset_policy"]["asset_id"], **st})
        else:    # product / logo placeholders are neutral shapes: a template preview never shows anyone's product
            logo = s["role"] == "brand_logo"
            els.append({**base, "type": "shape", "role": "logo" if logo else "product", "shape": "rect", "fill": "#94a3b8" if logo else "#cbd5e1", "radius": 4 if logo else 14, "opacity": 0.9})
    return CreativeSpec.model_validate({"canvas": {"width": W, "height": H}, "background": {**bg, "scrims": bg.get("scrims", [])}, "elements": els}).model_dump(mode="json")


def thumbnail(db: Session, ctx: OrgContext, storage: Storage, t: Template, tv: TemplateVersion, format_id: str) -> dict:
    """Render (once, cached per organisation by version+format) the blueprint with neutral placeholders; returns a signed URL."""
    lay = layout_for_size(db, tv, *(int(x) for x in format_id.split("x"))) if "x" in format_id else None
    if not lay:
        raise TemplateError(404, "format_unsupported", f"This template has no {format_id} layout")
    key = hashlib.sha256(f"{ctx.org_id}|{tv.id}|{format_id}|{THUMB_VERSION}".encode()).hexdigest()
    repo = OrgRepo(db, CreativeAsset, ctx.org_id)
    a = next(iter(repo.list(kind="template_preview", cache_key=key)), None)
    if not a:
        spec = CreativeSpec.model_validate(layout_preview_spec(lay["definition"], tv.definition, lay["width"], lay["height"]))
        res = render_spec(spec, make_resolver(db, ctx.org_id, storage))
        a = _store(db, storage, ctx.org_id, res.png, "image/png", "template_preview", f"templates/{tv.id}", cache_key=key, provider="local", model="resvg",
                   params={"template_version": str(tv.id), "format": format_id})
        db.flush()
    return {"asset_id": str(a.id), "url": storage.signed_url(a.storage_key, 3600), "width": a.width, "height": a.height, "format_id": format_id}


def default_format(sup: list[dict]) -> str | None:
    if not sup:
        return None
    ids = {f["format_id"] for f in sup}
    for pref in ("1080x1080", "300x250", "1080x1350"):
        if pref in ids:
            return pref
    return sup[0]["format_id"]


def attach_thumbnails(db: Session, ctx: OrgContext, storage: Storage, items: list[dict], compat: tuple[int, int] | None = None) -> None:
    for it in items:
        fid = f"{compat[0]}x{compat[1]}" if compat and it.get("compatible") else default_format(it["supported_formats"])
        try:
            t = db.get(Template, uuid.UUID(it["id"]))
            it["thumbnail"] = thumbnail(db, ctx, storage, t, version_of(db, t, it["current_version"]), fid) if fid else None
        except Exception:          # a broken blueprint must not take the whole library down
            it["thumbnail"] = None


# ---------------------------------------------------------------- lifecycle
def set_status(db: Session, ctx: OrgContext, t: Template, status: str) -> Template:
    require_manage(ctx, t)
    if status == "published" and t.status == "archived":
        raise TemplateError(409, "template_archived", "Archived templates cannot be published again; duplicate it instead")
    t.status = status
    audit(db, ctx, f"template.{status}", "template", template_id=str(t.id))
    db.flush()
    return t


def update_meta(db: Session, ctx: OrgContext, t: Template, changes: dict) -> Template:
    require_manage(ctx, t)
    vis = changes.pop("visibility", None)
    for k, v in changes.items():
        setattr(t, k, v)
    if vis and vis != t.visibility:
        if vis == "organization":
            if t.owner_type != "personal" or t.owner_id != ctx.user.id:
                raise TemplateError(403, "forbidden", "Only the owner can share a personal template")
            t.owner_type, t.visibility, t.owner_id = "organization", "organization", ctx.org_id
            audit(db, ctx, "template.shared", "template", template_id=str(t.id), to="organization")
        elif vis == "personal":
            if not is_manager(ctx) and t.created_by != ctx.user.id:
                raise TemplateError(403, "forbidden", "Only a template manager can make an organization template personal")
            t.owner_type, t.visibility, t.owner_id = "personal", "personal", t.created_by or ctx.user.id
        else:
            raise TemplateError(422, "invalid_visibility", "Visibility must be personal or organization")
    db.flush()
    return t


def set_favorite(db: Session, ctx: OrgContext, t: Template, on: bool) -> None:
    ex = db.scalar(select(TemplateFavorite).where(TemplateFavorite.template_id == t.id, TemplateFavorite.user_id == ctx.user.id, TemplateFavorite.organization_id == ctx.org_id))
    if on and not ex:
        db.add(TemplateFavorite(organization_id=ctx.org_id, template_id=t.id, user_id=ctx.user.id))
    elif not on and ex:
        db.delete(ex)
    db.flush()


def usage(db: Session, ctx: OrgContext, t: Template) -> dict:
    rows = list(db.scalars(select(TemplateUsage).where(TemplateUsage.template_id == t.id, TemplateUsage.organization_id == ctx.org_id).order_by(TemplateUsage.created_at.desc())))
    by_mode: dict[str, int] = {}
    for r in rows:
        by_mode[r.application_mode] = by_mode.get(r.application_mode, 0) + 1
    return {"template_id": str(t.id), "total": len(rows), "by_mode": by_mode, "last_used_at": rows[0].created_at.isoformat() if rows else None,
            "recent": [{"id": str(r.id), "mode": r.application_mode, "document_id": str(r.document_id) if r.document_id else None, "user_id": str(r.user_id) if r.user_id else None,
                        "created_at": r.created_at.isoformat()} for r in rows[:20]]}


# ---------------------------------------------------------------- authoring
def _new_template(db: Session, ctx: OrgContext, *, name, description, category, style_tags, layout_tags, visibility) -> Template:
    require_author(ctx)
    if visibility not in ("personal", "organization"):
        raise TemplateError(422, "invalid_visibility", "Visibility must be personal or organization")
    if category not in S.INDUSTRIES:
        raise TemplateError(422, "invalid_category", f"Industry must be one of {S.INDUSTRIES}")
    for tags, allowed, what in ((style_tags, S.STYLES, "style"), (layout_tags, S.LAYOUTS, "layout")):
        bad = [x for x in tags if x not in allowed]
        if bad:
            raise TemplateError(422, f"invalid_{what}", f"Unknown {what} tag(s): {bad}")
    t = Template(organization_id=ctx.org_id, name=name.strip(), description=description, owner_type=visibility, visibility=visibility,
                 owner_id=ctx.user.id if visibility == "personal" else ctx.org_id, category=category, style_tags=style_tags, layout_tags=layout_tags,
                 status="published", current_version=1, created_by=ctx.user.id)
    db.add(t)
    db.flush()
    return t


def create_template(db: Session, ctx: OrgContext, storage: Storage, *, name, description, category, style_tags, layout_tags, visibility, definition, layouts,
                    brand_constraints=None, publish=True) -> Template:
    t = _new_template(db, ctx, name=name, description=description, category=category, style_tags=style_tags, layout_tags=layout_tags, visibility=visibility)
    _check_layout_assets(db, ctx, layouts)
    try:
        tv = library.create_version_rows(db, ctx.org_id, t, 1, {**definition, "source": definition.get("source", "manual")}, layouts, ctx.user.id, brand_constraints=brand_constraints, note="Created")
    except Exception as e:
        raise TemplateError(422, "invalid_template", str(e)[:400]) from e
    t.status = "published" if publish else "draft"
    _register_assets(db, ctx, tv, layouts)
    audit(db, ctx, "template.created", "template", template_id=str(t.id), visibility=visibility)
    _finish_thumbnail(db, ctx, storage, t, tv)
    return t


def add_version(db: Session, ctx: OrgContext, storage: Storage, t: Template, *, definition, layouts, brand_constraints=None, note="") -> TemplateVersion:
    require_manage(ctx, t)
    _check_layout_assets(db, ctx, layouts)
    try:
        tv = library.create_version_rows(db, t.organization_id, t, t.current_version + 1, {**definition, "source": definition.get("source", "manual")}, layouts, ctx.user.id,
                                         brand_constraints=brand_constraints, note=note)
    except Exception as e:
        raise TemplateError(422, "invalid_template", str(e)[:400]) from e
    t.current_version = tv.version        # existing documents keep pointing at the version they were built from; versions are immutable
    _register_assets(db, ctx, tv, layouts)
    audit(db, ctx, "template.version_added", "template", template_id=str(t.id), version=tv.version)
    _finish_thumbnail(db, ctx, storage, t, tv)
    return tv


def versions(db: Session, t: Template) -> list[dict]:
    rows = db.scalars(select(TemplateVersion).where(TemplateVersion.template_id == t.id).order_by(TemplateVersion.version.desc()))
    return [{"version": v.version, "schema_version": v.schema_version, "change_note": v.change_note, "application_modes": v.application_modes,
             "created_by": str(v.created_by) if v.created_by else None, "created_at": v.created_at.isoformat(), "current": v.version == t.current_version,
             "formats": [f["format_id"] for f in supported_formats(db, v)]} for v in rows]


def _static_asset_ids(layouts: dict[str, dict]) -> set[uuid.UUID]:
    ids: set[uuid.UUID] = set()
    for raw in layouts.values():
        l = S.LayoutDef.model_validate(raw)
        if l.background and l.background.asset_id:
            ids.add(l.background.asset_id)
        ids |= {s.asset_policy.asset_id for s in l.slots if s.asset_policy.asset_id}
    return ids


def _check_layout_assets(db: Session, ctx: OrgContext, layouts: dict[str, dict]) -> None:
    """Template-owned assets must be THIS organisation's reusable creative assets (never customer product uploads, never another tenant's)."""
    try:
        ids = _static_asset_ids(layouts)
    except Exception as e:
        raise TemplateError(422, "invalid_template", str(e)[:300]) from e
    for aid in ids:
        if not OrgRepo(db, CreativeAsset, ctx.org_id).get(aid):
            raise TemplateError(422, "asset_not_allowed", f"Asset {aid} cannot be used in a template (not found or not reusable)")


def _register_assets(db: Session, ctx: OrgContext, tv: TemplateVersion, layouts: dict[str, dict]) -> None:
    for aid in _static_asset_ids(layouts):
        db.add(TemplateAsset(organization_id=ctx.org_id, template_version_id=tv.id, asset_id=aid, role="background_or_decor", asset_type="image", ownership="organization", reusable=True))
    db.flush()


def _finish_thumbnail(db: Session, ctx: OrgContext, storage: Storage, t: Template, tv: TemplateVersion) -> None:
    fid = default_format(supported_formats(db, tv))
    if fid:
        try:
            t.thumbnail_asset_id = uuid.UUID(thumbnail(db, ctx, storage, t, tv, fid)["asset_id"])
        except Exception:
            pass
    db.flush()


def duplicate(db: Session, ctx: OrgContext, storage: Storage, t: Template, *, name: str | None, visibility: str) -> Template:
    require_author(ctx)
    tv = version_of(db, t)
    layouts = raw_layouts(db, tv)
    new = _new_template(db, ctx, name=(name or f"{t.name} (copy)")[:200], description=t.description, category=t.category, style_tags=list(t.style_tags),
                        layout_tags=list(t.layout_tags), visibility=visibility)
    definition = {**tv.definition, "source": "duplicated", "source_template_id": str(t.id)}
    _check_layout_assets(db, ctx, layouts)
    ntv = library.create_version_rows(db, ctx.org_id, new, 1, definition, layouts, ctx.user.id, modes=tv.application_modes, brand_constraints=tv.brand_constraints, note=f"Duplicated from {t.name} v{tv.version}")
    _register_assets(db, ctx, ntv, layouts)
    audit(db, ctx, "template.duplicated", "template", template_id=str(new.id), source_template_id=str(t.id))
    _finish_thumbnail(db, ctx, storage, new, ntv)
    return new


# ---------------------------------------------------------------- save a design as a template
@dataclass
class SaveOptions:
    name: str
    description: str = ""
    category: str = "general"
    style_tags: list[str] = None            # type: ignore[assignment]
    layout_tags: list[str] = None           # type: ignore[assignment]
    visibility: str = "personal"
    formats: list[str] = None               # type: ignore[assignment]   # extra exact sizes ("WxH") with the same aspect ratio
    include_background_assets: bool = False
    include_decorative_assets: bool = False
    preserve_brand_styling: bool = True
    new_version_of: uuid.UUID | None = None


def design_to_layout(db: Session, ctx: OrgContext, doc: dict, opts: SaveOptions) -> tuple[dict, list[str], list[dict]]:
    """Turn one design into a LayoutDef: semantic slots, placeholders instead of copy, no product/customer assets. Returns (layout, notes, static assets)."""
    W, H = doc["canvas"]["width"], doc["canvas"]["height"]
    els = doc["elements"]
    roles, _ = mapping.assign_slots(els)
    notes: list[str] = []
    slots: list[dict] = []
    assets: list[dict] = []
    used_roles: set[str] = set()
    n_decor = 0
    for z, e in enumerate(els):
        if not e.get("visible", True):
            continue
        r = roles.get(e["id"])
        kind = e["type"]
        if r and r not in mapping.ALLOWED_KINDS or (r and kind not in mapping.ALLOWED_KINDS[r]):
            r = None
        if r in used_roles:
            r = None
        if not r:
            if kind == "shape":
                r = "decorative_shape"
            elif kind == "image" and e.get("role") == "decor":
                r = "decorative_shape"
            else:
                notes.append(f"“{e.get('name') or e['id']}” has no semantic role and was left out of the template")
                continue
        if r != "decorative_shape":
            used_roles.add(r)
        sid = r if r != "decorative_shape" else f"decor_{(n_decor := n_decor + 1)}"
        slot: dict = {"id": sid, "role": r, "kind": kind, "required": r in ("headline_primary", "product_primary", "primary_cta"), "z": min(100, z),
                      "transform": {"x": round(e["x"] / W, 5), "y": round(e["y"] / H, 5), "w": round(e["w"] / W, 5), "h": round(e["h"] / H, 5), "rotation": e.get("rotation", 0)}}
        style_keys = S.STYLE_KEYS[kind]
        style = {k: v for k, v in e.items() if k in style_keys and v is not None}
        if not opts.preserve_brand_styling:
            for k in ("font_family", "color", "fill", "text_color", "stroke"):
                style.pop(k, None)
        slot["default_style"] = style
        if kind in ("text", "button"):
            slot["content_policy"] = {"mode": "placeholder", "placeholder": "{{" + (r if r != "decorative_shape" else "text") + "}}"}
            slot["constraints"] = {"min_font_size": e.get("min_font_size"), **({"max_lines": e["max_lines"]} if kind == "text" else {})}
        elif kind == "image":
            if r == "decorative_shape":
                aid = uuid.UUID(e["asset_id"])
                if not opts.include_decorative_assets:
                    notes.append(f"Decorative image “{e.get('name') or e['id']}” left out (decorative assets not included)")
                    continue
                if OrgRepo(db, ProductAsset, ctx.org_id).get(aid) or not OrgRepo(db, CreativeAsset, ctx.org_id).get(aid):
                    notes.append(f"Image “{e.get('name') or e['id']}” is a product/customer upload and cannot be shared in a template")
                    continue
                slot["asset_policy"] = {"mode": "static", "asset_id": str(aid)}
                assets.append({"asset_id": aid, "role": "decorative"})
            else:
                slot["asset_policy"] = {"mode": "from_document"}      # product / logo come from the campaign, never from the template
                slot["constraints"] = {"image_fit": e.get("fit", "contain") if e.get("fit") != "fill" else "contain"}
        slots.append(slot)
    if "primary_cta" not in used_roles:
        pass
    bg = doc["background"]
    lbg = {"type": bg["type"], "color": bg["color"], "gradient_to": bg.get("gradient_to"), "angle": bg.get("angle", 180), "fit": bg.get("fit", "cover"), "scrims": bg.get("scrims", [])}
    if bg["type"] == "image":
        aid = bg.get("asset_id")
        ok = bool(aid) and opts.include_background_assets and OrgRepo(db, CreativeAsset, ctx.org_id).get(uuid.UUID(aid)) and not OrgRepo(db, ProductAsset, ctx.org_id).get(uuid.UUID(aid))
        if ok:
            lbg["asset_id"] = aid
            assets.append({"asset_id": uuid.UUID(aid), "role": "background"})
        else:
            lbg = {**lbg, "type": "solid", "asset_id": None, "gradient_to": None}
            notes.append("Background image not included: it becomes a solid colour in the template")
    if not slots:
        raise TemplateError(422, "nothing_to_save", "This design has no layers that can become template slots")
    return {"background": lbg, "slots": slots}, notes, assets


def save_as_template(db: Session, ctx: OrgContext, storage: Storage, doc_row, opts: SaveOptions) -> dict:
    require_author(ctx)
    doc = doc_row.document
    W, H = doc["canvas"]["width"], doc["canvas"]["height"]
    layout, notes, assets = design_to_layout(db, ctx, doc, opts)
    layouts = {f"{W}x{H}": layout}
    for fid in opts.formats or []:
        try:
            w, h = (int(x) for x in fid.lower().split("x"))
        except ValueError:
            raise TemplateError(422, "invalid_format", f"Invalid format {fid!r}")
        if (w, h) == (W, H):
            continue
        if not formats.valid_custom(w, h):
            raise TemplateError(422, "invalid_format", f"{fid} is outside the supported size range")
        if abs(math.log((w / h) / (W / H))) > 0.03:
            raise TemplateError(422, "format_unsupported", f"{fid} has a different aspect ratio than the source design; templates are never stretched. Save a design in that size instead.")
        scale = h / H
        ov = {}
        for s in layout["slots"]:
            if "font_size" in s["default_style"]:
                size = max(6, round(s["default_style"]["font_size"] * scale, 2))
                ov[s["id"]] = {"default_style": {"font_size": size, "min_font_size": max(6, round(s["default_style"].get("min_font_size", size * 0.6) * scale, 2))}}
        layouts[f"{w}x{h}"] = {"extends": f"{W}x{H}", "slot_overrides": ov}
    definition = {"palette": list(dict.fromkeys([layout["background"]["color"]] + [s["default_style"].get(k) for s in layout["slots"] for k in ("fill", "color") if s["default_style"].get(k)]))[:8],
                  "fonts": sorted({s["default_style"]["font_family"] for s in layout["slots"] if s["default_style"].get("font_family")})[:3], "notes": "", "sample": {}, "source": "saved_from_design"}
    if opts.new_version_of:
        t = require_visible(db, ctx, opts.new_version_of)
        require_manage(ctx, t)
        tv = add_version(db, ctx, storage, t, definition=definition, layouts=layouts, note=f"New version saved from a design")
    else:
        t = create_template(db, ctx, storage, name=opts.name, description=opts.description, category=opts.category, style_tags=opts.style_tags or [], layout_tags=opts.layout_tags or [],
                            visibility=opts.visibility, definition=definition, layouts=layouts)
        tv = version_of(db, t)
    audit(db, ctx, "template.saved_from_design", "template", template_id=str(t.id), visibility=t.visibility)
    return {"template": template_out(db, ctx, t, tv), "notes": notes, "included_assets": [{"asset_id": str(a["asset_id"]), "role": a["role"]} for a in assets]}
