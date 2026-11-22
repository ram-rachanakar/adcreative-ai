"""Spec -> SVG -> PNG. Deterministic: bundled fonts only, no network, no external references, all text XML-escaped."""
import base64
import io
import math
import uuid
from dataclasses import dataclass, field
from typing import Callable
from xml.sax.saxutils import escape, quoteattr

import resvg_py
from PIL import Image

from app.rendering import fonts
from app.rendering.spec import ButtonEl, CreativeSpec, ImageEl, ShapeEl, TextEl
from app.rendering.text import TextLayout, fit_text


@dataclass
class ResolvedAsset:
    data: bytes
    width: int
    height: int


Resolver = Callable[[uuid.UUID], ResolvedAsset | None]


@dataclass
class ElementReport:
    id: str
    type: str
    bbox: tuple[float, float, float, float]       # actual drawn extent
    out_of_bounds: bool = False
    text_overflow: bool = False
    font_size: float | None = None
    shrunk: bool = False
    lines: int | None = None


@dataclass
class RenderReport:
    elements: list[ElementReport] = field(default_factory=list)
    missing_assets: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.missing_assets and not any(e.out_of_bounds or e.text_overflow for e in self.elements)


@dataclass
class RenderResult:
    png: bytes
    svg: str
    report: RenderReport
    width: int
    height: int


def _n(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _data_uri(asset: ResolvedAsset, target_w: float, target_h: float) -> tuple[str, int, int]:
    """Embed a display-resolution copy (<=2x the drawn size). Stored originals are never modified."""
    im = Image.open(io.BytesIO(asset.data))
    im.load()
    scale = min(1.0, max(target_w * 2 / im.width, target_h * 2 / im.height))
    if scale < 1:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.Resampling.LANCZOS)
    has_alpha = im.mode in ("RGBA", "LA", "P") and (im.mode != "P" or "transparency" in im.info)
    buf = io.BytesIO()
    if has_alpha:
        im.convert("RGBA").save(buf, "PNG")
        mime = "image/png"
    else:
        im.convert("RGB").save(buf, "JPEG", quality=93, subsampling=0)
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}", im.width, im.height


def _crop_asset(a: ResolvedAsset, c) -> ResolvedAsset:
    im = Image.open(io.BytesIO(a.data))
    im.load()
    box = (round(c.x * im.width), round(c.y * im.height), max(round(c.x * im.width) + 1, round((c.x + c.w) * im.width)),
           max(round(c.y * im.height) + 1, round((c.y + c.h) * im.height)))
    out = im.crop(box)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return ResolvedAsset(buf.getvalue(), out.width, out.height)


def _gradient_vec(angle: float) -> tuple[float, float, float, float]:
    t = math.radians(angle)
    s, c = math.sin(t), math.cos(t)
    return 0.5 - 0.5 * s, 0.5 + 0.5 * c, 0.5 + 0.5 * s, 0.5 - 0.5 * c


