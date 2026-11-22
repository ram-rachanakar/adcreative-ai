"""Deterministic quality checks. Pure functions of (spec, rendered pixels, policy): no network, no randomness.

Every finding is {code, category, severity, message, element_id, value, threshold, fix}; `fix` names a deterministic
correction action from app.quality.corrections (or None when only a human / regeneration can resolve it)."""
import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from app.marketing import claims
from app.quality.policy import QualityPolicy
from app.rendering import color
from app.rendering.spec import ButtonEl, CreativeSpec, ImageEl, TextEl
from app.rendering.text import fit_text

CATEGORIES = ("legibility", "layout", "conversion", "brand", "technical")
PENALTY = {"fail": 30.0, "warn": 10.0, "info": 0.0}


@dataclass
class CheckInput:
    spec: CreativeSpec
    render_png: bytes
    backdrop_png: bytes | None            # the same render with every TextEl removed; used to measure text contrast honestly
    render_report: dict
    safe_top_pct: float = 0
    safe_bottom_pct: float = 0
    brand_colors: list[str] = field(default_factory=list)
    claim_source: str | None = None        # lower-cased source facts; when present, edited copy is re-checked by the claim guard
    expect_logo: bool = False


def _rgb(png: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png)) as im:
        return np.asarray(im.convert("RGB"), dtype=np.float64)


