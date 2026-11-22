"""Template mapping engine (pure: dict in -> dict out, never touches the DB or the input document).

map_template(doc, layout, options) -> Outcome
  * layers are matched to template slots by SEMANTIC ROLE (explicit `slot`, else inferred from role/type), never by layer id or z-index,
  * three modes: layout_only (geometry + z-order only), layout_style (+ typography/colours/decorative shapes, brand rules win),
    full (+ template background, template-owned assets/wording — product, logo, copy and CTA are replaced only on explicit confirmation),
  * unmapped layers are always preserved; campaign copy is never invented; a different product/logo is never substituted silently.
"""
import copy
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.rendering import fonts
from app.rendering.color import contrast, hex_to_rgb, pick_text_color, rgb_to_hex
from app.rendering.spec import CreativeSpec
from app.rendering.text import fit_text
from app.templates.schema import ALLOWED_KINDS, MODES, STYLE_KEYS

TEMPLATE_PREFIX = "tpl_"
NEUTRALS = {"#ffffff", "#000000", "#111111", "#f5f5f5", "#222222", "#333333"}
COLOR_KEYS = {"color", "fill", "text_color", "stroke"}
CAMPAIGN_ROLES = {"headline_primary", "headline_secondary", "body_copy", "primary_cta", "secondary_cta", "promotion_badge"}
IDENTITY_ROLES = {"product_primary", "product_secondary", "brand_logo"}


class MappingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Options:
    mode: str = "layout_only"
    confirm_replace: set[str] = field(default_factory=set)       # slot roles the user explicitly allows the template to replace
    slot_overrides: dict[str, str] = field(default_factory=dict)  # slot role -> layer id (resolves ambiguity)
    brand: dict = field(default_factory=dict)                      # effective brand profile: colors[], fonts[], min_logo_width
    brand_constraints: dict = field(default_factory=dict)          # template version's own wishes
    safe_zone: tuple[float, float] | None = None                   # (top_pct, bottom_pct) of the target format
    new_document: bool = False                                     # seeded from campaign content: nothing existing to protect


@dataclass
class Outcome:
    candidate: dict
    items: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    ambiguities: list[dict] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    needs_confirmation: list[str] = field(default_factory=list)    # roles whose replacement the user must confirm (full mode)

    def result(self) -> dict:
        counts: dict[str, int] = {}
        for i in self.items:
            counts[i["action"]] = counts.get(i["action"], 0) + 1
        return {"items": self.items, "missing_required": self.missing_required, "ambiguities": self.ambiguities, "unmapped_layers": self.unmapped,
                "needs_confirmation": self.needs_confirmation, "counts": counts}


def warn(out: Outcome, code: str, message: str, layer_id: str | None = None, severity: str = "warning") -> None:
    if not any(w["code"] == code and w["layer_id"] == layer_id and w["message"] == message for w in out.warnings):
        out.warnings.append({"code": code, "severity": severity, "message": message, "layer_id": layer_id})


# ---------------------------------------------------------------- semantic roles of existing layers
def _area(e: dict) -> float:
    return e["w"] * e["h"]


