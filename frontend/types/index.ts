export type Role = "owner" | "admin" | "designer" | "marketer" | "viewer";
export interface Org { id: string; name: string; slug: string; plan: string }
export interface Me { id: string; email: string; name: string; csrf_token: string; memberships: { organization: Org; role: Role }[] }
export interface Campaign { id: string; project_id: string; name: string; status: string; product_url: string | null; description: string; brief: Record<string, unknown> }
export interface Project { id: string; workspace_id: string; name: string; description: string }
export interface Workspace { id: string; name: string }
export interface Dashboard { campaigns: number; projects: number; generated_creatives: number; recent_campaigns: Campaign[] }
export interface Asset { id: string; kind: "image" | "logo"; mime: string; width: number; height: number; origin: string; source_url: string | null; url: string }
export interface ProductData { title?: string | null; description?: string | null; category?: string | null; brand_name?: string | null; price?: string | null; currency?: string | null; features: string[]; specifications: Record<string, string>; variants: string[]; user_description?: string }
export interface Product { id: string; campaign_id: string; source_url: string | null; data: ProductData; extraction_meta: { warnings?: string[]; sources?: Record<string, string>; final_url?: string }; user_edited: boolean; assets: Asset[] }
export interface Brand { id: string; name: string; detected: { colors?: string[]; color_source?: string | null; name?: string | null }; overrides: { colors?: string[] }; guidelines: string; effective: { colors?: string[] } }
export interface ConceptData { name: string; approach: string; advertising_angle: string; target_audience: string; marketing_objective: string; headline: string; alternative_headlines: string[]; supporting_copy: string; cta: string; emotional_appeal: string; visual_direction: string; background_description: string; product_positioning: string; color_palette: string[]; assumptions: string[]; claims_used: { text: string; source_field: string }[] }
export interface ClaimFlag { field: string; text: string; rule: string }
export interface Concept { id: string; campaign_id: string; approach: string; data: ConceptData; flags: ClaimFlag[]; status: "ok" | "flagged" | "acknowledged"; selected: boolean; edited: boolean; prompt_version: string; provider: string; model: string }
export interface Brief { id: string; concept_id: string; prompt_version: string; data: { objective: string; key_message: string; background_prompt_final: string; composition_notes: string; mood: string } }
export interface Usage { by_kind: { kind: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number; priced: boolean }[]; total_cost_usd: number; fully_priced: boolean }
export interface RenderElementReport { id: string; type: string; font_size: number | null; lines: number | null; text_overflow: boolean; out_of_bounds: boolean; shrunk: boolean }
export interface Creative { id: string; campaign_id: string; concept_id: string; format_keys: string[]; job_id: string | null; width: number; height: number; status: "rendered" | "needs_attention"; spec_version: number; favorite: boolean; url: string | null; quality: { score: number | null; status: QualityStatus | null; stale: boolean }; render_report: { ok?: boolean; errors?: string[]; missing_assets?: string[]; elements?: RenderElementReport[] }; spec?: CreativeSpec }
export type QualityStatus = "pass" | "warn" | "fail";
export interface QualityFinding { code: string; category: string; severity: "fail" | "warn" | "info"; message: string; element_id: string | null; fix: string | null; source?: string }
export interface QualityReport {
  assessed: true; id: string; creative_id: string; score: number; status: QualityStatus; deterministic_score: number; stale: boolean; can_revert: boolean; spec_version: number;
  categories: Record<string, number>; findings: QualityFinding[]; passed: string[]; blocking: string[];
  initial: { score: number; status: QualityStatus; blocking: string[] };
  corrections: { round: number | string; actions: { action: string; issue: string; element_id: string | null; detail: string }[]; score_before: number; score_after: number; accepted: boolean }[];
  recommendations: { code: string; message: string; suggestion: string; action: "regenerate_background" | "edit" }[];
  vision: null | { requested: boolean; assessed?: boolean; counted_in_score?: boolean; score?: number | null; scores?: Record<string, number | null> | null; summary?: string | null; provider?: string; model?: string; error?: string; skipped?: string };
}
export type QualityResponse = QualityReport | { assessed: false; creative_id: string; history: unknown[] };
export interface QualitySummary { total: number; counts: Record<QualityStatus | "unassessed", number>; average_score: number | null; stale: number }
export interface SpecElement { id: string; type: string; role: string; text?: string; label?: string; [k: string]: unknown }
export interface CreativeSpec { canvas: { width: number; height: number }; elements: SpecElement[]; meta: { notes: string[]; family?: string; template?: string }; [k: string]: unknown }
export interface FormatDef { id: string; platform: string; placement: string; name: string; width: number; height: number; file_types: string[]; max_file_kb: number | null; safe_zone: { top_pct: number; bottom_pct: number } | null; eligibility_notes: string; verification: string; validated_on: string | null }
export interface FormatRegistry { registry_version: number; note: string; platforms: string[]; formats: FormatDef[]; custom_limits: { min: number; max: number } }
export interface Preset { id: string; name: string; format_ids: string[]; custom_sizes: { width: number; height: number }[] }
export interface JobResult { quality_score?: number; quality_status?: QualityStatus; quality_corrections?: number; unit: string; concept_id: string; width: number; height: number; status: "ok" | "failed"; creative_id?: string; error?: string | null; needs_attention?: boolean }
export interface Job { id: string; kind?: string; campaign_id: string; status: string; progress: number; stage_detail: string; results: JobResult[]; error: string | null; cost_usd: number; attempts: number; cancel_requested: boolean; dead_lettered: boolean; planned_units: number | null }
export interface ExportJob { id: string; status: "pending" | "processing" | "completed" | "failed"; file_count: number; size_bytes: number; error: string | null; url: string | null; manifest: { quality_gate?: string; skipped?: { creative_id: string; width: number; height: number; reason: string }[]; items: { file: string; width: number; height: number; platform: string; placement: string; file_size_bytes: number; warnings: string[] }[] } | null }
