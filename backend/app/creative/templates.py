"""Adaptive layout engine. Picks a layout family from the canvas, builds a CreativeSpec, validates it (text fit, bounds, overlaps) and
walks a fallback chain when it does not fit: reflow/shrink (inside fit_text) -> drop supporting copy -> shortest approved alternate
headline -> minimal recompose. Every simplification is recorded in spec.meta.notes. The product is always `contain`-fitted (never distorted)."""
import uuid
from dataclasses import dataclass, field

from app.rendering import color
from app.rendering.spec import Background, ButtonEl, Canvas, CreativeSpec, ImageEl, Scrim, SpecMeta, TextEl
from app.rendering.text import fit_text

MIN_FONT_PX = 8.0
Box = tuple[float, float, float, float]  # x, y, w, h


class UnsupportedSize(Exception):
    pass


@dataclass
class ComposeInput:
    headline: str
    supporting: str
    cta: str
    palette: list[str]
    bg_asset_id: uuid.UUID
    bg_bytes: bytes
    product_asset_id: uuid.UUID
    product_size: tuple[int, int]
    alternatives: list[str] = field(default_factory=list)   # approved alternative headlines (already claim-checked)
    logo_asset_id: uuid.UUID | None = None
    logo_size: tuple[int, int] | None = None
    font_family: str = "Liberation Sans"
    concept_id: str | None = None
    notes: list[str] = field(default_factory=list)
    safe_top_pct: float = 0
    safe_bottom_pct: float = 0


@dataclass
class Layout:
    family: str
    product: Box
    headline: Box
    cta: Box
    text_area: Box
    align: str
    head_size: float
    head_lines: int
    head_valign: str = "top"
    body: Box | None = None
    body_size: float = 0
    logo: Box | None = None
    logo_anchor: str = "left"
    cta_font: float = 0
    notes: list[str] = field(default_factory=list)


def family_for(w: int, h: int) -> str:
    r, area = w / h, w * h
    if r > 2.4:
        return "very_wide"
    if r < 0.5:
        return "skyscraper"
    if area <= 100_000 and 0.6 <= r <= 2.4:
        return "small"
    if r < 0.85:
        return "portrait"
    if r <= 1.2:
        return "square"
    return "landscape"


def body_allowed(fam: str, w: int, h: int) -> bool:
    if fam in ("small", "skyscraper"):
        return False
    if fam == "very_wide":
        return h >= 120
    return True


# ------------------------------------------------------------------------------------------------ family layouts
def _landscape(w, h, m, logo, body) -> Layout:
    col_w = round(w * 0.50) - m
    logo_h = round(h * 0.10) if logo else 0
    top = m + (logo_h + m * 0.4 if logo_h else 0)
    avail = h - m - top - m
    btn_h = avail * 0.17
    head_h = avail * (0.40 if body else 0.62)
    return Layout("landscape", product=(round(w * 0.55), m, w - round(w * 0.55) - m, h - 2 * m),
                  headline=(m, top, col_w, head_h), body=(m, top + head_h + avail * 0.04, col_w, avail * 0.22) if body else None,
                  cta=(m, h - m - btn_h, min(col_w, w * 0.30), btn_h), text_area=(m, m, col_w, h - 2 * m), align="left",
                  head_size=h * 0.115, head_lines=3, body_size=h * 0.05, logo=(m, m, col_w * 0.5, logo_h) if logo_h else None, logo_anchor="left")


def _square(w, h, m, logo, body) -> Layout:
    full = w - 2 * m
    lg = h * 0.07 if logo else 0
    head_y = m + (lg + m * 0.3 if logo else 0)
    head_h = h * (0.20 if body else 0.24)
    prod_y = head_y + head_h + m * 0.3
    btn_h = h * 0.095
    btn_y = h - m - btn_h
    body_h = h * 0.10
    prod_h = (btn_y - m * 0.5 - body_h - prod_y) if body else (btn_y - m * 0.5 - prod_y)
    return Layout("square", product=(w * 0.2, prod_y, w * 0.6, max(prod_h, h * 0.2)), headline=(m, head_y, full, head_h),
                  body=(m, prod_y + max(prod_h, h * 0.2) + m * 0.2, full, body_h) if body else None,
                  cta=((w - min(full, w * 0.42)) / 2, btn_y, min(full, w * 0.42), btn_h), text_area=(0, 0, w, h), align="center",
                  head_size=h * 0.075, head_lines=3, head_valign="middle", body_size=h * 0.038,
                  logo=(w - m - w * 0.16, m * 0.5, w * 0.16, lg) if logo else None, logo_anchor="right")


