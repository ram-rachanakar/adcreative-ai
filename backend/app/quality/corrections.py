"""Deterministic, bounded corrections. Each action edits the CreativeSpec only (never the AI background, never the product pixels),
so every change is cheap, explainable and reversible. Anything these cannot fix is reported as a recommendation instead."""
from dataclasses import dataclass, field

import numpy as np

from app.quality import checks
from app.quality.policy import QualityPolicy
from app.rendering import color
from app.rendering.spec import ButtonEl, CreativeSpec, ImageEl, Scrim, TextEl

ESCALATE = {"recolor_text": "strengthen_scrim"}   # tried on the next round if the first remedy did not clear the finding


@dataclass
class CorrectionContext:
    policy: QualityPolicy
    backdrop: np.ndarray | None = None
    alternatives: list[str] = field(default_factory=list)
    palette: list[str] = field(default_factory=list)
    safe_top_pct: float = 0
    safe_bottom_pct: float = 0


def _el(spec: CreativeSpec, eid: str | None):
    return next((e for e in spec.elements if e.id == eid), None)


def _clamp(el, W, H, m=0.0, top=0.0, bottom=None):
    """Keep an element inside the canvas and (when given) inside the platform's reserved top/bottom bands."""
    bottom = H if bottom is None else bottom
    el.w, el.h = min(el.w, W - 2 * m), min(el.h, bottom - top)
    el.x, el.y = min(max(el.x, m), W - el.w - m), min(max(el.y, top), bottom - el.h)


def _zone(spec, ctx):
    H = spec.canvas.height
    return H * ctx.safe_top_pct / 100, H - H * ctx.safe_bottom_pct / 100


def recolor_text(spec, ctx, eid):
    el = _el(spec, eid)
    if not isinstance(el, TextEl) or ctx.backdrop is None:
        return None
    box = checks.text_extent(el)
    cur = checks.region_contrast(ctx.backdrop, el.color, box, ctx.policy.contrast_percentile)
    best = max((color.LIGHT, color.DARK), key=lambda c: (checks.region_contrast(ctx.backdrop, c, box, ctx.policy.contrast_percentile) or (0, 0))[0])
    new = checks.region_contrast(ctx.backdrop, best, box, ctx.policy.contrast_percentile)
    if not cur or not new or best.lower() == el.color.lower() or new[0] <= cur[0] + 0.3:
        return None
    old, el.color = el.color, best
    return f"Text colour of {eid} {old} -> {best} (contrast {cur[0]:.2f} -> {new[0]:.2f}:1)"


def strengthen_scrim(spec, ctx, eid):
    el = _el(spec, eid)
    if not isinstance(el, TextEl):
        return None
    light = color.luminance(color.hex_to_rgb(el.color)) > 0.4
    tone = "#000000" if light else "#ffffff"
    for s in spec.background.scrims:
        if s.color.lower() == tone and s.opacity < 0.9:
            old, s.opacity = s.opacity, min(0.9, s.opacity + 0.15)
            return f"Raised scrim opacity {old:.2f} -> {s.opacity:.2f} behind {eid}"
    if len(spec.background.scrims) >= 4:
        return None
    spec.background.scrims.append(Scrim(color=tone, opacity=0.45))
    return f"Added a 45% {'dark' if light else 'light'} scrim for {eid}"


def use_shorter_headline(spec, ctx, eid):
    el = _el(spec, eid)
    if not isinstance(el, TextEl):
        return None
    cands = sorted((a for a in ctx.alternatives if a.strip() and len(a) < len(el.text) and a != el.text), key=len, reverse=True)
    if not cands:
        return None
    old, el.text = el.text, cands[0]
    return f"Headline switched to approved alternative ({len(old)} -> {len(cands[0])} chars)"


def reduce_font(spec, ctx, eid):
    el = _el(spec, eid)
    W, H = spec.canvas.width, spec.canvas.height
    if isinstance(el, ButtonEl):
        nw = min(el.w * 1.25, W - 2 * max(4, min(W, H) * 0.02))
        if nw <= el.w + 1:
            return None
        cx = el.x + el.w / 2
        el.w, el.x = nw, cx - nw / 2
        _clamp(el, W, H)
        return f"Widened button {eid} to fit its label"
    if isinstance(el, TextEl):
        limit = H - H * ctx.safe_bottom_pct / 100 - 2
        nh = min(el.h * 1.2, limit - el.y)
        if nh <= el.h + 1:
            return None
        el.h = nh
        return f"Gave {eid} more vertical room"
    return None


def drop_body(spec, ctx, eid):
    body = next((e for e in spec.elements if isinstance(e, TextEl) and e.role == "body"), None)
    if body is None:
        return None
    spec.elements = [e for e in spec.elements if e.id != body.id]
    return "Removed supporting copy to resolve crowding (headline + CTA are kept)"