def build_svg(spec: CreativeSpec, resolve: Resolver, transparent: bool = False) -> tuple[str, RenderReport]:
    W, H = spec.canvas.width, spec.canvas.height
    rep = RenderReport()
    defs: list[str] = []
    body: list[str] = []
    bg = spec.background

    # --- background (transparent export omits the background layer and its scrims entirely)
    if transparent:
        pass
    elif bg.type == "solid":
        body.append(f'<rect width="{W}" height="{H}" fill="{bg.color}"/>')
    elif bg.type == "gradient":
        x1, y1, x2, y2 = _gradient_vec(bg.angle)
        defs.append(f'<linearGradient id="bg" x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}">'
                    f'<stop offset="0" stop-color="{bg.color}"/><stop offset="1" stop-color="{bg.gradient_to}"/></linearGradient>')
        body.append(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')
    else:
        a = resolve(bg.asset_id)
        body.append(f'<rect width="{W}" height="{H}" fill="{bg.color}"/>')
        if a is None:
            rep.missing_assets.append(str(bg.asset_id))
        else:
            uri, _, _ = _data_uri(a, W, H)
            par = {"cover": "xMidYMid slice", "contain": "xMidYMid meet", "fill": "none"}[bg.fit]
            body.append(f'<image x="0" y="0" width="{W}" height="{H}" preserveAspectRatio="{par}" xlink:href="{uri}"/>')
    for si, sc in enumerate([] if transparent else bg.scrims):
        sw, sh = sc.w if sc.w is not None else W, sc.h if sc.h is not None else H
        if sc.fade == "none":
            body.append(f'<rect x="{_n(sc.x)}" y="{_n(sc.y)}" width="{_n(sw)}" height="{_n(sh)}" fill="{sc.color}" fill-opacity="{_n(sc.opacity)}"/>')
        else:
            a, b = (0, 1) if sc.fade == "right" else (1, 0)
            u = sc.solid_until if sc.fade == "right" else 1 - sc.solid_until
            stops = (f'<stop offset="0" stop-color="{sc.color}" stop-opacity="{_n(sc.opacity)}"/><stop offset="{_n(u)}" stop-color="{sc.color}" stop-opacity="{_n(sc.opacity)}"/>'
                     f'<stop offset="1" stop-color="{sc.color}" stop-opacity="0"/>') if sc.fade == "right" else (
                     f'<stop offset="0" stop-color="{sc.color}" stop-opacity="0"/><stop offset="{_n(u)}" stop-color="{sc.color}" stop-opacity="{_n(sc.opacity)}"/>'
                     f'<stop offset="1" stop-color="{sc.color}" stop-opacity="{_n(sc.opacity)}"/>')
            defs.append(f'<linearGradient id="scrim{si}" x1="0" y1="0" x2="1" y2="0">{stops}</linearGradient>')
            body.append(f'<rect x="{_n(sc.x)}" y="{_n(sc.y)}" width="{_n(sw)}" height="{_n(sh)}" fill="url(#scrim{si})"/>')

    def oob(x, y, w, h) -> bool:
        return x < -1 or y < -1 or x + w > W + 1 or y + h > H + 1

    for i, el in enumerate(spec.elements):
        if not el.visible:
            continue
        start = len(body)
        op = f' opacity="{_n(el.opacity)}"' if el.opacity < 1 else ""
        if isinstance(el, ShapeEl):
            st = f' stroke="{el.stroke}" stroke-width="{_n(el.stroke_width)}"' if el.stroke and el.stroke_width else ""
            if el.shape == "ellipse":
                body.append(f'<ellipse cx="{_n(el.x + el.w / 2)}" cy="{_n(el.y + el.h / 2)}" rx="{_n(el.w / 2)}" ry="{_n(el.h / 2)}" fill="{el.fill}"{st}{op}/>')
            else:
                body.append(f'<rect x="{_n(el.x)}" y="{_n(el.y)}" width="{_n(el.w)}" height="{_n(el.h)}" rx="{_n(el.radius)}" fill="{el.fill}"{st}{op}/>')
            rep.elements.append(ElementReport(el.id, "shape", (el.x, el.y, el.w, el.h), oob(el.x, el.y, el.w, el.h)))

        elif isinstance(el, ImageEl):
            a = resolve(el.asset_id)
            if a is None:
                rep.missing_assets.append(str(el.asset_id))
                continue
            if el.crop:
                a = _crop_asset(a, el.crop)
            if el.fit == "fill":
                dx, dy, dw, dh, par, clip = el.x, el.y, el.w, el.h, "none", ""
            elif el.fit == "contain":  # aspect ratio is preserved exactly; the image is never stretched
                s = min(el.w / a.width, el.h / a.height)
                dw, dh = a.width * s, a.height * s
                dx, dy = el.x + (el.w - dw) / 2, el.y + (el.h - dh) / 2
                par, clip = "none", ""
            else:
                dx, dy, dw, dh, par = el.x, el.y, el.w, el.h, "xMidYMid slice"
                clip = ""
            uri, _, _ = _data_uri(a, dw, dh)
            if el.corner_radius or el.fit == "cover":
                cid = f"clip{i}"
                defs.append(f'<clipPath id="{cid}"><rect x="{_n(dx)}" y="{_n(dy)}" width="{_n(dw)}" height="{_n(dh)}" rx="{_n(el.corner_radius)}"/></clipPath>')
                clip = f' clip-path="url(#{cid})"'
            if el.shadow == "contact":
                gid = f"sh{i}"
                defs.append(f'<radialGradient id="{gid}"><stop offset="0" stop-color="#000" stop-opacity="0.38"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>')
                body.append(f'<ellipse cx="{_n(dx + dw / 2)}" cy="{_n(dy + dh - dh * 0.01)}" rx="{_n(dw * 0.46)}" ry="{_n(max(2, dh * 0.045))}" fill="url(#{gid})"/>')
            if el.shadow == "drop":
                fid = f"ds{i}"
                defs.append(f'<filter id="{fid}" x="-20%" y="-20%" width="140%" height="150%"><feGaussianBlur in="SourceAlpha" stdDeviation="{_n(max(1, dh * 0.03))}"/>'
                            f'<feOffset dx="0" dy="{_n(max(1, dh * 0.02))}" result="o"/><feFlood flood-color="#000" flood-opacity="0.35"/><feComposite in2="o" operator="in"/>'
                            f'<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>')
                body.append(f'<g filter="url(#{fid})"><image x="{_n(dx)}" y="{_n(dy)}" width="{_n(dw)}" height="{_n(dh)}" preserveAspectRatio="{par}"{clip}{op} xlink:href="{uri}"/></g>')
            else:
                body.append(f'<image x="{_n(dx)}" y="{_n(dy)}" width="{_n(dw)}" height="{_n(dh)}" preserveAspectRatio="{par}"{clip}{op} xlink:href="{uri}"/>')
            rep.elements.append(ElementReport(el.id, "image", (dx, dy, dw, dh), oob(dx, dy, dw, dh)))

        elif isinstance(el, TextEl):
            try:
                lay = fit_text(el.text, el.font_family, el.font_weight, el.font_size, el.min_font_size if el.auto_fit else el.font_size, el.max_lines, el.line_height,
                               el.w, el.h, el.letter_spacing, el.uppercase)
            except fonts.MissingFont as e:
                rep.errors.append(str(e))
                continue
            body.append(_text_svg(el.x, el.y, el.w, el.h, lay, el.font_family, el.font_weight, el.color, el.align, el.valign, el.letter_spacing, op))
            rep.elements.append(ElementReport(el.id, "text", (el.x, el.y, el.w, el.h), oob(el.x, el.y, el.w, el.h), lay.overflow,
                                              lay.font_size, lay.shrunk, len(lay.lines)))

        elif isinstance(el, ButtonEl):
            try:
                pad = el.w * 0.08 if el.padding_x is None else min(el.padding_x, el.w / 3)
                lay = fit_text(el.label, el.font_family, el.font_weight, el.font_size, el.min_font_size, 1, 1.0, el.w - 2 * pad, el.h,
                               0, el.uppercase)
            except fonts.MissingFont as e:
                rep.errors.append(str(e))
                continue
            body.append(f'<rect x="{_n(el.x)}" y="{_n(el.y)}" width="{_n(el.w)}" height="{_n(el.h)}" rx="{_n(min(el.radius, el.h / 2))}" fill="{el.fill}"{op}/>')
            body.append(_text_svg(el.x, el.y, el.w, el.h, lay, el.font_family, el.font_weight, el.text_color, "center", "middle", 0, op))
            rep.elements.append(ElementReport(el.id, "button", (el.x, el.y, el.w, el.h), oob(el.x, el.y, el.w, el.h), lay.overflow,
                                              lay.font_size, lay.shrunk, len(lay.lines)))

        if el.rotation and len(body) > start:
            body[start:] = [f'<g transform="rotate({_n(el.rotation)} {_n(el.x + el.w / 2)} {_n(el.y + el.h / 2)})">' + "".join(body[start:]) + "</g>"]

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
           f'<defs>{"".join(defs)}</defs>{"".join(body)}</svg>')
    return svg, rep