def assign_slots(elements: list[dict]) -> tuple[dict[str, str | None], list[dict]]:
    """layer id -> semantic slot role (or None). Explicit `slot` wins; otherwise inferred deterministically from role/type."""
    slots: dict[str, str | None] = {e["id"]: e.get("slot") for e in elements}
    taken = {s for s in slots.values() if s}
    idx = {e["id"]: i for i, e in enumerate(elements)}

    def fill(cands: list[dict], names: list[str]):
        for e, n in zip(cands, names):
            if n not in taken or slots[e["id"]] == n:
                slots[e["id"]] = n
                taken.add(n)

    free = [e for e in elements if not slots[e["id"]] and not e["id"].startswith(TEMPLATE_PREFIX)]
    fill(sorted([e for e in free if e["type"] == "text" and e["role"] == "headline"], key=lambda e: (-e["font_size"], idx[e["id"]])), ["headline_primary", "headline_secondary"])
    fill(sorted([e for e in free if e["type"] == "text" and e["role"] == "body"], key=lambda e: idx[e["id"]]), ["body_copy"])
    fill(sorted([e for e in free if e["type"] == "button"], key=lambda e: (-_area(e), idx[e["id"]])), ["primary_cta", "secondary_cta"])
    fill(sorted([e for e in free if e["type"] == "image" and e["role"] == "logo"], key=lambda e: idx[e["id"]]), ["brand_logo"])
    fill(sorted([e for e in free if e["type"] == "image" and e["role"] == "product"], key=lambda e: (-_area(e), idx[e["id"]])), ["product_primary", "product_secondary"])
    fill(sorted([e for e in free if e["role"] == "badge"], key=lambda e: idx[e["id"]]), ["promotion_badge"])
    # template-created decoration keeps its role so a later application can recognise (and replace) it
    for e in elements:
        if e["id"].startswith(TEMPLATE_PREFIX) and not slots[e["id"]]:
            slots[e["id"]] = "decorative_shape"
    return slots, []


# ---------------------------------------------------------------- colour helpers (brand rules)
def _dist(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(hex_to_rgb(a), hex_to_rgb(b)))


def brand_color(value: str, palette: list[str]) -> str:
    v = value.lower()
    if not palette or v in NEUTRALS or v in {p.lower() for p in palette}:
        return value
    return min(palette, key=lambda p: _dist(v, p.lower()))


