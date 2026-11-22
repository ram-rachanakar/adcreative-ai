"use client";
import { api, ApiError } from "@/lib/api";
import type { SaveResult, Transport, TransportError } from "@/lib/editor/autosave";
import type { DocView } from "@/lib/editor/types";

/** Maps ApiError/network failures onto the autosave engine's TransportError. status 0 = network failure (retryable). */
const wrap = async <T,>(p: Promise<T>): Promise<T> => {
  try { return await p; } catch (e) {
    if (e instanceof ApiError) throw { status: e.status, code: e.code, message: e.message, body: e.body } satisfies TransportError;
    throw { status: 0, message: "Network error" } satisfies TransportError;
  }
};

export const makeTransport = (docId: string): Transport => ({
  commands: (b) => wrap(api<SaveResult>(`/creative-documents/${docId}/operations`, { method: "POST", json: b })),
  undo: (rev) => wrap(api<SaveResult>(`/creative-documents/${docId}/undo`, { method: "POST", json: { expected_revision: rev } })),
  redo: (rev) => wrap(api<SaveResult>(`/creative-documents/${docId}/redo`, { method: "POST", json: { expected_revision: rev } })),
  reload: () => api<DocView>(`/creative-documents/${docId}`),
});

export interface EditorAssets { products: AssetItem[]; logos: AssetItem[]; backgrounds: AssetItem[]; cutouts: (AssetItem & { parent_asset_id: string | null })[]; brand: { colors: string[]; fonts: string[] } }
export interface AssetItem { id: string; kind: string; url: string; width: number; height: number; mime: string }
export interface VersionRow { id: string; revision: number; version_name: string; change_summary: string; kind: string; created_at: string }
export interface RenderJobOut { output_format?: string; id: string; status: "pending" | "processing" | "completed" | "failed"; url: string | null; width: number | null; height: number | null; warnings: string[]; error_message: string | null; revision: number }
export interface PreflightWarning { code: string; severity: "error" | "warning" | "info"; message: string; layer_id: string | null }
export interface RegenJob { id: string; status: "pending" | "running" | "ready" | "accepted" | "rejected" | "failed" | "cancelled"; target_layer_id: string; target_type: string; source_revision: number; current_revision: number; stale: boolean; target_exists: boolean; target_changed: boolean;
  candidates: { index: number; asset_id?: string; url?: string; text?: string; label?: string; method?: string }[]; error_message: string | null; provider: string | null; usage: Record<string, unknown> }
