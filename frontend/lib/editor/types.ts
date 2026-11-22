/** Editor document types. Mirrors backend CreativeSpec v2 (flat layers; `z_index` is derived from array order). Konva-free on purpose. */
export type Hex = string;
export type FontFamily = "Liberation Sans" | "Liberation Serif" | "DejaVu Sans";
export const FONT_FAMILIES: FontFamily[] = ["Liberation Sans", "Liberation Serif", "DejaVu Sans"];
export const BG = "background";

export interface Crop { x: number; y: number; w: number; h: number }
interface Base { id: string; role: string; slot?: string | null; name: string; x: number; y: number; w: number; h: number; opacity: number; locked: boolean; visible: boolean; rotation: number; z_index?: number }
export interface TextLayer extends Base { type: "text"; text: string; font_family: FontFamily; font_weight: 400 | 700; font_size: number; min_font_size: number; max_lines: number; auto_fit: boolean; line_height: number; color: Hex; align: "left" | "center" | "right"; valign: "top" | "middle" | "bottom"; letter_spacing: number; uppercase: boolean }
export interface ImageLayer extends Base { type: "image"; asset_id: string; fit: "contain" | "cover" | "fill"; shadow: "none" | "contact" | "drop"; corner_radius: number; crop: Crop | null }
export interface ShapeLayer extends Base { type: "shape"; shape: "rect" | "ellipse"; fill: Hex; radius: number; stroke: Hex | null; stroke_width: number }
export interface ButtonLayer extends Base { type: "button"; label: string; fill: Hex; text_color: Hex; font_family: FontFamily; font_weight: 400 | 700; font_size: number; min_font_size: number; radius: number; uppercase: boolean; padding_x: number | null }
export type Layer = TextLayer | ImageLayer | ShapeLayer | ButtonLayer;

export interface Scrim { color: Hex; opacity: number; x: number; y: number; w: number | null; h: number | null; fade: "none" | "right" | "left"; solid_until: number }
export interface Background { type: "solid" | "gradient" | "image"; color: Hex; gradient_to: Hex | null; angle: number; asset_id: string | null; scrims: Scrim[]; fit: "cover" | "contain" | "fill"; locked: boolean }

/** Canonical document as stored (no derived fields). */
export interface Doc { canvas: { width: number; height: number }; background: Background; elements: Layer[]; meta: Record<string, unknown>; version?: number }

export interface AssetInfo { id: string; url?: string; width?: number; height?: number; mime?: string; missing?: boolean }
export interface FontInfo { family: string; weight: number; file: string }
/** What GET /creative-documents/{id} returns. */
export interface DocView {
  id: string; creative_id: string; campaign_id: string; format_id: string; name: string; schema_version: number; revision: number; rendered_revision: number | null; status: string;
  canvas: { width: number; height: number }; background: Background & { layer_id: string }; layers: Layer[]; metadata: Record<string, unknown>;
  assets: Record<string, AssetInfo>; artboard: { width: number; height: number; format_id: string; safe_zone: { top_pct: number; bottom_pct: number } | null }; fonts: FontInfo[]; updated_at: string | null;
}

export type CommandType = "ADD_LAYER" | "UPDATE_TEXT" | "UPDATE_STYLE" | "MOVE_ELEMENT" | "RESIZE_ELEMENT" | "ROTATE_ELEMENT" | "CHANGE_COLOR" | "REPLACE_IMAGE" | "CHANGE_BACKGROUND" | "CROP_IMAGE" | "REORDER_LAYER" | "DUPLICATE_ELEMENT" | "DELETE_ELEMENT" | "TOGGLE_VISIBILITY" | "TOGGLE_LOCK" | "ALIGN_ELEMENT" | "DISTRIBUTE_ELEMENTS" | "REGENERATE_ELEMENT_ACCEPTED" | "APPLY_PATCH";
export interface Command { type: CommandType; payload: Record<string, unknown> }
export interface Patch { layers: Record<string, Layer | null>; order: string[]; background?: Background }

export const toDoc = (v: DocView): Doc => ({ canvas: v.canvas, background: (({ layer_id: _l, ...b }) => b)(v.background) as Background, elements: v.layers.map(({ z_index: _z, ...l }) => l as Layer), meta: v.metadata });