def _backdrop(bg: dict) -> tuple[int, int, int] | None:
    if bg.get("type") == "image":
        return None
    c = hex_to_rgb(bg["color"])
    if bg.get("type") == "gradient" and bg.get("gradient_to"):
        d = hex_to_rgb(bg["gradient_to"])
        return tuple((x + y) // 2 for x, y in zip(c, d))  # type: ignore[return-value]
    return c


# ---------------------------------------------------------------- main
def map_template(doc: dict, layout: dict, opt: Options) -> Outcome:
    if opt.mode not in MODES:
        raise MappingError("invalid_mode", f"Unknown application mode {opt.mode!r}")
    W, H = doc["canvas"]["width"], doc["canvas"]["height"]
    if (layout["width"], layout["height"]) != (W, H):
        raise MappingError("format_unsupported", f"Template layout is {layout['width']}x{layout['height']}, the banner is {W}x{H}: no stretching is done")
    ldef = layout["definition"]
    cand = copy.deepcopy(doc)
    out = Outcome(candidate=cand)
    els: list[dict] = cand["elements"]
    by_id = {e["id"]: e for e in els}
    styled = opt.mode in ("layout_style", "full")
    palette = list(opt.brand.get("colors") or []) if opt.brand_constraints.get("respect_brand_colors", True) else []
    brand_family = None
    if opt.brand.get("fonts") and opt.brand_constraints.get("respect_brand_fonts", True):
        brand_family, note = fonts.resolve_family(opt.brand["fonts"][0])
        if note:
            warn(out, "brand_font_substituted", note)

    # ---- 1. previous template decoration is replaced by the new one (never touched in layout_only)
    if styled:
        for e in [e for e in els if e["id"].startswith(TEMPLATE_PREFIX)]:
            els.remove(e)
            by_id.pop(e["id"])
            out.removed.append(e["id"])
            out.items.append({"slot": e.get("slot") or "decorative_shape", "layer_id": e["id"], "layer_name": e.get("name") or e["id"], "action": "removed", "changes": [], "note": "Decoration from the previous template"})

    # ---- 2. match layers to slots
    roles, _ = assign_slots(els)
    pool: dict[str, list[dict]] = {}
    for e in els:
        r = roles.get(e["id"])
        if not r or e["id"].startswith(TEMPLATE_PREFIX):
            continue
        if e["type"] not in ALLOWED_KINDS[r]:
            warn(out, "slot_type_mismatch", f"“{e.get('name') or e['id']}” is a {e['type']} layer and cannot fill the {r} slot", e["id"])
            continue
        pool.setdefault(r, []).append(e)
    for r, cands in pool.items():
        pick = opt.slot_overrides.get(r)
        if pick:
            hit = next((c for c in cands if c["id"] == pick), None)
            if not hit:
                raise MappingError("invalid_override", f"Layer {pick} cannot fill {r}")
            cands.sort(key=lambda c: c["id"] != pick)
        explicit = [c for c in cands if c.get("slot") == r]
        if len(explicit) > 1 and not pick:
            out.ambiguities.append({"slot": r, "candidates": [c["id"] for c in explicit], "chosen": explicit[0]["id"]})
            warn(out, "ambiguous_slot", f"{len(explicit)} layers are tagged {r}; using “{explicit[0].get('name') or explicit[0]['id']}” (choose another to override)", explicit[0]["id"], "info")

    used: set[str] = set()
    matched: list[tuple[dict, dict]] = []                       # (layer, slot) in slot z order
    slots = sorted(ldef["slots"], key=lambda s: s["z"])
    for s in slots:
        cands = [c for c in pool.get(s["role"], []) if c["id"] not in used and c["type"] == s["kind"]]
        if cands:
            used.add(cands[0]["id"])
            matched.append((cands[0], s))
    mapped_slots = {s["id"] for _, s in matched}

    # ---- 3. apply per mode
    bg = cand["background"]
    if opt.mode == "full" and ldef.get("background"):
        if bg.get("locked"):
            warn(out, "background_locked", "The background is locked, so the template background was not applied", "background")
        else:
            nb = copy.deepcopy(ldef["background"])
            if nb.get("type") != "image":
                nb["color"] = brand_color(nb["color"], palette) if palette else nb["color"]
                if nb.get("gradient_to") and palette:
                    nb["gradient_to"] = brand_color(nb["gradient_to"], palette)
                if nb["color"] != ldef["background"]["color"]:
                    warn(out, "brand_color_substituted", f"Template background colour {ldef['background']['color']} replaced by brand colour {nb['color']}", "background", "info")
            cand["background"] = {**nb, "locked": False}
            out.replaced.append("background")
            out.items.append({"slot": "background", "layer_id": "background", "layer_name": "Background", "action": "replaced", "changes": ["background"], "note": "Template background"})
    bd = _backdrop(cand["background"])

    order_keys: dict[str, int] = {}
    for layer, s in matched:
        changes: list[str] = []
        t = s["transform"]
        geom = {"x": round(t["x"] * W, 2), "y": round(t["y"] * H, 2), "w": max(1.0, round(t["w"] * W, 2)), "h": max(1.0, round(t["h"] * H, 2)), "rotation": t.get("rotation", 0.0)}
        if layer.get("locked"):
            warn(out, "layer_locked", f"“{layer.get('name') or layer['id']}” is locked and was left where it is", layer["id"])
            out.preserved.append(layer["id"])
            out.items.append({"slot": s["role"], "layer_id": layer["id"], "layer_name": layer.get("name") or layer["id"], "action": "preserved", "changes": [], "note": "Locked layer"})
            continue
        for k, v in geom.items():
            if abs(float(layer.get(k, 0)) - v) > 0.01:
                layer[k] = v
                changes.append(k) if k not in changes else None
        geometry_changed = bool(changes)
        c = s["constraints"]
        if c.get("min_w_px") and layer["w"] < c["min_w_px"] or c.get("min_h_px") and layer["h"] < c["min_h_px"]:
            warn(out, "slot_too_small", f"{s['role']} box ({layer['w']:.0f}x{layer['h']:.0f}px) is below the slot minimum", layer["id"])
        style_changes: list[str] = []
        replaced = False
        if styled:
            style_changes += _style(layer, s, opt, palette, brand_family, bd, out)
        if opt.mode == "full":
            replaced = _replace_content(layer, s, opt, out)
        action = "replaced" if replaced else "restyled" if style_changes else "moved" if geometry_changed else "preserved"
        if action == "preserved":
            out.preserved.append(layer["id"])
        elif action == "replaced":
            out.replaced.append(layer["id"])
        out.items.append({"slot": s["role"], "layer_id": layer["id"], "layer_name": layer.get("name") or layer["id"], "action": action,
                          "changes": sorted(set(changes + style_changes)), "note": ""})
        order_keys[layer["id"]] = s["z"]
        _check_fit(layer, out)

    # ---- 4. unmatched layers are preserved, never deleted
    matched_ids = {l["id"] for l, _ in matched}
    for e in els:
        if e["id"] in matched_ids or e["id"].startswith(TEMPLATE_PREFIX):
            continue
        out.unmapped.append(e["id"])
        out.preserved.append(e["id"])
        out.items.append({"slot": roles.get(e["id"]), "layer_id": e["id"], "layer_name": e.get("name") or e["id"], "action": "preserved", "changes": [],
                          "note": "No matching template slot: left untouched"})

    # ---- 5. missing slots / safe additions
    for s in slots:
        if s["id"] in mapped_slots:
            continue
        if s["role"] in ("decorative_shape", "promotion_badge") and s["kind"] == "shape" and styled:
            lay = _new_shape(s, W, H, opt, palette, bd, set(by_id) | set(out.created), out)
            els.append(lay)
            by_id[lay["id"]] = lay
            out.created.append(lay["id"])
            order_keys[lay["id"]] = s["z"]
            out.items.append({"slot": s["role"], "layer_id": lay["id"], "layer_name": lay["name"], "action": "added", "changes": [], "note": "Template decoration (no campaign content)"})
        elif s["required"]:
            out.missing_required.append(s["role"])
            warn(out, "missing_required_slot", f"This banner has no layer for the required “{s['role']}” slot; nothing was invented", None)
        elif s["role"] in CAMPAIGN_ROLES | IDENTITY_ROLES:
            warn(out, "empty_optional_slot", f"Optional slot “{s['role']}” has no matching layer and stays empty", None, "info")

    # ---- 6. layer order: mapped layers take the slots' z order within the positions they already occupy; new decoration is inserted by z
    _reorder(els, order_keys, set(out.created))
    cand["elements"] = els
    if opt.safe_zone:
        _safe_zone_check(cand, opt.safe_zone, out)
    _logo_rules(cand, opt, out)
    _overlaps(cand, out)
    try:
        cand = CreativeSpec.model_validate(cand).model_dump(mode="json")
    except ValidationError as e:
        x = e.errors()[0]
        raise MappingError("invalid_candidate", f"Candidate is not a valid design: {'.'.join(map(str, x['loc']))}: {x['msg']}") from e
    out.candidate = cand
    # roles that need explicit confirmation in full mode but were not given
    for r in sorted(out.needs_confirmation):
        warn(out, "replacement_not_confirmed", f"The template supplies its own content for “{r}”; it was NOT applied because it was not confirmed", None)
    return out


# ---------------------------------------------------------------- pieces
def _style(layer: dict, slot: dict, opt: Options, palette: list[str], brand_family: str | None, bd, out: Outcome) -> list[str]:
    changed: list[str] = []
    allowed = STYLE_KEYS[layer["type"]]
    ds = {k: v for k, v in slot["default_style"].items() if k in allowed}
    c = slot["constraints"]
    if c.get("image_fit") and layer["type"] == "image" and layer.get("fit") != "fill":
        ds.setdefault("fit", c["image_fit"])
    if c.get("min_font_size") and layer["type"] in ("text", "button"):
        ds.setdefault("min_font_size", c["min_font_size"])
    if c.get("max_lines") and layer["type"] == "text":
        ds.setdefault("max_lines", c["max_lines"])
    if layer["type"] in ("text", "button") and brand_family:
        if ds.get("font_family", layer["font_family"]) != brand_family:
            if ds.get("font_family") and ds["font_family"] != brand_family:
                warn(out, "brand_font_enforced", f"Template font {ds['font_family']} replaced by the brand font {brand_family}", layer["id"], "info")
            ds["font_family"] = brand_family
    for k in list(ds):
        if k in COLOR_KEYS and isinstance(ds[k], str):
            wanted = ds[k]
            ds[k] = brand_color(wanted, palette)
            if ds[k] != wanted:
                warn(out, "brand_color_substituted", f"Template colour {wanted} replaced by brand colour {ds[k]}", layer["id"], "info")
    if layer["type"] == "text" and "color" in ds and bd is not None and contrast(hex_to_rgb(ds["color"]), bd) < 3.0:
        safe, _scrim = pick_text_color(bd)
        warn(out, "contrast_adjusted", f"Text colour {ds['color']} had too little contrast on the template background; using {safe}", layer["id"], "info")
        ds["color"] = safe
    if layer["type"] == "button" and "fill" in ds and "text_color" not in ds:
        ds["text_color"] = max(("#ffffff", "#111111"), key=lambda c_: contrast(hex_to_rgb(c_), hex_to_rgb(ds["fill"])))
    for k, v in ds.items():
        if layer.get(k) != v:
            layer[k] = v
            changed.append(k)
    return changed


def _replace_content(layer: dict, slot: dict, opt: Options, out: Outcome) -> bool:
    """Full mode only: template-owned wording/assets replace the document's ONLY with explicit confirmation (or on a freshly seeded document)."""
    role = slot["role"]
    cp, ap = slot["content_policy"], slot["asset_policy"]
    wants = (cp["mode"] == "static" and layer["type"] in ("text", "button")) or (ap["mode"] == "static" and layer["type"] == "image")
    if not wants:
        return False
    if not opt.new_document and role not in opt.confirm_replace:
        if role not in out.needs_confirmation:
            out.needs_confirmation.append(role)
        return False
    if layer["type"] == "text":
        layer["text"] = cp["text"]
    elif layer["type"] == "button":
        layer["label"] = cp["text"][:30]
    else:
        layer["asset_id"] = str(ap["asset_id"])
        layer["crop"] = None
    return True


def _new_shape(slot: dict, W: int, H: int, opt: Options, palette: list[str], bd, taken: set[str], out: Outcome) -> dict:
    t = slot["transform"]
    base = f"{TEMPLATE_PREFIX}{slot['id']}"[:40]
    lid, n = base, 1
    while lid in taken:
        n += 1
        lid = f"{base[:36]}_{n}"
    ds = {k: v for k, v in slot["default_style"].items() if k in STYLE_KEYS["shape"]}
    for k in list(ds):
        if k in COLOR_KEYS and isinstance(ds[k], str):
            nv = brand_color(ds[k], palette)
            if nv != ds[k]:
                warn(out, "brand_color_substituted", f"Template colour {ds[k]} replaced by brand colour {nv}", lid, "info")
            ds[k] = nv
    return {"id": lid, "type": "shape", "role": "badge" if slot["role"] == "promotion_badge" else "decor", "slot": slot["role"], "name": f"Template: {slot['id']}",
            "x": round(t["x"] * W, 2), "y": round(t["y"] * H, 2), "w": max(1.0, round(t["w"] * W, 2)), "h": max(1.0, round(t["h"] * H, 2)),
            "rotation": t.get("rotation", 0.0), "opacity": ds.pop("opacity", 1), "locked": False, "visible": True,
            "shape": ds.pop("shape", "rect"), "fill": ds.pop("fill", "#cccccc"), "radius": ds.pop("radius", 0), "stroke": ds.pop("stroke", None), "stroke_width": ds.pop("stroke_width", 0)}


def _reorder(els: list[dict], keys: dict[str, int], created: set[str]) -> None:
    """Mapped layers are permuted among the positions they already occupy (so unmapped layers keep their place); new decoration is inserted by z."""
    mapped_pos = [i for i, e in enumerate(els) if e["id"] in keys and e["id"] not in created]
    ordered = sorted((els[i] for i in mapped_pos), key=lambda e: keys[e["id"]])   # stable: equal z keeps the old order
    for i, e in zip(mapped_pos, ordered):
        els[i] = e
    for e in [e for e in els if e["id"] in created]:
        els.remove(e)
        pos = next((i for i, o in enumerate(els) if o["id"] in keys and o["id"] not in created and keys[o["id"]] > keys[e["id"]]), len(els))
        els.insert(pos, e)


def _check_fit(layer: dict, out: Outcome) -> None:
    if layer["type"] == "text":
        lay = fit_text(layer["text"], layer["font_family"], layer["font_weight"], layer["font_size"], layer["min_font_size"] if layer.get("auto_fit", True) else layer["font_size"],
                       layer["max_lines"], layer["line_height"], layer["w"], layer["h"], layer.get("letter_spacing", 0), layer.get("uppercase", False))
        if lay.overflow:
            warn(out, "text_overflow", f"Text in “{layer.get('name') or layer['id']}” does not fit its new box", layer["id"])
        elif lay.font_size < 8:
            warn(out, "min_font_size", f"Text in “{layer.get('name') or layer['id']}” would render at {lay.font_size:.0f}px", layer["id"])
    elif layer["type"] == "button":
        if layer["font_size"] < 8 or layer["h"] < 12:
            warn(out, "min_font_size", f"CTA “{layer.get('name') or layer['id']}” is very small ({layer['w']:.0f}x{layer['h']:.0f}px)", layer["id"])
    elif layer["type"] == "image" and layer.get("fit") == "fill":
        warn(out, "image_distortion", f"“{layer.get('name') or layer['id']}” uses fit “fill” and may be stretched in its new box", layer["id"])


def _inter(a: dict, b: dict) -> float:
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return max(0.0, w) * max(0.0, h)


def _overlaps(cand: dict, out: Outcome) -> None:
    keep = [e for e in cand["elements"] if e.get("visible", True) and e["type"] in ("text", "button") or (e.get("slot") == "brand_logo" and e.get("visible", True))]
    for i, a in enumerate(keep):
        for b in keep[i + 1:]:
            ov = _inter(a, b)
            if ov and ov / min(_area(a), _area(b)) > 0.15:
                warn(out, "overlap", f"“{a.get('name') or a['id']}” overlaps “{b.get('name') or b['id']}”", a["id"])


def _safe_zone_check(cand: dict, zone: tuple[float, float], out: Outcome) -> None:
    H = cand["canvas"]["height"]
    top, bottom = zone[0] / 100 * H, H - zone[1] / 100 * H
    for e in cand["elements"]:
        if e.get("visible", True) and (e["type"] in ("text", "button") or e.get("slot") == "brand_logo"):
            if (top and e["y"] < top - 0.5) or (zone[1] and e["y"] + e["h"] > bottom + 0.5):
                warn(out, "safe_zone", f"“{e.get('name') or e['id']}” sits outside the platform safe area", e["id"])


def _logo_rules(cand: dict, opt: Options, out: Outcome) -> None:
    W, H = cand["canvas"]["width"], cand["canvas"]["height"]
    min_w = max(float(opt.brand.get("min_logo_width") or 0), float(opt.brand_constraints.get("min_logo_width_px") or 0))
    clear = float(opt.brand_constraints.get("logo_clear_space_pct") or 0)
    for e in cand["elements"]:
        if e.get("slot") != "brand_logo" or not e.get("visible", True):
            continue
        if min_w and e["w"] < min_w:
            warn(out, "logo_too_small", f"Logo is {e['w']:.0f}px wide; the brand minimum is {min_w:.0f}px", e["id"])
        if clear:
            m = clear * min(W, H)
            if e["x"] < m or e["y"] < m or e["x"] + e["w"] > W - m or e["y"] + e["h"] > H - m:
                warn(out, "logo_clear_space", "Logo is closer to the edge than the template's clear-space rule", e["id"])
