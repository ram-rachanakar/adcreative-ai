import { z } from "zod";
import { INDUSTRIES, LAYOUTS, STYLES } from "./types";

const FORMAT = /^\d{2,4}x\d{2,4}$/;
export const saveAsTemplateSchema = z.object({
  name: z.string().trim().min(1, "Give the template a name").max(200),
  description: z.string().max(1000).default(""),
  category: z.enum(INDUSTRIES),
  style_tags: z.array(z.enum(STYLES)).max(7),
  layout_tags: z.array(z.enum(LAYOUTS)).max(5),
  visibility: z.enum(["personal", "organization"]),
  extra_formats: z.string().default("").refine(v => v.split(/[\s,]+/).filter(Boolean).every(x => FORMAT.test(x)), "Use sizes like 336x280, separated by commas"),
  include_background_assets: z.boolean(),
  include_decorative_assets: z.boolean(),
  preserve_brand_styling: z.boolean(),
  new_version_of: z.string().optional(),
});
export type SaveAsTemplateForm = z.infer<typeof saveAsTemplateSchema>;
export const parseFormats = (v: string) => v.split(/[\s,]+/).filter(Boolean);

export const previewRequestSchema = z.object({
  mode: z.enum(["layout_only", "layout_style", "full"]),
  document_id: z.string().uuid().optional(), campaign_id: z.string().uuid().optional(), format_id: z.string().regex(FORMAT).optional(),
  confirm_replace: z.array(z.string()), slot_overrides: z.record(z.string()),
}).refine(r => !!r.document_id !== !!r.campaign_id, "Choose either an existing banner or a campaign");
