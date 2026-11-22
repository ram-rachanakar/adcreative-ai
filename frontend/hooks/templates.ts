"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listQuery, templateApi } from "@/lib/templates/templateApi";
import type { Filters, PreviewRequest } from "@/lib/templates/types";

export const tkeys = { all: ["templates"] as const, list: (qs: string) => ["templates", "list", qs] as const, detail: (id: string, v?: number) => ["templates", "detail", id, v ?? 0] as const };

export function useTemplates(filters: Filters, opts: { documentId?: string; compat?: string; enabled?: boolean } = {}) {
  const qs = listQuery(filters, opts.documentId, opts.compat);
  return useQuery({ queryKey: tkeys.list(qs), queryFn: () => templateApi.list(qs), enabled: opts.enabled ?? true, staleTime: 15_000 });
}
export const useTemplateDetails = (id: string | null, version?: number) =>
  useQuery({ queryKey: tkeys.detail(id ?? "", version), queryFn: () => templateApi.get(id!, version), enabled: !!id });
export const useTemplateVersions = (id: string | null) => useQuery({ queryKey: ["templates", "versions", id], queryFn: () => templateApi.versions(id!), enabled: !!id });
export const useThumbnail = (id: string | null, formatId: string | undefined, version?: number) =>
  useQuery({ queryKey: ["templates", "thumb", id, formatId, version ?? 0], queryFn: () => templateApi.thumbnail(id!, formatId!, version), enabled: !!id && !!formatId, staleTime: 30 * 60_000 });

export function useTemplatePreview(templateId: string) {
  return useMutation({ mutationFn: (body: PreviewRequest) => templateApi.preview(templateId, body) });
}
export function useTemplateApplication(templateId: string) {
  const qc = useQueryClient();
  const apply = useMutation({ mutationFn: (b: { application_id: string; expected_revision?: number; confirm?: boolean }) => templateApi.apply(templateId, b), onSuccess: () => void qc.invalidateQueries({ queryKey: tkeys.all }) });
  const reject = useMutation({ mutationFn: (applicationId: string) => templateApi.reject(applicationId) });
  return { apply, reject };
}
export function useSaveAsTemplate(documentId: string) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (json: Record<string, unknown>) => templateApi.saveAs(documentId, json), onSuccess: () => void qc.invalidateQueries({ queryKey: tkeys.all }) });
}
export function useTemplateActions() {
  const qc = useQueryClient(); const done = () => void qc.invalidateQueries({ queryKey: tkeys.all });
  return {
    favorite: useMutation({ mutationFn: (v: { id: string; on: boolean }) => templateApi.favorite(v.id, v.on), onSuccess: done }),
    publish: useMutation({ mutationFn: (id: string) => templateApi.publish(id), onSuccess: done }),
    archive: useMutation({ mutationFn: (id: string) => templateApi.archive(id), onSuccess: done }),
    duplicate: useMutation({ mutationFn: (v: { id: string; name?: string; visibility: "personal" | "organization" }) => templateApi.duplicate(v.id, { name: v.name, visibility: v.visibility }), onSuccess: done }),
    patch: useMutation({ mutationFn: (v: { id: string; json: Parameters<typeof templateApi.patch>[1] }) => templateApi.patch(v.id, v.json), onSuccess: done }),
  };
}
