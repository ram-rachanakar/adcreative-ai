import type { Creative, FormatDef, Job } from "@/types";

export const ACTIVE = new Set(["pending", "analyzing", "briefing", "generating_assets", "composing", "rendering", "validating"]);
export const isActive = (status: string) => ACTIVE.has(status);

export const PLATFORM_LABEL: Record<string, string> = {
  google_display: "Google Display", instagram: "Instagram", facebook: "Facebook", linkedin: "LinkedIn", x: "X", pinterest: "Pinterest", youtube: "YouTube",
};
export const platformLabel = (p: string) => PLATFORM_LABEL[p] ?? p;

export function groupByPlatform(formats: FormatDef[]): [string, FormatDef[]][] {
  const m = new Map<string, FormatDef[]>();
  for (const f of formats) m.set(f.platform, [...(m.get(f.platform) ?? []), f]);
  return [...m.entries()];
}

/** Toggle a set of ids on/off together ("select all" for a platform). Returns a new set. */
export function toggleMany(current: Set<string>, ids: string[], on: boolean): Set<string> {
  const n = new Set(current);
  ids.forEach(i => (on ? n.add(i) : n.delete(i)));
  return n;
}

export function parseCustomSize(text: string, min = 50, max = 4096): { width: number; height: number } | string {
  const m = /^\s*(\d{1,5})\s*[x×*]\s*(\d{1,5})\s*$/i.exec(text);
  if (!m) return "Use the form 1080x1350";
  const [w, h] = [Number(m[1]), Number(m[2])];
  if (w < min || h < min || w > max || h > max) return `Each side must be between ${min} and ${max}px`;
  return { width: w, height: h };
}

export function jobSummary(job: Job): string {
  const ok = job.results.filter(r => r.status === "ok").length;
  const bad = job.results.filter(r => r.status === "failed").length;
  const total = job.planned_units ?? ok + bad;
  if (isActive(job.status)) return `${ok + bad} of ${total} banners processed`;
  if (job.status === "completed") return `${ok} banners created`;
  if (job.status === "partially_completed") return `${ok} created, ${bad} failed`;
  if (job.status === "cancelled") return `Cancelled after ${ok} banners`;
  return job.error ?? "Failed";
}

export type QualityFilter = "all" | "pass" | "warn" | "fail" | "unassessed";
export interface Filters { platform: string; concept: string; favoritesOnly: boolean; attentionOnly: boolean; quality?: QualityFilter }
export function filterCreatives(list: Creative[], f: Filters): Creative[] {
  return list.filter(c =>
    (f.platform === "all" || c.format_keys.some(k => k.startsWith(f.platform + "_")) || (f.platform === "custom" && c.format_keys.length === 0)) &&
    (f.concept === "all" || c.concept_id === f.concept) &&
    (!f.favoritesOnly || c.favorite) && (!f.attentionOnly || c.status === "needs_attention") &&
    (!f.quality || f.quality === "all" || (f.quality === "unassessed" ? c.quality.status === null : c.quality.status === f.quality)));
}

/** Honest label: "not checked" when never assessed, "(outdated)" after an edit. Never invents a number. */
export function qualityLabel(q: Creative["quality"]): string {
  if (q.score === null || q.status === null) return "not checked";
  return `${q.score}/100 · ${q.status}${q.stale ? " (outdated)" : ""}`;
}
