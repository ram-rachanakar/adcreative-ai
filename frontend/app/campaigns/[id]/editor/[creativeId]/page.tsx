"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useRequireAuth } from "@/hooks/useAuth";
import { api, ApiError } from "@/lib/api";
import type { QueueItem } from "@/lib/editor/autosave";
import type { DocView } from "@/lib/editor/types";
import EditorShell from "@/features/editor/EditorShell";
import { makeTransport } from "@/features/editor/useEditorApi";
import { useEditor } from "@/stores/editor";
import { useSession } from "@/stores/session";

const DEBOUNCE = Number(process.env.NEXT_PUBLIC_EDITOR_AUTOSAVE_MS ?? 800);   // configurable autosave debounce

export default function EditorPage() {
  const { id, creativeId } = useParams<{ id: string; creativeId: string }>();
  const { me, loading } = useRequireAuth(); const orgId = useSession(s => s.orgId); const loaded = useEditor(s => s.view?.creative_id);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!me || !orgId) return; let cancelled = false; const role = me.memberships.find(m => m.organization.id === orgId)?.role; const canEdit = role !== "viewer";
    (async () => {
      try {
        const view = canEdit ? await api<DocView>(`/creatives/${creativeId}/document`, { method: "POST" }) : await api<DocView>(`/creatives/${creativeId}/document`);
        if (cancelled) return; let recover: QueueItem[] | undefined;
        try { const raw = localStorage.getItem(`editor-pending:${view.id}`); if (raw) { const p = JSON.parse(raw) as { base: number; items: QueueItem[] }; if (p.base === view.revision) recover = p.items; else localStorage.removeItem(`editor-pending:${view.id}`); } } catch { /* storage unavailable */ }
        useEditor.getState().load(view, makeTransport(view.id), { readOnly: !canEdit, debounceMs: DEBOUNCE, isOnline: () => navigator.onLine, recover });
      } catch (e) { if (!cancelled) setError(e instanceof ApiError && e.status === 404 ? "This banner doesn't exist or you don't have access to it." : (e as Error).message); }
    })();
    return () => { cancelled = true; void useEditor.getState().saveNow().finally(() => { /* leave the store loaded: the next route load() replaces it */ }); };
  }, [me, orgId, creativeId]);
  if (loading || !me) return <div className="grid min-h-screen place-items-center text-zinc-500">Loading…</div>;
  if (error) return <div className="grid min-h-screen place-items-center text-center"><div><p className="mb-3 text-red-400" role="alert">{error}</p><a className="btn" href={`/campaigns/${id}/creatives`}>Back to banners</a></div></div>;
  if (!loaded || loaded !== creativeId) return <div className="grid min-h-screen place-items-center text-zinc-500">Opening editor…</div>;
  return <EditorShell campaignId={id} creativeId={creativeId} />;
}