def _text_svg(x, y, w, h, lay: TextLayout, family, weight, color, align, valign, spacing, op) -> str:
    block_h = lay.line_px * len(lay.lines)
    top = y if valign == "top" else (y + (h - block_h) / 2 if valign == "middle" else y + h - block_h)
    ax = x if align == "left" else (x + w / 2 if align == "center" else x + w)
    anchor = {"left": "start", "center": "middle", "right": "end"}[align]
    half_lead = (lay.line_px - (lay.ascent + lay.descent)) / 2
    spans = []
    for n, line in enumerate(lay.lines):
        base = round(top + n * lay.line_px + half_lead + lay.ascent)   # baselines sit on whole pixels, as browsers draw them (keeps the editor canvas and the export aligned)
        spans.append(f'<tspan x="{_n(ax)}" y="{_n(base)}">{escape(line)}</tspan>')
    ls = f' letter-spacing="{_n(spacing)}"' if spacing else ""
    return (f'<text font-family={quoteattr(family)} font-weight="{weight}" font-size="{_n(lay.font_size)}" fill="{color}" '
            f'text-anchor="{anchor}"{ls}{op}>{"".join(spans)}</text>')


def render_spec(spec: CreativeSpec, resolve: Resolver, transparent: bool = False) -> RenderResult:
    svg, rep = build_svg(spec, resolve, transparent)
    W, H = spec.canvas.width, spec.canvas.height
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=W, height=H, skip_system_fonts=True, font_files=fonts.all_font_files(),
                                       text_rendering="optimize_legibility"))
    with Image.open(io.BytesIO(png)) as im:
        if im.size != (W, H):
            rep.errors.append(f"Rendered size {im.size} differs from canvas {(W, H)}")
    return RenderResult(png, svg, rep, W, H)
