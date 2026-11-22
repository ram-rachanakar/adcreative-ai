"""Built-in (system) templates: declarative blueprints with explicit per-format layouts. No images are stored; backgrounds are solid/gradient so a
system template works for every organization. Seeding is idempotent (keyed by system_key) and the only writer of organization-less rows."""
import copy
import math
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.creative import formats
from app.models import Template, TemplateLayout, TemplateVersion
from app.templates import schema as S

Box = tuple[float, float, float, float, float]   # x, y, w, h (fractions of the canvas), font size as a fraction of canvas height (0 for non-text)
ROLES = ["logo", "headline", "body", "product", "cta"]


def size_class(w: int, h: int) -> str:
    r = w / h
    return "wide" if r >= 3 else "land2" if r >= 1.5 else "land" if r >= 1.15 else "square" if r >= 0.9 else "portrait" if r >= 0.6 else "tall"


# family -> class -> role -> box. A family/class that is missing means "this template does not support those sizes" (never stretched).
LAND2: dict[str, Box] = {"logo": (0.04, 0.07, 0.14, 0.12, 0), "headline": (0.04, 0.22, 0.5, 0.34, 0.12), "body": (0.04, 0.58, 0.46, 0.14, 0.055),
                         "cta": (0.04, 0.76, 0.22, 0.16, 0.065), "product": (0.6, 0.08, 0.36, 0.84, 0)}
FAMILIES: dict[str, dict[str, dict[str, Box]]] = {
    "stack": {
        "square": {"logo": (0.38, 0.04, 0.24, 0.08, 0), "product": (0.2, 0.14, 0.6, 0.42, 0), "headline": (0.1, 0.58, 0.8, 0.14, 0.06), "body": (0.15, 0.73, 0.7, 0.07, 0.03), "cta": (0.32, 0.84, 0.36, 0.09, 0.035)},
        "land": {"logo": (0.03, 0.03, 0.2, 0.1, 0), "product": (0.28, 0.05, 0.44, 0.45, 0), "headline": (0.06, 0.52, 0.88, 0.17, 0.09), "body": (0.1, 0.7, 0.8, 0.1, 0.05), "cta": (0.32, 0.83, 0.36, 0.12, 0.06)},
        "portrait": {"logo": (0.35, 0.04, 0.3, 0.06, 0), "product": (0.12, 0.12, 0.76, 0.44, 0), "headline": (0.08, 0.58, 0.84, 0.13, 0.05), "body": (0.12, 0.72, 0.76, 0.07, 0.022), "cta": (0.28, 0.83, 0.44, 0.07, 0.028)},
        "tall": {"logo": (0.3, 0.05, 0.4, 0.05, 0), "product": (0.1, 0.14, 0.8, 0.36, 0), "headline": (0.08, 0.53, 0.84, 0.12, 0.04), "body": (0.1, 0.66, 0.8, 0.08, 0.018), "cta": (0.2, 0.8, 0.6, 0.06, 0.024)},
        "wide": {"logo": (0.92, 0.2, 0.07, 0.6, 0), "product": (0.01, 0.08, 0.12, 0.84, 0), "headline": (0.14, 0.1, 0.5, 0.8, 0.3), "cta": (0.68, 0.2, 0.22, 0.6, 0.26)},
        "land2": LAND2,
    },
    "side": {
        "square": {"logo": (0.06, 0.06, 0.2, 0.08, 0), "headline": (0.06, 0.2, 0.46, 0.3, 0.065), "body": (0.06, 0.52, 0.44, 0.16, 0.032), "cta": (0.06, 0.76, 0.3, 0.09, 0.035), "product": (0.54, 0.14, 0.42, 0.72, 0)},
        "land": {"logo": (0.05, 0.06, 0.22, 0.1, 0), "headline": (0.05, 0.2, 0.5, 0.3, 0.1), "body": (0.05, 0.52, 0.5, 0.14, 0.055), "cta": (0.05, 0.75, 0.3, 0.14, 0.065), "product": (0.58, 0.1, 0.38, 0.8, 0)},
        "portrait": {"logo": (0.06, 0.04, 0.28, 0.05, 0), "headline": (0.06, 0.12, 0.88, 0.16, 0.05), "body": (0.06, 0.29, 0.88, 0.08, 0.022), "product": (0.1, 0.4, 0.8, 0.42, 0), "cta": (0.06, 0.86, 0.5, 0.07, 0.028)},
        "tall": {"logo": (0.06, 0.04, 0.35, 0.04, 0), "headline": (0.07, 0.1, 0.86, 0.14, 0.04), "body": (0.07, 0.25, 0.86, 0.08, 0.018), "product": (0.08, 0.36, 0.84, 0.4, 0), "cta": (0.15, 0.82, 0.7, 0.06, 0.024)},
        "wide": {"logo": (0.01, 0.2, 0.07, 0.6, 0), "headline": (0.1, 0.1, 0.42, 0.8, 0.3), "cta": (0.55, 0.2, 0.2, 0.6, 0.25), "product": (0.8, 0.05, 0.18, 0.9, 0)},
        "land2": LAND2,
    },
    "hero": {   # full-bleed look: text over the top/left, product low; no strip support
        "square": {"headline": (0.06, 0.06, 0.6, 0.26, 0.075), "body": (0.06, 0.34, 0.5, 0.14, 0.034), "product": (0.45, 0.34, 0.5, 0.6, 0), "cta": (0.06, 0.8, 0.3, 0.09, 0.036), "logo": (0.06, 0.9, 0.18, 0.06, 0)},
        "land": {"headline": (0.06, 0.08, 0.55, 0.3, 0.1), "body": (0.06, 0.4, 0.5, 0.14, 0.055), "cta": (0.06, 0.7, 0.3, 0.14, 0.065), "product": (0.56, 0.18, 0.4, 0.74, 0), "logo": (0.06, 0.88, 0.18, 0.09, 0)},
        "portrait": {"headline": (0.07, 0.05, 0.86, 0.2, 0.055), "body": (0.07, 0.26, 0.7, 0.1, 0.022), "product": (0.15, 0.4, 0.7, 0.42, 0), "cta": (0.07, 0.86, 0.5, 0.065, 0.028), "logo": (0.7, 0.88, 0.23, 0.05, 0)},
        "tall": {"headline": (0.07, 0.06, 0.86, 0.16, 0.042), "body": (0.07, 0.23, 0.8, 0.08, 0.018), "product": (0.1, 0.36, 0.8, 0.4, 0), "cta": (0.15, 0.82, 0.7, 0.06, 0.024), "logo": (0.3, 0.92, 0.4, 0.04, 0)},
        "land2": LAND2,
    },
}
ROLE_TO_SLOT = {"logo": ("brand_logo", "image"), "headline": ("headline_primary", "text"), "body": ("body_copy", "text"), "product": ("product_primary", "image"), "cta": ("primary_cta", "button")}