def _portrait(w, h, m, logo, body, y0, y1) -> Layout:
    use = y1 - y0
    full = w - 2 * m
    lg = use * 0.05 if logo else 0
    head_y = y0 + (lg * 1.3 if logo else 0)
    head_h = use * 0.20
    prod_y = head_y + head_h + m * 0.3
    btn_h = use * 0.075
    btn_y = y1 - btn_h
    body_h = use * 0.10
    prod_h = (btn_y - m * 0.5 - body_h - m * 0.4 - prod_y) if body else (btn_y - m * 0.5 - prod_y)
    return Layout("portrait", product=(w * 0.12, prod_y, w * 0.76, prod_h), headline=(m, head_y, full, head_h),
                  body=(m, prod_y + prod_h + m * 0.4, full, body_h) if body else None,
                  cta=((w - min(full, w * 0.6)) / 2, btn_y, min(full, w * 0.6), btn_h), text_area=(0, 0, w, h_of(y1, y0, m)), align="center",
                  head_size=use * 0.058, head_lines=3, body_size=use * 0.028,
                  logo=((w - w * 0.3) / 2, y0, w * 0.3, lg) if logo else None, logo_anchor="center")


def h_of(y1, y0, m):  # text area for stacked portrait layouts = full canvas height reached by content
    return y1 + m


def _small(w, h, m, logo) -> Layout:
    if w / h >= 1.15:   # 300x250 / 336x280
        col_w = w * 0.52 - m
        lg = h * 0.11 if logo else 0
        top = m + (lg + m * 0.4 if lg else 0)
        btn_h = h * 0.17
        return Layout("small", product=(w * 0.54, m, w * 0.46 - m, h - 2 * m), headline=(m, top, col_w, h - m - top - btn_h - m * 1.2),
                      cta=(m, h - m - btn_h, min(col_w, w * 0.42), btn_h), text_area=(m, m, col_w, h - 2 * m), align="left",
                      head_size=h * 0.13, head_lines=4, head_valign="middle", logo=(m, m, col_w * 0.6, lg) if lg else None, logo_anchor="left",
                      notes=["Small display: supporting copy omitted"])
    btn_h = h * 0.16   # 250x250 / 200x200
    head_h = h * 0.27
    return Layout("small", product=(w * 0.2, m + head_h + m * 0.3, w * 0.6, h - 2 * m - head_h - btn_h - m * 0.9), headline=(m, m, w - 2 * m, head_h),
                  cta=((w - w * 0.55) / 2, h - m - btn_h, w * 0.55, btn_h), text_area=(0, 0, w, h), align="center", head_size=h * 0.095,
                  head_lines=3, head_valign="middle", notes=["Small display: supporting copy and logo omitted"])


def _very_wide(w, h, m, logo, body) -> Layout:
    notes = []
    inner = h - 2 * m
    x = m
    lg_box = None
    if logo and h >= 90:
        lw = min(w * 0.14, inner * 2.2)
        lg_box = (x, m, lw, inner)
        x += lw + m
    elif logo:
        notes.append("Logo omitted: banner too small to show it legibly")
    prod_w = inner * 1.0
    prod = (x, m, prod_w, inner)
    x += prod_w + m * 1.5
    btn_h = min(inner * 0.62, 60) if h < 120 else min(h * 0.22, 64)
    btn_w = max(70, min(w * 0.2, 230)) if w >= 400 else w * 0.30
    cta = (w - m - btn_w, (h - btn_h) / 2, btn_w, btn_h)
    head_w = cta[0] - m * 1.5 - x
    tall = h >= 120
    head_h = inner * (0.55 if body else 0.9) if tall else inner
    return Layout("very_wide", product=prod, headline=(x, m, head_w, head_h), body=(x, m + head_h + inner * 0.05, head_w, inner * 0.35) if body and tall else None,
                  cta=cta, text_area=(x - m, 0, head_w + 2 * m, h), align="left", head_size=h * (0.30 if not tall else 0.17),
                  head_lines=2 if not tall else 3, head_valign="middle", body_size=h * 0.075, logo=lg_box, logo_anchor="left",
                  cta_font=min(btn_h * 0.42, btn_w * 0.16), notes=notes + (["Wide banner: reduced copy"] if h < 120 else []))


