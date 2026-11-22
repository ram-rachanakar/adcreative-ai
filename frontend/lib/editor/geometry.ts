export interface Box { x: number; y: number; w: number; h: number }
export interface Guide { axis: "x" | "y"; pos: number }
export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
export const round = (v: number, d = 2) => Math.round(v * 10 ** d) / 10 ** d;

/** Fit-to-screen zoom so the whole canvas is visible with padding. */
export function fitZoom(canvas: { width: number; height: number }, view: { width: number; height: number }, pad = 48): number {
  if (view.width <= 0 || view.height <= 0) return 1;
  return clamp(Math.min((view.width - pad * 2) / canvas.width, (view.height - pad * 2) / canvas.height), 0.05, 8);
}

export interface SnapResult { x: number; y: number; guides: Guide[] }
/** Snap a moving box to canvas edges/centre, other layers' edges/centres, and an optional grid. Distances are in document pixels. */
export function snapBox(box: Box, canvas: { width: number; height: number }, others: Box[], opts: { threshold: number; grid?: number }): SnapResult {
  const xs = [0, canvas.width / 2, canvas.width], ys = [0, canvas.height / 2, canvas.height];
  for (const o of others) { xs.push(o.x, o.x + o.w / 2, o.x + o.w); ys.push(o.y, o.y + o.h / 2, o.y + o.h); }
  const guides: Guide[] = []; let { x, y } = box;
  const pick = (cands: number[], pts: number[]) => { let best: { d: number; shift: number; at: number } | null = null; for (const p of pts) for (const c of cands) { const d = Math.abs(c - p); if (d <= opts.threshold && (!best || d < best.d)) best = { d, shift: c - p, at: c }; } return best; };
  const bx = pick(xs, [box.x, box.x + box.w / 2, box.x + box.w]); const by = pick(ys, [box.y, box.y + box.h / 2, box.y + box.h]);
  if (bx) { x += bx.shift; guides.push({ axis: "x", pos: bx.at }); } else if (opts.grid) x = Math.round(x / opts.grid) * opts.grid;
  if (by) { y += by.shift; guides.push({ axis: "y", pos: by.at }); } else if (opts.grid) y = Math.round(y / opts.grid) * opts.grid;
  return { x, y, guides };
}

/** Safe area in px from the format registry's top/bottom percentages (the only safe-zone data the registry holds). */
export function safeArea(canvas: { width: number; height: number }, zone: { top_pct: number; bottom_pct: number } | null): Box | null {
  if (!zone || (!zone.top_pct && !zone.bottom_pct)) return null;
  const top = (canvas.height * zone.top_pct) / 100, bottom = (canvas.height * zone.bottom_pct) / 100;
  return { x: 0, y: top, w: canvas.width, h: canvas.height - top - bottom };
}

export const nudge = (shift: boolean, step = 1) => (shift ? step * 10 : step);