@dataclass
class Arch:
    key: str
    name: str
    description: str
    family: str
    industry: str
    styles: list[str]
    layouts: list[str]
    bg: dict
    head: dict            # text style for the headline
    body: dict
    cta: dict
    serif: bool = False
    decor: str = "none"   # none | bar | badge | overlay
    accent: str = "#ff5a1f"
    flip: bool = False


ARCHETYPES: list[Arch] = [
    Arch("minimal-centered", "Minimal Centered", "Clean white canvas, centred product, short headline and a quiet CTA.", "stack", "general", ["minimal"], ["centered"],
         {"type": "solid", "color": "#ffffff"}, {"color": "#111111", "align": "center", "font_weight": 700}, {"color": "#444444", "align": "center"}, {"fill": "#111111", "text_color": "#ffffff", "radius": 6}),
    Arch("modern-split", "Modern Split", "Dark gradient with the message on the left and the product on the right.", "side", "electronics", ["modern"], ["split", "product-focused"],
         {"type": "gradient", "color": "#0f172a", "gradient_to": "#1e293b", "angle": 135}, {"color": "#ffffff", "font_weight": 700}, {"color": "#cbd5e1"}, {"fill": "#38bdf8", "text_color": "#0f172a", "radius": 8}, decor="bar", accent="#38bdf8"),
    Arch("luxury-dark", "Luxury Noir", "Deep black-to-bronze backdrop, serif capitals and a gold CTA.", "stack", "beauty", ["luxury"], ["centered"],
         {"type": "gradient", "color": "#111111", "gradient_to": "#2a2118", "angle": 180}, {"color": "#e8d5a8", "align": "center", "uppercase": True, "letter_spacing": 2, "font_weight": 400},
         {"color": "#cdbb94", "align": "center"}, {"fill": "#c9a24b", "text_color": "#111111", "radius": 2, "uppercase": True}, serif=True),
    Arch("bold-promo", "Bold Promo", "High-impact yellow sale layout with the product on the left and a promo badge.", "side", "general", ["bold", "promotional"], ["split", "product-focused"],
         {"type": "solid", "color": "#ffd400"}, {"color": "#111111", "font_weight": 700, "uppercase": True}, {"color": "#222222"}, {"fill": "#111111", "text_color": "#ffffff", "radius": 4, "uppercase": True}, decor="badge", accent="#e11d48", flip=True),
    Arch("editorial-left", "Editorial", "Warm paper tone, serif headline, generous white space.", "side", "fashion", ["editorial"], ["text-focused"],
         {"type": "solid", "color": "#f4efe6"}, {"color": "#1c1c1c", "font_weight": 700}, {"color": "#3a3a3a"}, {"fill": "#1c1c1c", "text_color": "#f4efe6", "radius": 0}, serif=True),
    Arch("hero-fullbleed", "Hero Full-bleed", "Photographic look: text over a dark overlay with the product low and large.", "hero", "fitness", ["modern", "bold"], ["full-bleed", "product-focused"],
         {"type": "gradient", "color": "#111827", "gradient_to": "#374151", "angle": 160}, {"color": "#ffffff", "font_weight": 700, "uppercase": True}, {"color": "#e5e7eb"}, {"fill": "#f97316", "text_color": "#111111", "radius": 6}, decor="overlay", accent="#f97316"),
    Arch("corporate-clean", "Corporate Clean", "Light, trustworthy layout with a blue accent bar.", "side", "services", ["corporate"], ["split", "text-focused"],
         {"type": "solid", "color": "#f8fafc"}, {"color": "#0b3d91", "font_weight": 700}, {"color": "#334155"}, {"fill": "#0b3d91", "text_color": "#ffffff", "radius": 6}, decor="bar", accent="#0b3d91"),
    Arch("fresh-food", "Fresh Food", "Warm cream background with a big product and an appetising red CTA.", "stack", "food", ["promotional"], ["product-focused", "centered"],
         {"type": "solid", "color": "#fff4e0"}, {"color": "#7a1f00", "align": "center", "font_weight": 700}, {"color": "#5b3a29", "align": "center"}, {"fill": "#d62828", "text_color": "#ffffff", "radius": 24}, decor="badge", accent="#d62828"),
    Arch("home-warm", "Home Warm", "Soft linen tones with a serif headline for home and living.", "stack", "home", ["minimal"], ["centered"],
         {"type": "solid", "color": "#efe7dc"}, {"color": "#3b2f2f", "align": "center", "font_weight": 700}, {"color": "#5c4b4b", "align": "center"}, {"fill": "#3b2f2f", "text_color": "#efe7dc", "radius": 4}, serif=True),
]
SAMPLE = {"headline_primary": "Your headline here", "body_copy": "A short supporting message", "primary_cta": "Shop now"}