def _skyscraper(w, h, m, logo) -> Layout:
    lg = w * 0.28 if logo else 0
    top = m + (lg + m if lg else 0)
    head_h = h * 0.24
    btn_h = max(30, h * 0.06)
    prod_y = top + head_h + m
    return Layout("skyscraper", product=(m, prod_y, w - 2 * m, h - m - btn_h - m * 1.5 - prod_y), headline=(m, top, w - 2 * m, head_h),
                  cta=(m, h - m - btn_h, w - 2 * m, btn_h), text_area=(0, 0, w, h), align="center", head_size=w * 0.17, head_lines=5,
                  head_valign="middle", logo=(m, m, w - 2 * m, lg) if lg else None, logo_anchor="center", notes=["Skyscraper: supporting copy omitted"])


def build_layout(w: int, h: int, logo: bool, body: bool, safe_top: float, safe_bottom: float) -> Layout:
    fam = family_for(w, h)
    m = max(6.0, round(min(w, h) * 0.07))
    if fam == "landscape":
        return _landscape(w, h, m, logo, body)
    if fam == "square":
        return _square(w, h, m, logo, body)
    if fam == "portrait":
        return _portrait(w, h, m, logo, body, max(m, h * safe_top / 100), h - max(m, h * safe_bottom / 100))
    if fam == "small":
        return _small(w, h, max(6.0, min(w, h) * 0.06), logo)
    if fam == "very_wide":
        return _very_wide(w, h, max(4.0, h * 0.08), logo, body)
    return _skyscraper(w, h, max(6.0, w * 0.07), logo)


# ------------------------------------------------------------------------------------------------ spec building
def _build_spec(inp: ComposeInput, w: int, h: int, lay: Layout, headline: str, with_body: bool, with_logo: bool, steps: list[str]) -> CreativeSpec:
    notes = list(inp.notes) + lay.notes + steps
    tx, ty, tw, th = lay.text_area
    avg = color.cover_region_average(inp.bg_bytes, w, h, lay.text_area)
    text_hex, scrim = color.pick_text_color(avg)
    bg = Background(type="image", asset_id=inp.bg_asset_id, color=color.rgb_to_hex(avg))
    if scrim:
        # Text at the left edge -> scrim that fades out to the right (no hard edge). Text anywhere else -> even full-canvas scrim.
        left = tx <= w * 0.1 and tw < w * 0.7 and lay.family in ("landscape", "small")
        if left:
            span = min(w, tx + tw + w * 0.32)
            bg.scrims.append(Scrim(color=scrim[0], opacity=scrim[1], x=0, y=0, w=span, h=h, fade="right", solid_until=min(0.9, 0.92 * (tx + tw) / span)))
        else:
            bg.scrims.append(Scrim(color=scrim[0], opacity=scrim[1]))
        notes.append(f"Added {int(scrim[1] * 100)}% scrim to reach 4.5:1 text contrast")
    eff = color.blend(avg, color.hex_to_rgb(scrim[0]), scrim[1]) if scrim else avg
    fill, label = color.button_colors(inp.palette, eff)

    els: list = []
    if with_logo and lay.logo and inp.logo_asset_id and inp.logo_size:
        lx, ly, lw, lh = lay.logo
        tw_ = min(lw, lh * inp.logo_size[0] / inp.logo_size[1])
        lx = lx if lay.logo_anchor == "left" else (lx + lw - tw_ if lay.logo_anchor == "right" else lx + (lw - tw_) / 2)
        els.append(ImageEl(id="logo", role="logo", name="Logo", x=lx, y=ly, w=tw_, h=lh, asset_id=inp.logo_asset_id, fit="contain"))
    px, py, pw, ph = lay.product
    els.append(ImageEl(id="product", role="product", name="Product", x=px, y=py, w=pw, h=ph, asset_id=inp.product_asset_id, fit="contain", shadow="contact"))
    hx, hy, hw, hh = lay.headline
    hs = max(lay.head_size, MIN_FONT_PX)
    els.append(TextEl(id="headline", role="headline", name="Headline", x=hx, y=hy, w=hw, h=hh, text=headline, font_family=inp.font_family, font_weight=700,
                      font_size=hs, min_font_size=max(MIN_FONT_PX, hs * 0.55), max_lines=lay.head_lines, line_height=1.1, color=text_hex, align=lay.align,
                      valign=lay.head_valign))
    if with_body and lay.body and inp.supporting:
        bx, by, bw, bh = lay.body
        bs = max(lay.body_size, MIN_FONT_PX)
        els.append(TextEl(id="body", role="body", name="Supporting copy", x=bx, y=by, w=bw, h=bh, text=inp.supporting, font_family=inp.font_family, font_weight=400,
                          font_size=bs, min_font_size=max(MIN_FONT_PX, bs * 0.7), max_lines=3, line_height=1.25, color=text_hex, align=lay.align))
    cx, cy, cw, ch = lay.cta
    cf = lay.cta_font or ch * 0.42
    els.append(ButtonEl(id="cta", role="cta", name="CTA", x=cx, y=cy, w=cw, h=ch, label=inp.cta, fill=fill, text_color=label, font_family=inp.font_family,
                        font_size=max(cf, MIN_FONT_PX), min_font_size=max(MIN_FONT_PX, cf * 0.7), radius=ch * 0.25))
    return CreativeSpec(canvas=Canvas(width=w, height=h), background=bg, elements=els,
                        meta=SpecMeta(concept_id=inp.concept_id, template=f"{lay.family}.v2", family=lay.family, notes=notes))


