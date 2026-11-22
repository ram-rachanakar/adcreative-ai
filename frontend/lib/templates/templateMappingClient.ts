import type { Action, MappingItem, SupportedFormat, TemplateSummary, Warning } from "./types";

export const ACTION_ORDER: Action[] = ["replaced", "added", "removed", "restyled", "moved", "preserved"];
export const ACTION_LABEL: Record<Action, string> = { preserved: "Preserved", moved: "Moved", restyled: "Restyled", replaced: "Replaced", added: "Added", removed: "Removed" };
export const ACTION_HELP: Record<Action, string> = {
  preserved: "Kept exactly as it is", moved: "New position or size", restyled: "New look (and position)", replaced: "Swapped for template content", added: "New decoration from the template", removed: "Previous template decoration taken out",
};
export const SLOT_LABEL = (s: string | null) => (s ? s.replace(/_/g, " ") : "no matching slot");

export function groupMapping(items: MappingItem[]): Record<Action, MappingItem[]> {
  const out = { preserved: [], moved: [], restyled: [], replaced: [], added: [], removed: [] } as Record<Action, MappingItem[]>;
  for (const i of items) out[i.action].push(i);
  return out;
}
export const changedCount = (items: MappingItem[]) => items.filter(i => i.action !== "preserved").length;
export const sortWarnings = (w: Warning[]) => [...w].sort((a, b) => ["error", "warning", "info"].indexOf(a.severity) - ["error", "warning", "info"].indexOf(b.severity));

/** Formats a template can serve for this banner size; never claims compatibility it does not have. */
export const supports = (t: Pick<TemplateSummary, "supported_formats">, w: number, h: number) => t.supported_formats.some(f => f.width === w && f.height === h);
export const sizeLabel = (f: Pick<SupportedFormat, "width" | "height">) => `${f.width}×${f.height}`;
export const summarizeSizes = (fs: SupportedFormat[], max = 4) => (fs.length <= max ? fs.map(sizeLabel).join(", ") : `${fs.slice(0, max).map(sizeLabel).join(", ")} +${fs.length - max}`);
export const defaultFormat = (fs: SupportedFormat[]) => (["1080x1080", "300x250", "1080x1350"].map(id => fs.find(f => f.format_id === id)).find(Boolean) ?? fs[0])?.format_id;