def _slots(a: Arch, cls: str, W: int, H: int) -> list[dict] | None:
    table = FAMILIES[a.family].get(cls)
    if not table:
        return None
    fam = "Liberation Serif" if a.serif else "Liberation Sans"
    out: list[dict] = []
    z = {"logo": 6, "headline": 4, "body": 5, "product": 3, "cta": 7}
    for role, box in table.items():
        x, y, w, h, fs = box
        if a.flip and a.family == "side" and cls in ("square", "land", "land2", "wide"):   # mirror horizontally: product left, text right
            x = round(1 - x - w, 4)
        slot_role, kind = ROLE_TO_SLOT[role]
        s: dict = {"id": role, "role": slot_role, "kind": kind, "required": role in ("headline", "product", "cta"), "z": z[role],
                   "transform": {"x": x, "y": y, "w": w, "h": h, "rotation": 0}}
        if kind == "text":
            base = a.head if role == "headline" else a.body
            size = max(8, round(fs * H))
            s["default_style"] = {"font_family": fam, "font_size": size, "min_font_size": max(8, round(size * 0.6)), "max_lines": 3 if role == "headline" else 2,
                                  "auto_fit": True, "valign": "middle" if role == "headline" else "top", "align": "left", **base}
            if a.flip and a.family == "side" and base.get("align", "left") == "left":
                s["default_style"]["align"] = "left"
            s["constraints"] = {"min_font_size": 8, "max_lines": 3 if role == "headline" else 2}
        elif kind == "button":
            size = max(8, round(fs * H))
            s["default_style"] = {"font_family": fam, "font_size": size, "min_font_size": max(8, round(size * 0.6)), "font_weight": 700, **a.cta}
            s["constraints"] = {"min_font_size": 8}
        else:
            s["default_style"] = {"fit": "contain"} if role == "product" else {"fit": "contain"}
            s["constraints"] = {"min_w_px": 24} if role == "logo" else {"image_fit": "contain"}
        out.append(s)
    # decoration (no campaign content): added in layout+style / full
    def deco(sid, role, kind, tr, style, z_):
        out.append({"id": sid, "role": role, "kind": kind, "required": False, "z": z_, "transform": {**tr, "rotation": 0}, "default_style": style})
    if a.decor == "bar" and cls in ("square", "land", "land2", "portrait", "tall"):
        deco("accent_bar", "decorative_shape", "shape", {"x": 0, "y": 0, "w": 1, "h": round(max(6 / H, 0.012), 4)}, {"fill": a.accent, "shape": "rect"}, 1)
    elif a.decor == "badge" and cls in ("square", "land", "portrait", "tall"):
        d = 0.2 if cls in ("square", "portrait", "tall") else 0.28
        w = d if cls != "land" else 0.16
        h = round(w * W / H, 4)
        deco("promo_badge", "promotion_badge", "shape", {"x": round(0.97 - w, 4), "y": 0.04, "w": w, "h": h}, {"fill": a.accent, "shape": "ellipse"}, 2)
    elif a.decor == "overlay":
        deco("dark_overlay", "decorative_shape", "shape", {"x": 0, "y": 0, "w": 1, "h": 1}, {"fill": "#000000", "shape": "rect", "opacity": 0.28}, 1)
    return out


