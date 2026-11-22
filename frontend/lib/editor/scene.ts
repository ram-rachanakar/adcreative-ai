/** Pure placement maths shared by the Konva canvas. Each function mirrors the corresponding branch of backend rendering/svg.py. */
import { fitText, type Measure, type MetricsFn } from "./textLayout";
import type { Background, ButtonLayer, ImageLayer, TextLayer } from "./types";

export interface TextPlan { lines: { text: string; x: number; baseline: number }[]; fontSize: number; anchor: "left" | "center" | "right"; overflow: boolean; shrunk: boolean; family: string; weight: number; color: string; spacing: number }

/** Mirrors svg.py::_text_svg. `box` is the layer's own rectangle in document space. */
export function textPlan(l: TextLayer | ButtonLayer, measure: Measure, metrics: MetricsFn): TextPlan {
  const isBtn = l.type === "button";
  const pad = isBtn ? ((l as ButtonLayer).padding_x ?? l.w * 0.08) : 0;
  const boxW = isBtn ? l.w - 2 * Math.min(pad, l.w / 3) : l.w;
  const t = l as TextLayer; const b = l as ButtonLayer;
  const lay = fitText(measure, { text: isBtn ? b.label : t.text, family: l.font_family, weight: l.font_weight, size: l.font_size, minSize: l.min_font_size, maxLines: isBtn ? 1 : t.max_lines, lineHeight: isBtn ? 1 : t.line_height,
    boxW, boxH: l.h, spacing: isBtn ? 0 : t.letter_spacing, uppercase: l.uppercase, autoFit: isBtn ? true : t.auto_fit });
  const m = metrics(l.font_family, l.font_weight, lay.fontSize);
  const align = isBtn ? "center" : t.align, valign = isBtn ? "middle" : t.valign;
  const blockH = lay.linePx * lay.lines.length;
  const top = valign === "top" ? l.y : valign === "middle" ? l.y + (l.h - blockH) / 2 : l.y + l.h - blockH;
  const ax = align === "left" ? l.x : align === "center" ? l.x + l.w / 2 : l.x + l.w;
  const halfLead = (lay.linePx - (m.ascent + m.descent)) / 2;
  return { lines: lay.lines.map((text, n) => ({ text, x: ax, baseline: top + n * lay.linePx + halfLead + m.ascent })), fontSize: lay.fontSize, anchor: align, overflow: lay.overflow, shrunk: lay.shrunk,
    family: l.font_family, weight: l.font_weight, color: isBtn ? b.text_color : t.color, spacing: isBtn ? 0 : t.letter_spacing };
}

export interface ImagePlan { x: number; y: number; w: number; h: number; src: { x: number; y: number; w: number; h: number } }
/** Mirrors svg.py image branch: crop first (fractions of source), then contain / cover(slice) / fill into the layer box. */
export function imagePlan(l: ImageLayer, nat: { width: number; height: number }): ImagePlan {
  const c = l.crop ?? { x: 0, y: 0, w: 1, h: 1 };
  const sx = c.x * nat.width, sy = c.y * nat.height, sw = Math.max(1, c.w * nat.width), sh = Math.max(1, c.h * nat.height);
  if (l.fit === "fill") return { x: l.x, y: l.y, w: l.w, h: l.h, src: { x: sx, y: sy, w: sw, h: sh } };
  if (l.fit === "contain") { const s = Math.min(l.w / sw, l.h / sh); const dw = sw * s, dh = sh * s; return { x: l.x + (l.w - dw) / 2, y: l.y + (l.h - dh) / 2, w: dw, h: dh, src: { x: sx, y: sy, w: sw, h: sh } }; }
  const s = Math.max(l.w / sw, l.h / sh); const vw = l.w / s, vh = l.h / s;     // slice: show the centred window that fills the box
  return { x: l.x, y: l.y, w: l.w, h: l.h, src: { x: sx + (sw - vw) / 2, y: sy + (sh - vh) / 2, w: vw, h: vh } };
}

/** Mirrors svg.py::_gradient_vec; returns points in pixels for a canvas of W×H. */
export function gradientPoints(angle: number, W: number, H: number) {
  const t = (angle * Math.PI) / 180, s = Math.sin(t), c = Math.cos(t);
  return { start: { x: (0.5 - 0.5 * s) * W, y: (0.5 + 0.5 * c) * H }, end: { x: (0.5 + 0.5 * s) * W, y: (0.5 - 0.5 * c) * H } };
}

export function backgroundImagePlan(bg: Background, nat: { width: number; height: number }, W: number, H: number): ImagePlan {
  return imagePlan({ x: 0, y: 0, w: W, h: H, fit: bg.fit, crop: null } as ImageLayer, nat);
}

export const rotatedCorners = (l: { x: number; y: number; w: number; h: number; rotation: number }) => {
  const cx = l.x + l.w / 2, cy = l.y + l.h / 2, r = (l.rotation * Math.PI) / 180, cs = Math.cos(r), sn = Math.sin(r);
  return ([[-1, -1], [1, -1], [1, 1], [-1, 1]] as const).map(([a, b]) => ({ x: cx + (a * l.w / 2) * cs - (b * l.h / 2) * sn, y: cy + (a * l.w / 2) * sn + (b * l.h / 2) * cs }));
};

/** Convert Konva node state (centre position, scale, rotation) back into layer geometry. Scale is baked into w/h. */
export function nodeToBox(n: { cx: number; cy: number; scaleX: number; scaleY: number; rotation: number }, w: number, h: number) {
  const nw = Math.max(1, Math.abs(w * n.scaleX)), nh = Math.max(1, Math.abs(h * n.scaleY));
  return { x: n.cx - nw / 2, y: n.cy - nh / 2, w: nw, h: nh, rotation: n.rotation };
}
