/** Port of backend rendering/text.py: greedy word wrap + shrink-to-fit, measured with the SAME bundled TTFs (loaded via FontFace).
 *  `measure` is injectable so tests and parity checks can run without a browser. */
export type Measure = (text: string, family: string, weight: number, size: number) => number;
export interface TextLayoutResult { lines: string[]; fontSize: number; linePx: number; overflow: boolean; shrunk: boolean; width: number; height: number }

let ctx: CanvasRenderingContext2D | null = null;
export const canvasMeasure: Measure = (text, family, weight, size) => {
  if (!ctx) ctx = document.createElement("canvas").getContext("2d");
  if (!ctx) return text.length * size * 0.55;
  ctx.font = `${weight} ${size}px "${family}"`;
  return ctx.measureText(text).width;
};

const width = (m: Measure, t: string, fam: string, w: number, s: number, sp: number) => m(t, fam, w, s) + sp * Math.max(0, t.length - 1);

export function wrap(m: Measure, text: string, fam: string, weight: number, size: number, spacing: number, maxW: number): { lines: string[]; tooWide: boolean } {
  const lines: string[] = []; let cur = ""; let tooWide = false;
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const trial = cur ? `${cur} ${word}` : word;
    if (width(m, trial, fam, weight, size, spacing) <= maxW) { cur = trial; continue; }
    if (cur) lines.push(cur);
    cur = word;
    if (width(m, word, fam, weight, size, spacing) > maxW) tooWide = true;
  }
  if (cur) lines.push(cur);
  return { lines, tooWide };
}

export interface FitArgs { text: string; family: string; weight: number; size: number; minSize: number; maxLines: number; lineHeight: number; boxW: number; boxH: number; spacing?: number; uppercase?: boolean; autoFit?: boolean }
export function fitText(m: Measure, a: FitArgs): TextLayoutResult {
  let text = a.text.split(/\s+/).filter(Boolean).join(" "); if (a.uppercase) text = text.toUpperCase();
  const spacing = a.spacing ?? 0; const min = a.autoFit === false ? a.size : a.minSize;
  let s = a.size; const step = Math.max(0.5, a.size * 0.03);
  for (;;) {
    const { lines, tooWide } = wrap(m, text, a.family, a.weight, s, spacing, a.boxW);
    const linePx = s * a.lineHeight; const height = linePx * lines.length;
    const fits = lines.length <= a.maxLines && height <= a.boxH + 0.01 && !tooWide;
    if (fits || s - step < min - 1e-9) {
      if (!fits && s > min) { s = min; continue; }
      return { lines, fontSize: s, linePx, overflow: !fits, shrunk: s < a.size - 1e-9, width: Math.max(0, ...lines.map(l => width(m, l, a.family, a.weight, s, spacing))), height };
    }
    s -= step;
  }
}

const loaded = new Set<string>();
/** Load the bundled TTFs the renderer uses. Resolves when all are ready so first paint already has final metrics. */
export async function loadFonts(fonts: { family: string; weight: number; file: string }[], base: string): Promise<void> {
  if (typeof FontFace === "undefined") return;
  await Promise.all(fonts.map(async f => {
    const key = `${f.family}:${f.weight}`; if (loaded.has(key)) return;
    const face = new FontFace(f.family, `url(${base}/editor/fonts/${f.file})`, { weight: String(f.weight) });
    await face.load(); document.fonts.add(face); loaded.add(key);
  }));
}

export interface Metrics { ascent: number; descent: number }
export type MetricsFn = (family: string, weight: number, size: number) => Metrics;
/** Ascent/descent like Pillow's getmetrics() (integers, hhea metrics of the same TTF). */
export const canvasMetrics: MetricsFn = (family, weight, size) => {
  if (!ctx) ctx = document.createElement("canvas").getContext("2d");
  if (!ctx) return { ascent: size * 0.9, descent: size * 0.2 };
  ctx.font = `${weight} ${size}px "${family}"`;
  const m = ctx.measureText("Hg") as TextMetrics;
  return { ascent: Math.round(m.fontBoundingBoxAscent ?? size * 0.9), descent: Math.round(m.fontBoundingBoxDescent ?? size * 0.2) };
};
