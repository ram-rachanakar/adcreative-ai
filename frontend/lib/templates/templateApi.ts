import { api } from "@/lib/api";
import type { DocView } from "@/lib/editor/types";
import type { Filters, Preview, PreviewRequest, SaveResult, TemplateDetail, TemplateList, TemplateSummary, TemplateVersionRow, Thumb } from "./types";

export function listQuery(f: Filters, documentId?: string, compat?: string): string {
  const p = new URLSearchParams();
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.tab !== "all") p.set("tab", f.tab);
  for (const k of ["industry", "style", "layout", "aspect", "owner", "source"] as const) if (f[k]) p.set(k, f[k]);
  if (f.favorites) p.set("favorites", "true");
  if (f.dimensions && /^\d{2,4}x\d{2,4}$/.test(f.dimensions)) { const [w, h] = f.dimensions.split("x"); p.set("width", w); p.set("height", h); }
  if (documentId) p.set("document_id", documentId);
  else if (compat && f.compatibleOnly) p.set("compatible_with", compat);
  return p.toString();
}
export const templateApi = {
  list: (qs: string) => api<TemplateList>(`/templates${qs ? `?${qs}` : ""}`),
  get: (id: string, version?: number) => api<TemplateDetail>(`/templates/${id}${version ? `?version=${version}` : ""}`),
  versions: (id: string) => api<TemplateVersionRow[]>(`/templates/${id}/versions`),
  thumbnail: (id: string, formatId: string, version?: number) => api<Thumb>(`/templates/${id}/layouts/${formatId}/thumbnail${version ? `?version=${version}` : ""}`),
  preview: (id: string, body: PreviewRequest) => api<Preview>(`/templates/${id}/preview`, { method: "POST", json: body }),
  apply: (id: string, body: { application_id: string; expected_revision?: number; confirm?: boolean }) =>
    api<{ document: DocView; application_id: string; created: boolean; creative_id?: string }>(`/templates/${id}/apply`, { method: "POST", json: body }),
  reject: (applicationId: string) => api<{ status: string }>(`/template-applications/${applicationId}/reject`, { method: "POST" }),
  revert: (applicationId: string, force = false) => api<{ document: DocView }>(`/template-applications/${applicationId}/revert`, { method: "POST", json: { force } }),
  favorite: (id: string, on: boolean) => api<{ favorite: boolean }>(`/templates/${id}/favorite`, { method: on ? "POST" : "DELETE" }),
  publish: (id: string) => api<TemplateSummary>(`/templates/${id}/publish`, { method: "POST" }),
  archive: (id: string) => api<TemplateSummary>(`/templates/${id}/archive`, { method: "POST" }),
  duplicate: (id: string, json: { name?: string; visibility: "personal" | "organization" }) => api<TemplateSummary>(`/templates/${id}/duplicate`, { method: "POST", json }),
  patch: (id: string, json: Partial<Pick<TemplateSummary, "name" | "description" | "category" | "style_tags" | "layout_tags">> & { visibility?: string }) => api<TemplateSummary>(`/templates/${id}`, { method: "PATCH", json }),
  saveAs: (documentId: string, json: Record<string, unknown>) => api<SaveResult>(`/creative-documents/${documentId}/save-as-template`, { method: "POST", json }),
};