def move_into_safe_zone(spec, ctx, eid):
    el = _el(spec, eid)
    if el is None:
        return None
    W, H = spec.canvas.width, spec.canvas.height
    top, bottom = H * ctx.safe_top_pct / 100, H - H * ctx.safe_bottom_pct / 100
    if el.h > bottom - top:
        return None
    ny = min(max(el.y, top), bottom - el.h)
    if abs(ny - el.y) < 0.5:
        return None
    old, el.y = el.y, ny
    return f"Moved {eid} {abs(ny - old):.0f}px into the platform safe zone"


def clamp_to_canvas(spec, ctx, eid):
    el = _el(spec, eid)
    if el is None:
        return None
    before = (el.x, el.y, el.w, el.h)
    _clamp(el, spec.canvas.width, spec.canvas.height)
    return f"Pulled {eid} back inside the canvas" if (el.x, el.y, el.w, el.h) != before else None


def enlarge_cta(spec, ctx, eid):
    b = _el(spec, eid)
    if not isinstance(b, ButtonEl):
        return None
    W, H = spec.canvas.width, spec.canvas.height
    need = max(ctx.policy.min_cta_height_px, min(W, H) * ctx.policy.min_cta_height_pct / 100)
    k = need / b.h
    if k <= 1.01:
        return None
    cx, cy = b.x + b.w / 2, b.y + b.h / 2
    b.w, b.h, b.font_size = b.w * k, b.h * k, b.font_size * k
    b.x, b.y = cx - b.w / 2, cy - b.h / 2
    top, bottom = _zone(spec, ctx)
    _clamp(b, W, H, 0, top, bottom)
    return f"Enlarged button {eid} x{k:.2f}"


def enlarge_logo(spec, ctx, eid):
    lg = _el(spec, eid)
    if not isinstance(lg, ImageEl):
        return None
    k = ctx.policy.min_logo_px / max(lg.w, lg.h)
    if k <= 1.01:
        return None
    lg.w, lg.h = lg.w * k, lg.h * k
    top, bottom = _zone(spec, ctx)
    _clamp(lg, spec.canvas.width, spec.canvas.height, 0, top, bottom)
    return f"Enlarged logo x{k:.2f}"


def fix_cta_colors(spec, ctx, eid):
    b = _el(spec, eid)
    if not isinstance(b, ButtonEl):
        return None
    avg = checks.ring_average(ctx.backdrop, (b.x, b.y, b.w, b.h)) if ctx.backdrop is not None else None
    pool = [c for c in [*ctx.palette, b.fill] if c]
    fill = max(pool, key=lambda c: color.contrast(color.hex_to_rgb(c), avg)) if avg is not None else b.fill
    label = max((color.LIGHT, color.DARK), key=lambda c: color.contrast(color.hex_to_rgb(c), color.hex_to_rgb(fill)))
    if (fill.lower(), label.lower()) == (b.fill.lower(), b.text_color.lower()):
        return None
    old = (b.fill, b.text_color)
    b.fill, b.text_color = fill, label
    return f"Button colours {old[0]}/{old[1]} -> {fill}/{label}"


def restore_product_fit(spec, ctx, eid):
    el = _el(spec, eid)
    if not isinstance(el, ImageEl) or el.fit == "contain":
        return None
    el.fit = "contain"
    return "Restored the product to 'contain' so it is not cropped"


ACTIONS = {f.__name__: f for f in (recolor_text, strengthen_scrim, use_shorter_headline, reduce_font, drop_body, move_into_safe_zone, clamp_to_canvas,
                                   enlarge_cta, enlarge_logo, fix_cta_colors, restore_product_fit)}


def apply_fixes(spec: CreativeSpec, findings: list[dict], ctx: CorrectionContext, tried: set[tuple[str, str | None]]) -> tuple[CreativeSpec, list[dict]]:
    """One correction round. `tried` remembers (action, element) pairs so a remedy that did not work escalates instead of repeating."""
    new = spec.model_copy(deep=True)
    done: list[dict] = []
    order = sorted([f for f in findings if f.get("fix")], key=lambda f: f["severity"] != "fail")
    for f in order:
        action = f["fix"]
        while action in ESCALATE and (action, f.get("element_id")) in tried:   # only remedies that have a stronger sibling escalate; the rest are idempotent
            action = ESCALATE[action]
        if not action:
            continue
        tried.add((action, f.get("element_id")))
        try:
            msg = ACTIONS[action](new, ctx, f.get("element_id"))
        except Exception:  # a failing remedy must never break assessment
            msg = None
        if msg:
            done.append({"action": action, "issue": f["code"], "element_id": f.get("element_id"), "detail": msg})
    return new, done