def build_layouts(a: Arch) -> dict[str, dict]:
    """format key ("WxH") -> LayoutDef dict. Sizes of the same class and (nearly) the same ratio extend the first one with scaled type."""
    sizes = sorted({(f.width, f.height) for f in formats.registry().formats})
    primary: dict[str, tuple[int, int]] = {}
    layouts: dict[str, dict] = {}
    for W, H in sizes:
        cls = size_class(W, H)
        slots = _slots(a, cls, W, H)
        if not slots:
            continue
        key = f"{W}x{H}"
        base = primary.get(cls)
        if base and abs(math.log((W / H) / (base[0] / base[1]))) < 0.03:
            bslots = {s["id"]: s for s in _slots(a, cls, *base) or []}
            scale = H / base[1]
            ov = {}
            for s in slots:
                if "font_size" in s["default_style"]:
                    size = max(8, round(bslots[s["id"]]["default_style"]["font_size"] * scale))
                    ov[s["id"]] = {"default_style": {"font_size": size, "min_font_size": max(8, round(size * 0.6))}}
            layouts[key] = {"extends": f"{base[0]}x{base[1]}", "slot_overrides": ov}
            continue
        primary.setdefault(cls, (W, H))
        layouts[key] = {"background": a.bg, "slots": slots}
    return layouts


def definition(a: Arch) -> dict:
    return {"palette": list(dict.fromkeys([a.bg["color"], a.accent, a.head.get("color", "#111111")])), "fonts": ["Liberation Serif" if a.serif else "Liberation Sans"], "notes": a.description, "sample": SAMPLE}


def _sysadmin(db: Session, on: bool) -> None:
    db.execute(text("select set_config('app.system_admin', :v, true)"), {"v": "on" if on else ""})


def create_version_rows(db: Session, org_id, tpl: Template, version: int, definition_: dict, layouts: dict[str, dict], user_id, *, modes=None, brand_constraints=None, note="") -> TemplateVersion:
    """Validates every layout (including inheritance resolution) and writes the immutable version + its layout rows."""
    S.Definition.model_validate(definition_)
    S.BrandConstraints.model_validate(brand_constraints or {})
    if not layouts:
        raise ValueError("a template needs at least one format layout")
    resolved = {}
    for fid in layouts:
        S.validate_layout(layouts[fid])
    for fid in layouts:
        resolved[fid] = S.resolve_layout(layouts, fid)
    tv = TemplateVersion(organization_id=org_id, template_id=tpl.id, version=version, schema_version=S.SCHEMA_VERSION, definition=definition_,
                         brand_constraints=brand_constraints or {}, application_modes=modes or list(S.MODES), change_note=note[:400], created_by=user_id)
    db.add(tv)
    db.flush()
    for fid, raw in layouts.items():
        w, h = (int(v) for v in fid.split("x"))
        db.add(TemplateLayout(organization_id=org_id, template_version_id=tv.id, format_id=fid, width=w, height=h, aspect_ratio=round(w / h, 5), layout_definition=raw))
    db.flush()
    return tv


def ensure_system_templates(db: Session) -> None:
    """Idempotent and cheap (one indexed query when everything exists); also self-heals if the table was truncated."""
    existing = set(db.scalars(select(Template.system_key).where(Template.owner_type == "system")))
    if len(existing) >= len(ARCHETYPES):
        return
    saved = db.info.get("org_id")
    _sysadmin(db, True)
    try:
        for a in ARCHETYPES:
            if a.key in existing:
                continue
            t = Template(system_key=a.key, name=a.name, description=a.description, owner_type="system", visibility="system", organization_id=None, category=a.industry,
                         style_tags=a.styles, layout_tags=a.layouts, status="published", current_version=1)
            db.add(t)
            db.flush()
            create_version_rows(db, None, t, 1, definition(a), build_layouts(a), None, note="Built-in template")
        db.commit()
    except IntegrityError:          # another worker seeded concurrently (unique system_key)
        db.rollback()
    finally:
        _sysadmin(db, False)
        db.info["org_id"] = saved
