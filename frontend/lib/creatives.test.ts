import { describe, expect, it } from "vitest";
import { filterCreatives, groupByPlatform, isActive, jobSummary, parseCustomSize, qualityLabel, toggleMany } from "./creatives";
import type { Creative, FormatDef, Job } from "@/types";

const cr = (o: Partial<Creative>): Creative => ({ id: "1", campaign_id: "c", concept_id: "k1", format_keys: [], job_id: null, width: 1, height: 1, status: "rendered", spec_version: 1, favorite: false, url: null, render_report: {}, quality: { score: null, status: null, stale: false }, ...o });
const fd = (platform: string, id: string): FormatDef => ({ id, platform, placement: "p", name: "n", width: 1, height: 1, file_types: ["png"], max_file_kb: null, safe_zone: null, eligibility_notes: "", verification: "unverified", validated_on: null });

describe("creatives helpers", () => {
  it("groups formats by platform preserving order", () => {
    const g = groupByPlatform([fd("x", "a"), fd("instagram", "b"), fd("x", "c")]);
    expect(g.map(([p, f]) => [p, f.map(i => i.id)])).toEqual([["x", ["a", "c"]], ["instagram", ["b"]]]);
  });
  it("select-all toggles many ids immutably", () => {
    const base = new Set(["a"]);
    const on = toggleMany(base, ["b", "c"], true);
    expect([...on].sort()).toEqual(["a", "b", "c"]);
    expect([...base]).toEqual(["a"]);
    expect([...toggleMany(on, ["a", "b"], false)]).toEqual(["c"]);
  });
  it("validates custom sizes", () => {
    expect(parseCustomSize("1080 x 1350")).toEqual({ width: 1080, height: 1350 });
    expect(parseCustomSize("728×90")).toEqual({ width: 728, height: 90 });
    expect(typeof parseCustomSize("10x10")).toBe("string");
    expect(typeof parseCustomSize("9999x100")).toBe("string");
    expect(typeof parseCustomSize("wide")).toBe("string");
  });
  it("knows which job states are still running", () => {
    expect(isActive("rendering")).toBe(true);
    for (const s of ["completed", "partially_completed", "failed", "cancelled"]) expect(isActive(s)).toBe(false);
  });
  it("summarises jobs honestly", () => {
    const job = (status: string, results: Job["results"]): Job => ({ id: "j", campaign_id: "c", status, progress: 0, stage_detail: "", results, error: "boom", cost_usd: 0, attempts: 0, cancel_requested: false, dead_lettered: false, planned_units: 4 });
    const ok = { unit: "u", concept_id: "k", width: 1, height: 1, status: "ok" as const };
    expect(jobSummary(job("rendering", [ok]))).toBe("1 of 4 banners processed");
    expect(jobSummary(job("partially_completed", [ok, { ...ok, status: "failed" }]))).toBe("1 created, 1 failed");
    expect(jobSummary(job("failed", []))).toBe("boom");
  });
  it("filters by platform, concept, favourites and attention", () => {
    const list = [cr({ id: "a", format_keys: ["instagram_story_1080x1920"], favorite: true }), cr({ id: "b", format_keys: ["google_display_x"], concept_id: "k2", status: "needs_attention" }), cr({ id: "c" })];
    const f = { platform: "all", concept: "all", favoritesOnly: false, attentionOnly: false };
    expect(filterCreatives(list, f)).toHaveLength(3);
    expect(filterCreatives(list, { ...f, platform: "instagram" }).map(c => c.id)).toEqual(["a"]);
    expect(filterCreatives(list, { ...f, platform: "custom" }).map(c => c.id)).toEqual(["c"]);
    expect(filterCreatives(list, { ...f, concept: "k2" }).map(c => c.id)).toEqual(["b"]);
    expect(filterCreatives(list, { ...f, favoritesOnly: true }).map(c => c.id)).toEqual(["a"]);
    expect(filterCreatives(list, { ...f, attentionOnly: true }).map(c => c.id)).toEqual(["b"]);
  });
});

describe("quality helpers", () => {
  const mk = (q: Partial<Creative["quality"]>, extra: Partial<Creative> = {}): Creative => ({ id: "x", campaign_id: "c", concept_id: "k", format_keys: [], job_id: null, width: 1, height: 1, status: "rendered", spec_version: 1, favorite: false, url: null, render_report: {}, quality: { score: null, status: null, stale: false, ...q }, ...extra });
  it("never invents a score for banners that were not checked", () => {
    expect(qualityLabel(mk({}).quality)).toBe("not checked");
    expect(qualityLabel(mk({ score: 91, status: "pass" }).quality)).toBe("91/100 · pass");
    expect(qualityLabel(mk({ score: 91, status: "pass", stale: true }).quality)).toContain("outdated");
  });
  it("filters by quality status, including unchecked", () => {
    const list = [mk({ score: 90, status: "pass" }, { id: "a" }), mk({ score: 50, status: "fail" }, { id: "b" }), mk({}, { id: "c" })];
    const base = { platform: "all", concept: "all", favoritesOnly: false, attentionOnly: false };
    expect(filterCreatives(list, { ...base, quality: "fail" }).map(c => c.id)).toEqual(["b"]);
    expect(filterCreatives(list, { ...base, quality: "unassessed" }).map(c => c.id)).toEqual(["c"]);
    expect(filterCreatives(list, { ...base, quality: "all" })).toHaveLength(3);
    expect(filterCreatives(list, base)).toHaveLength(3);
  });
});
