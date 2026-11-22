"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { DocView } from "@/lib/editor/types";
import { useEditor } from "@/stores/editor";
import { Modal } from "./controls";
import type { VersionRow } from "./useEditorApi";

const KIND: Record<string, string> = { initial: "Original", manual: "Saved", auto: "Auto checkpoint", ai_accept: "AI edit", restore_point: "Before restore" };

export default function VersionPanel({ documentId, onClose }: { documentId: string; onClose: () => void }) {
  const qc = useQueryClient(); const [name, setName] = useState(""); const [err, setErr] = useState<string | null>(null); const [confirm, setConfirm] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["versions", documentId], queryFn: async () => { await useEditor.getState().saveNow(); return api<VersionRow[]>(`/creative-documents/${documentId}/versions`); } });
  const save = useMutation({ mutationFn: async () => { await useEditor.getState().saveNow(); return api(`/creative-documents/${documentId}/versions`, { method: "POST", json: { name: name.trim() || "Saved version" } }); }, onSuccess: () => { setName(""); qc.invalidateQueries({ queryKey: ["versions", documentId] }); }, onError: e => setErr((e as Error).message) });
  const restore = useMutation({
    mutationFn: async (id: string) => { await useEditor.getState().saveNow(); return api<{ document: DocView }>(`/creative-documents/${documentId}/restore/${id}`, { method: "POST" }); },
    onSuccess: r => { useEditor.getState().adoptServer(r.document, "Restore version"); qc.invalidateQueries({ queryKey: ["versions", documentId] }); setConfirm(null); onClose(); }, onError: e => setErr((e as Error).message),
  });
  return (
    <Modal title="Version history" onClose={onClose}>
      <div className="mb-4 flex gap-2"><input aria-label="Version name" className="input" placeholder="Name this version (optional)" value={name} maxLength={200} onChange={e => setName(e.target.value)} /><button className="btn shrink-0" disabled={save.isPending} onClick={() => save.mutate()}>Save version</button></div>
      {err && <p className="err" role="alert">{err}</p>}
      {q.isLoading && <p className="text-sm text-zinc-400">Loading…</p>}
      <ul className="space-y-2">{(q.data ?? []).map(v => (
        <li key={v.id} className="flex items-center justify-between gap-2 rounded-lg border border-line p-2.5 text-sm">
          <div><div className="font-medium">{v.version_name || KIND[v.kind]}</div><div className="text-xs text-zinc-400">{KIND[v.kind] ?? v.kind} · revision {v.revision} · {new Date(v.created_at).toLocaleString()}</div>{v.change_summary && <div className="text-xs text-zinc-500">{v.change_summary}</div>}</div>
          {confirm === v.id ? <span className="flex gap-1"><button className="btn !px-2 !py-1 text-xs" disabled={restore.isPending} onClick={() => restore.mutate(v.id)}>Confirm restore</button><button className="rounded-lg border border-line px-2 py-1 text-xs" onClick={() => setConfirm(null)}>Cancel</button></span>
            : <button className="rounded-lg border border-line px-3 py-1 text-xs hover:border-accent" onClick={() => setConfirm(v.id)}>Restore</button>}
        </li>))}</ul>
      <p className="mt-3 text-xs text-zinc-500">Restoring creates a new revision, so nothing is lost and you can undo it. Versions are immutable.</p>
    </Modal>
  );
}
