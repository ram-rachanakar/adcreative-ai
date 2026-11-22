import { describe, expect, it } from "vitest";
import { parseFormats, previewRequestSchema, saveAsTemplateSchema } from "./schemas";
import { defaultFormat, groupMapping, sizeLabel, sortWarnings, summarizeSizes, supports } from "./templateMappingClient";
import { listQuery } from "./templateApi";
import { EMPTY_FILTERS, type MappingItem, type SupportedFormat } from "./types";

const fmt = (w: number, h: number): SupportedFormat => ({ format_id: `${w}x${h}`, width: w, height: h, aspect_ratio: w / h, aspect_class: "square", inherits: null, placements: [] });
const item = (action: MappingItem["action"], id: string): MappingItem => ({ slot: "headline_primary", layer_id: id, layer_name: id, action, changes: [], note: "" });

describe("template list query", () => {
  it("serialises only active filters", () => {
    expect(listQuery(EMPTY_FILTERS)).toBe("");
    expect(listQuery({ ...EMPTY_FILTERS, q: " luxury ", tab: "mine", style: "bold", favorites: true, dimensions: "300x250" })).toBe("q=luxury&tab=mine&style=bold&favorites=true&width=300&height=250");
  });
  it("document context annotates compatibility; the compatible filter only applies without one", () => {
    expect(listQuery(EMPTY_FILTERS, "doc-1")).toBe("document_id=doc-1");
    expect(listQuery({ ...EMPTY_FILTERS, compatibleOnly: true }, undefined, "300x250")).toBe("compatible_with=300x250");
    expect(listQuery(EMPTY_FILTERS, undefined, "300x250")).toBe("");
  });
  it("ignores malformed dimensions", () => { expect(listQuery({ ...EMPTY_FILTERS, dimensions: "huge" })).toBe(""); });
});

describe("mapping helpers", () => {
  it("groups every action and keeps unknown groups empty", () => {
    const g = groupMapping([item("moved", "a"), item("preserved", "b"), item("moved", "c")]);
    expect(g.moved.map(i => i.layer_id)).toEqual(["a", "c"]); expect(g.replaced).toEqual([]); expect(g.preserved).toHaveLength(1);
  });
  it("sorts errors before warnings before info", () => {
    expect(sortWarnings([{ code: "a", severity: "info", message: "", layer_id: null }, { code: "b", severity: "error", message: "", layer_id: null }, { code: "c", severity: "warning", message: "", layer_id: null }]).map(w => w.code)).toEqual(["b", "c", "a"]);
  });
  it("never reports compatibility a template does not have", () => {
    const t = { supported_formats: [fmt(300, 250), fmt(1080, 1080)] };
    expect(supports(t, 300, 250)).toBe(true); expect(supports(t, 728, 90)).toBe(false);
  });
  it("summarises sizes and picks a sensible default preview format", () => {
    expect(summarizeSizes([fmt(1, 1), fmt(2, 2), fmt(3, 3), fmt(4, 4), fmt(5, 5)], 2)).toBe("1×1, 2×2 +3"); expect(sizeLabel(fmt(300, 250))).toBe("300×250");
    expect(defaultFormat([fmt(300, 250), fmt(1080, 1080)])).toBe("1080x1080"); expect(defaultFormat([fmt(728, 90)])).toBe("728x90"); expect(defaultFormat([])).toBeUndefined();
  });
});

describe("zod schemas", () => {
  const ok = { name: "My", description: "", category: "beauty", style_tags: ["minimal"], layout_tags: ["centered"], visibility: "organization", extra_formats: "336x280, 600x500", include_background_assets: false, include_decorative_assets: false, preserve_brand_styling: true };
  it("accepts a valid save form and parses extra formats", () => { expect(saveAsTemplateSchema.safeParse(ok).success).toBe(true); expect(parseFormats("336x280, 600x500  1080x1080")).toEqual(["336x280", "600x500", "1080x1080"]); });
  it("rejects empty names, unknown tags, bad visibility and malformed sizes", () => {
    for (const bad of [{ name: "  " }, { category: "weapons" }, { style_tags: ["gaudy"] }, { visibility: "public" }, { extra_formats: "wide" }]) expect(saveAsTemplateSchema.safeParse({ ...ok, ...bad }).success).toBe(false);
  });
  it("a preview targets exactly one of document or campaign", () => {
    const base = { mode: "full", confirm_replace: [], slot_overrides: {} } as const; const id = "11111111-1111-4111-8111-111111111111";
    expect(previewRequestSchema.safeParse({ ...base, document_id: id }).success).toBe(true);
    expect(previewRequestSchema.safeParse({ ...base }).success).toBe(false);
    expect(previewRequestSchema.safeParse({ ...base, document_id: id, campaign_id: id }).success).toBe(false);
    expect(previewRequestSchema.safeParse({ ...base, document_id: id, mode: "everything" }).success).toBe(false);
  });
});
