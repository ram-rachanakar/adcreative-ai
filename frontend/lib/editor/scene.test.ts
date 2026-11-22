import { describe, expect, it } from "vitest";
import { backgroundImagePlan, gradientPoints, imagePlan, nodeToBox, rotatedCorners, textPlan } from "./scene";
import { button, image, makeDoc, text } from "./fixtures";
import type { ButtonLayer, ImageLayer, TextLayer } from "./types";

const measure = (t: string, _f: string, _w: number, s: number) => t.length * s * 0.5;
const metrics = () => ({ ascent: 10, descent: 3 });

describe("scene placement (mirrors backend svg.py)", () => {
  it("text baseline = top + n*linePx + halfLead + ascent, aligned by anchor and valign", () => {
    const p = textPlan(text({ text: "aa bb cc dd", w: 30, h: 100, font_size: 20, min_font_size: 20, line_height: 1.5, valign: "top", align: "center", max_lines: 5 }) as TextLayer, measure, metrics);
    expect(p.lines.map(l => l.text)).toEqual(["aa", "bb", "cc", "dd"]);
    const linePx = 30, halfLead = (30 - 13) / 2;
    expect(p.lines[0].baseline).toBeCloseTo(10 + halfLead + 10); expect(p.lines[1].baseline - p.lines[0].baseline).toBeCloseTo(linePx);
    expect(p.lines[0].x).toBe(10 + 15); expect(p.anchor).toBe("center");
    const mid = textPlan(text({ text: "aa", w: 60, h: 100, font_size: 20, valign: "middle" }) as TextLayer, measure, metrics);
    expect(mid.lines[0].baseline).toBeGreaterThan(p.lines[0].baseline + 20);
  });
  it("auto_fit=false keeps font size and flags overflow", () => {
    const p = textPlan(text({ text: "word word word word", w: 40, h: 20, font_size: 30, auto_fit: false, max_lines: 1 }) as TextLayer, measure, metrics);
    expect(p.fontSize).toBe(30); expect(p.overflow).toBe(true);
  });
  it("button text is centred in both axes with 8% default horizontal padding", () => {
    const p = textPlan(button({ label: "Go", w: 100, h: 40, font_size: 16 }) as ButtonLayer, measure, metrics);
    expect(p.anchor).toBe("center"); expect(p.lines[0].x).toBe(10 + 50);
  });
  it("image contain/cover/fill and crop", () => {
    const nat = { width: 200, height: 100 };
    const c = imagePlan(image({ x: 0, y: 0, w: 100, h: 100, fit: "contain" }) as ImageLayer, nat); expect([c.x, c.y, c.w, c.h]).toEqual([0, 25, 100, 50]);
    const v = imagePlan(image({ x: 0, y: 0, w: 100, h: 100, fit: "cover" }) as ImageLayer, nat); expect([v.w, v.h]).toEqual([100, 100]); expect(v.src).toEqual({ x: 50, y: 0, w: 100, h: 100 });
    const f = imagePlan(image({ x: 5, y: 5, w: 80, h: 30, fit: "fill" }) as ImageLayer, nat); expect([f.x, f.y, f.w, f.h]).toEqual([5, 5, 80, 30]);
    const k = imagePlan(image({ x: 0, y: 0, w: 100, h: 100, fit: "contain", crop: { x: 0, y: 0, w: 0.5, h: 1 } }) as ImageLayer, nat); expect(k.src).toEqual({ x: 0, y: 0, w: 100, h: 100 }); expect([k.w, k.h]).toEqual([100, 100]);
  });
  it("gradient vector and background image plan", () => {
    const g = gradientPoints(180, 100, 200); expect(g.start.x).toBeCloseTo(50); expect(g.start.y).toBeCloseTo(0); expect(g.end.y).toBeCloseTo(200);
    expect(backgroundImagePlan({ ...makeDoc().background, fit: "cover" }, { width: 100, height: 100 }, 300, 250).src.h).toBeCloseTo(83.33, 1);
  });
  it("rotation geometry and node->box conversion bake scale into size", () => {
    const [a, b] = rotatedCorners({ x: 0, y: 0, w: 100, h: 50, rotation: 90 }); expect(a.x).toBeCloseTo(75); expect(a.y).toBeCloseTo(-25); expect(b.y).toBeCloseTo(75);
    const r = nodeToBox({ cx: 100, cy: 100, scaleX: 2, scaleY: 0.5, rotation: 30 }, 40, 40); expect(r).toEqual({ x: 60, y: 90, w: 80, h: 20, rotation: 30 });
  });
});
