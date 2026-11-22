/** Template system types (mirrors backend /templates API). Server state only: none of this lives in the editor's Zustand store. */
export type ApplyMode = "layout_only" | "layout_style" | "full";
export type Tab = "all" | "layouts" | "styles" | "industry" | "mine";
export const INDUSTRIES = ["beauty", "fashion", "electronics", "food", "home", "fitness", "services", "general"] as const;
export const STYLES = ["minimal", "luxury", "modern", "bold", "editorial", "corporate", "promotional"] as const;
export const LAYOUTS = ["centered", "split", "full-bleed", "product-focused", "text-focused"] as const;
export const ASPECTS = ["wide", "landscape", "square", "portrait", "tall"] as const;
export const MODE_INFO: Record<ApplyMode, { label: string; summary: string; changes: string; keeps: string }> = {
  layout_only: { label: "Layout only", summary: "Rearranges your elements.", changes: "Positions, sizes, alignment, layer order.", keeps: "Copy, fonts, colours, product, logo, background and CTA styling." },
  layout_style: { label: "Layout + style", summary: "New arrangement and look.", changes: "Layout plus typography, shapes, decoration and colours (brand rules win).", keeps: "Product, logo, campaign copy, CTA text and background." },
  full: { label: "Full template", summary: "The complete template.", changes: "Layout, styling, background and decoration.", keeps: "Product and logo unless you confirm replacing them. Needs your confirmation." },
};

export interface SupportedFormat { format_id: string; width: number; height: number; aspect_ratio: number; aspect_class: string; inherits: string | null; placements: string[] }
export interface Thumb { asset_id: string; url: string; width: number; height: number; format_id: string }
export interface TemplateSummary {
  id: string; name: string; description: string; owner_type: "system" | "organization" | "personal"; owner: string; visibility: string; category: string; style_tags: string[]; layout_tags: string[];
  status: "draft" | "published" | "archived"; current_version: number; source: string; application_modes: ApplyMode[]; supported_formats: SupportedFormat[]; favorite: boolean; can_edit: boolean;
  compatible?: boolean; thumbnail?: Thumb | null;
}
export interface TemplateList { total: number; items: TemplateSummary[]; filters: { industries: string[]; styles: string[]; layouts: string[] } }
export interface TemplateDetail extends TemplateSummary { definition: Record<string, unknown>; brand_constraints: Record<string, unknown>; slots_by_format: Record<string, { id: string; role: string; kind: string; required: boolean }[]> }
export interface TemplateVersionRow { version: number; change_note: string; created_at: string; current: boolean; formats: string[] }

export type Action = "preserved" | "moved" | "restyled" | "replaced" | "added" | "removed";
export interface MappingItem { slot: string | null; layer_id: string; layer_name: string; action: Action; changes: string[]; note: string }
export interface Warning { code: string; severity: "error" | "warning" | "info"; message: string; layer_id: string | null }
export interface QualitySnapshot { score?: number; status?: string; findings?: { code: string; severity: string; message: string }[]; error?: string }
export interface Preview {
  application_id: string; status: string; mode: ApplyMode; format_id: string; template: { id: string; name: string; version: number }; document_id: string | null; campaign_id: string | null; source_revision: number | null;
  before_url: string | null; after_url: string | null; warnings: Warning[]; requires_confirmation: boolean; needs_confirmation: string[]; can_apply: boolean; quality: { before: QualitySnapshot | null; after: QualitySnapshot | null };
  mapping: { items: MappingItem[]; missing_required: string[]; ambiguities: { slot: string; candidates: string[]; chosen: string }[]; unmapped_layers: string[]; counts: Record<string, number> };
}
export interface PreviewRequest { template_version?: number; document_id?: string; campaign_id?: string; format_id?: string; mode: ApplyMode; confirm_replace: string[]; slot_overrides: Record<string, string> }
export interface SaveResult { template: TemplateSummary; notes: string[]; included_assets: { asset_id: string; role: string }[] }
export interface Filters { q: string; tab: Tab; industry: string; style: string; layout: string; aspect: string; owner: string; source: string; favorites: boolean; compatibleOnly: boolean; dimensions: string }
export const EMPTY_FILTERS: Filters = { q: "", tab: "all", industry: "", style: "", layout: "", aspect: "", owner: "", source: "", favorites: false, compatibleOnly: false, dimensions: "" };