def spec_problems(spec: CreativeSpec) -> list[str]:
    """Deterministic layout validation without rasterising: text fit, minimum font size, bounds, overlaps."""
    W, H = spec.canvas.width, spec.canvas.height
    out: list[str] = []
    boxes = []
    for e in spec.elements:
        if e.x < -1 or e.y < -1 or e.x + e.w > W + 1 or e.y + e.h > H + 1:
            out.append(f"{e.id}: outside canvas")
        if e.role in ("logo", "product", "headline", "body", "cta"):
            boxes.append((e.id, e.x, e.y, e.w, e.h))
        if isinstance(e, TextEl):
            lay = fit_text(e.text, e.font_family, e.font_weight, e.font_size, e.min_font_size, e.max_lines, e.line_height, e.w, e.h, e.letter_spacing, e.uppercase)
            if lay.overflow:
                out.append(f"{e.id}: text does not fit")
            elif lay.font_size < MIN_FONT_PX:
                out.append(f"{e.id}: font below {MIN_FONT_PX:g}px")
        elif isinstance(e, ButtonEl):
            lay = fit_text(e.label, e.font_family, e.font_weight, e.font_size, e.min_font_size, 1, 1.0, e.w * 0.84, e.h, 0, e.uppercase)
            if lay.overflow or lay.font_size < MIN_FONT_PX:
                out.append(f"{e.id}: label does not fit")
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            ox, oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]), min(a[2] + a[4], b[2] + b[4]) - max(a[2], b[2])
            if ox > 1 and oy > 1:
                out.append(f"{a[0]} overlaps {b[0]}")
    return out


def _shortest(inp: ComposeInput) -> str:
    return min([inp.headline] + [a for a in inp.alternatives if a.strip()], key=len)


def compose(inp: ComposeInput, w: int, h: int) -> CreativeSpec:
    """Build the best-fitting spec for (w, h), simplifying copy/layout only when needed (and saying so)."""
    fam = family_for(w, h)
    has_logo = bool(inp.logo_asset_id and inp.logo_size)
    allow_body = body_allowed(fam, w, h) and bool(inp.supporting.strip())
    short = _shortest(inp)
    attempts: list[tuple[str, bool, bool, str, list[str]]] = [("", allow_body, has_logo, inp.headline, [])]
    if allow_body:
        attempts.append(("drop-body", False, has_logo, inp.headline, ["Simplified: supporting copy dropped to fit"]))
    if short != inp.headline:
        attempts.append(("short-headline", False, has_logo, short, ["Simplified: shortest approved headline used", *(["Supporting copy dropped"] if allow_body else [])]))
    attempts.append(("minimal", False, False, short, ["Recomposed minimally: logo and supporting copy dropped"] if has_logo else ["Recomposed minimally"]))
    best: tuple[int, CreativeSpec] | None = None
    for _, body, logo, head, steps in attempts:
        lay = build_layout(w, h, logo, body, inp.safe_top_pct, inp.safe_bottom_pct)
        spec = _build_spec(inp, w, h, lay, head, body, logo, steps)
        probs = spec_problems(spec)
        if not probs:
            return spec
        if best is None or len(probs) < best[0]:
            best = (len(probs), spec, probs)  # type: ignore[assignment]
    spec, probs = best[1], best[2]  # type: ignore[misc]
    spec.meta.notes.append("Layout could not be fully fitted: " + "; ".join(probs[:4]))
    return spec