def _lum(a: np.ndarray) -> np.ndarray:
    v = a / 255.0
    lin = np.where(v <= 0.03928, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def region_contrast(backdrop: np.ndarray, fg_hex: str, box: tuple[float, float, float, float], pct: float) -> tuple[float, float] | None:
    """(contrast at the worst `pct` percent of pixels, fraction of pixels below 3:1) of fg colour vs the backdrop inside box."""
    H, W = backdrop.shape[:2]
    x, y, w, h = box
    x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(W, int(np.ceil(x + w))), min(H, int(np.ceil(y + h)))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    L = _lum(backdrop[y0:y1, x0:x1]).ravel()
    lf = color.luminance(color.hex_to_rgb(fg_hex))
    hi, lo = np.maximum(L, lf), np.minimum(L, lf)
    ratios = (hi + 0.05) / (lo + 0.05)
    return float(np.percentile(ratios, pct)), float((ratios < 3.0).mean())


def ring_average(backdrop: np.ndarray, box: tuple[float, float, float, float], pad: int = 6):
    """Mean colour of the 6px ring around a box (what a button sits on)."""
    H, W = backdrop.shape[:2]
    x, y, w, h = box
    x0, y0, x1, y1 = max(0, int(x) - pad), max(0, int(y) - pad), min(W, int(x + w) + pad), min(H, int(y + h) + pad)
    if x1 <= x0 or y1 <= y0:
        return None
    patch = backdrop[y0:y1, x0:x1]
    mask = np.ones(patch.shape[:2], bool)
    ix0, iy0, ix1, iy1 = max(0, int(x) - x0), max(0, int(y) - y0), min(patch.shape[1], int(x + w) - x0), min(patch.shape[0], int(y + h) - y0)
    mask[iy0:iy1, ix0:ix1] = False
    return patch[mask].mean(axis=0) if mask.any() else None


def text_extent(el: TextEl) -> tuple[float, float, float, float]:
    """Where the text actually lands inside its box (same fitting routine as the renderer)."""
    lay = fit_text(el.text, el.font_family, el.font_weight, el.font_size, el.min_font_size, el.max_lines, el.line_height, el.w, el.h, el.letter_spacing, el.uppercase)
    w, h = min(max(lay.width, 1.0), el.w), min(max(lay.height, 1.0), el.h)
    x = el.x if el.align == "left" else (el.x + el.w - w if el.align == "right" else el.x + (el.w - w) / 2)
    y = el.y if el.valign == "top" else (el.y + el.h - h if el.valign == "bottom" else el.y + (el.h - h) / 2)
    return x, y, w, h


def _inter(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w, h = min(ax + aw, bx + bw) - max(ax, bx), min(ay + ah, by + bh) - max(ay, by)
    return max(0.0, w) * max(0.0, h)


def decide(score: int, findings: list[dict], pol: QualityPolicy) -> str:
    if any(f["severity"] == "fail" for f in findings) or score < pol.fail_below:
        return "fail"
    return "pass" if score >= pol.pass_at and not any(f["severity"] == "warn" for f in findings) else "warn"


def run_checks(inp: CheckInput, pol: QualityPolicy) -> dict:
    spec, W, H = inp.spec, inp.spec.canvas.width, inp.spec.canvas.height
    findings: list[dict] = []
    evaluated: set[str] = set()

    def add(code, cat, sev, msg, el=None, value=None, threshold=None, fix=None):
        findings.append({"code": code, "category": cat, "severity": sev, "message": msg, "element_id": el,
                         "value": None if value is None else round(float(value), 2), "threshold": threshold, "fix": fix})

    def ran(*codes):
        evaluated.update(codes)

    short = min(W, H)
    texts = [e for e in spec.elements if isinstance(e, TextEl) and e.visible]
    buttons = [e for e in spec.elements if isinstance(e, ButtonEl) and e.visible]
    images = [e for e in spec.elements if isinstance(e, ImageEl) and e.visible]
    logos = [e for e in images if e.role == "logo"]
    products = [e for e in images if e.role == "product"]
    headline = next((t for t in texts if t.role == "headline"), None)
    body = next((t for t in texts if t.role == "body"), None)
    rep = {e["id"]: e for e in inp.render_report.get("elements", [])}

    # ---- technical
    ran("dimensions", "render_errors")
    with Image.open(io.BytesIO(inp.render_png)) as im:
        if im.size != (W, H) or im.format != "PNG":
            add("dimensions", "technical", "fail", f"Rendered {im.size} {im.format}, expected {(W, H)} PNG", value=im.size[0], threshold=W)
    for e in inp.render_report.get("errors", []) + [f"missing asset {m}" for m in inp.render_report.get("missing_assets", [])]:
        add("render_errors", "technical", "fail", f"Render problem: {e}")

    # ---- required content
    ran("missing_headline", "missing_cta")
    if not headline or not headline.text.strip():
        add("missing_headline", "conversion", "fail", "No headline text")
    if not buttons or not buttons[0].label.strip():
        add("missing_cta", "conversion", "fail", "No call-to-action button")

    # ---- text fit & size
    ran("text_overflow", "font_too_small", "font_small")
    for el in [*texts, *buttons]:
        r = rep.get(el.id, {})
        if r.get("text_overflow"):
            fix = "use_shorter_headline" if el is headline else ("drop_body" if el is body else "reduce_font")
            add("text_overflow", "legibility", "fail", f"Text does not fit its box ({el.id})", el.id, fix=fix)
        fs = r.get("font_size")
        if fs is not None:
            if fs < pol.min_font_px_fail:
                add("font_too_small", "legibility", "fail", f"{el.id} renders at {fs:.1f}px (minimum {pol.min_font_px_fail:g}px)", el.id, fs, pol.min_font_px_fail,
                    fix="use_shorter_headline" if el is headline else ("drop_body" if el is body else None))
            elif fs < pol.min_font_px_warn:
                add("font_small", "legibility", "warn", f"{el.id} renders at {fs:.1f}px (recommended {pol.min_font_px_warn:g}px+)", el.id, fs, pol.min_font_px_warn,
                    fix="use_shorter_headline" if el is headline else ("drop_body" if el is body else None))

    # ---- contrast (measured on pixels, not assumed)
    ran("contrast_blocking", "contrast_low", "cta_contrast", "cta_vs_background")
    backdrop = _rgb(inp.backdrop_png) if inp.backdrop_png else None
    for el in texts:
        if backdrop is None or el.opacity < 0.95 or not el.text.strip():
            continue
        m = region_contrast(backdrop, el.color, text_extent(el), pol.contrast_percentile)
        if m is None:
            continue
        ratio, bad_frac = m
        if ratio < pol.block_contrast:
            add("contrast_blocking", "legibility", "fail", f"{el.id}: {ratio:.2f}:1 on the worst {pol.contrast_percentile:g}% of its backdrop (min {pol.block_contrast:g}:1)", el.id, ratio, pol.block_contrast, fix="recolor_text")
        elif ratio < pol.min_contrast:
            add("contrast_low", "legibility", "warn", f"{el.id}: {ratio:.2f}:1 (AA recommends {pol.min_contrast:g}:1)", el.id, ratio, pol.min_contrast, fix="recolor_text")
    for b in buttons:
        c = color.contrast(color.hex_to_rgb(b.text_color), color.hex_to_rgb(b.fill))
        if c < pol.min_contrast:
            add("cta_contrast", "legibility", "fail" if c < pol.block_contrast else "warn", f"Button label contrast {c:.1f}:1", b.id, c, pol.min_contrast, fix="fix_cta_colors")
        if backdrop is not None:
            avg = ring_average(backdrop, (b.x, b.y, b.w, b.h))
            if avg is not None:
                c2 = color.contrast(color.hex_to_rgb(b.fill), avg)
                if c2 < pol.cta_vs_background_contrast:
                    add("cta_vs_background", "conversion", "warn", f"Button blends into its surroundings ({c2:.1f}:1)", b.id, c2, pol.cta_vs_background_contrast, fix="fix_cta_colors")

    # ---- layout: bounds, safe zones, margins, overlaps, density
    ran("off_canvas", "outside_safe_zone", "edge_margin", "overlap", "text_density")
    boxes: dict[str, tuple] = {}
    for el in [*texts, *buttons, *logos]:
        boxes[el.id] = text_extent(el) if isinstance(el, TextEl) else (el.x, el.y, el.w, el.h)
    margin = short * pol.edge_margin_pct / 100
    top, bottom = H * inp.safe_top_pct / 100, H * inp.safe_bottom_pct / 100
    for el in [*texts, *buttons, *logos]:
        x, y, w, h = boxes[el.id]
        critical = not isinstance(el, ImageEl)
        if x < -0.5 or y < -0.5 or x + w > W + 0.5 or y + h > H + 0.5:
            add("off_canvas", "layout", "fail" if critical else "warn", f"{el.id} extends beyond the canvas", el.id, fix="clamp_to_canvas")
        elif inp.safe_top_pct or inp.safe_bottom_pct:
            if y < top - 0.5 or y + h > H - bottom + 0.5:
                add("outside_safe_zone", "layout", "fail" if critical else "warn", f"{el.id} sits in the platform's reserved UI zone", el.id, fix="move_into_safe_zone")
        if min(x, y, W - x - w, H - y - h) < margin - 0.5 and not (x < -0.5 or y < -0.5):
            add("edge_margin", "layout", "warn", f"{el.id} is closer than {pol.edge_margin_pct:g}% to the edge", el.id, min(x, y, W - x - w, H - y - h), margin, fix=None)
    tb = [(e, boxes[e.id]) for e in [*texts, *buttons]]
    for i in range(len(tb)):
        for j in range(i + 1, len(tb)):
            (a, ba), (b, bb) = tb[i], tb[j]
            frac = _inter(ba, bb) / max(1.0, min(ba[2] * ba[3], bb[2] * bb[3]))
            if frac * 100 > pol.max_overlap_pct:
                drop = body if body in (a, b) else None
                add("overlap", "layout", "fail" if frac > 0.25 else "warn", f"{a.id} overlaps {b.id} ({frac * 100:.0f}%)", a.id, frac * 100, pol.max_overlap_pct,
                    fix="drop_body" if drop is not None else None)
    for lg in logos:
        for other in [*texts, *buttons]:
            if _inter(boxes[lg.id], boxes[other.id]) > 0:
                add("overlap", "layout", "warn", f"Logo overlaps {other.id}", lg.id, fix=None)
    text_area = sum(boxes[e.id][2] * boxes[e.id][3] for e in [*texts, *buttons])
    if text_area / (W * H) * 100 > pol.max_text_area_pct:
        add("text_density", "layout", "warn", f"Text and button cover {text_area / (W * H) * 100:.0f}% of the canvas", value=text_area / (W * H) * 100, threshold=pol.max_text_area_pct,
            fix="drop_body" if body else None)

    # ---- conversion: CTA size & hierarchy
    ran("cta_small", "weak_hierarchy")
    need = max(pol.min_cta_height_px, short * pol.min_cta_height_pct / 100)
    for b in buttons:
        if b.h < need - 0.5:
            add("cta_small", "conversion", "warn", f"Button is {b.h:.0f}px tall (recommended {need:.0f}px+)", b.id, b.h, need, fix="enlarge_cta")
    if headline and body:
        hs, bs = rep.get(headline.id, {}).get("font_size"), rep.get(body.id, {}).get("font_size")
        if hs and bs and hs < bs * 1.15:
            add("weak_hierarchy", "conversion", "warn", "Headline is not clearly larger than the supporting copy", headline.id, hs / bs, 1.15, fix="drop_body")

    # ---- brand & content
    ran("logo_small", "logo_missing", "product_small", "product_distorted", "off_brand_cta", "unsupported_claim")
    if inp.expect_logo and not logos:
        dropped = any("logo" in n.lower() and ("omitted" in n.lower() or "dropped" in n.lower()) for n in spec.meta.notes)
        add("logo_missing", "brand", "info" if dropped else "warn",
            "The logo was left off by the layout because the format is too small for it" if dropped else "A logo was supplied but is not on this banner", fix=None)
    for lg in logos:
        if lg.w < pol.min_logo_px and lg.h < pol.min_logo_px:
            add("logo_small", "brand", "warn", f"Logo is {lg.w:.0f}x{lg.h:.0f}px (minimum {pol.min_logo_px:g}px on its long side)", lg.id, max(lg.w, lg.h), pol.min_logo_px, fix="enlarge_logo")
    for p in products:
        if p.fit != "contain":
            add("product_distorted", "brand", "fail", "Product image is cropped (fit must be 'contain' to preserve the original product)", p.id, fix="restore_product_fit")
        if p.w * p.h / (W * H) * 100 < pol.min_product_area_pct:
            add("product_small", "brand", "warn", f"Product occupies {p.w * p.h / (W * H) * 100:.1f}% of the canvas", p.id, p.w * p.h / (W * H) * 100, pol.min_product_area_pct, fix=None)
    if buttons and inp.brand_colors:
        d = min(float(np.linalg.norm(np.array(color.hex_to_rgb(buttons[0].fill)) - np.array(color.hex_to_rgb(c)))) for c in inp.brand_colors)
        if d > pol.brand_color_max_distance:
            add("off_brand_cta", "brand", "info", f"CTA colour is not from the brand/concept palette (distance {d:.0f})", buttons[0].id, d, pol.brand_color_max_distance)
    if inp.claim_source is not None:
        copy = {e.id: e.text for e in texts} | {e.id: e.label for e in buttons}
        for hit in claims.check_copy(copy, inp.claim_source):
            add("unsupported_claim", "brand", "warn", f"{hit['field']}: '{hit['text']}' ({hit['rule']})", hit["field"], fix=None)

    # ---- aggregate
    failing = {f["code"] for f in findings}
    cats: dict[str, float] = {}
    for c in CATEGORIES:
        cats[c] = max(0.0, 100.0 - sum(PENALTY[f["severity"]] for f in findings if f["category"] == c))
    w = pol.weights.model_dump()
    avg = sum(cats[c] * w[c] for c in CATEGORIES) / (sum(w.values()) or 1)
    score = 0.6 * avg + 0.4 * min(cats.values())   # the weakest category pulls the score down: one bad dimension cannot hide behind four good ones
    blocking = [f for f in findings if f["severity"] == "fail"]
    score = int(round(score))
    if blocking:
        score = min(score, pol.fail_below - 1)   # a blocking defect can never hide behind good averages
    return {"score": score, "categories": {k: int(round(v)) for k, v in cats.items()}, "findings": findings,
            "passed": sorted(evaluated - failing), "blocking": [f["code"] for f in blocking]}
