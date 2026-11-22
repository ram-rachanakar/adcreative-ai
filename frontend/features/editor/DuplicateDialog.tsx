"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { DocView } from "@/lib/editor/types";
import { useEditor } from "@/stores/editor";
import type { FormatRegistry } from "@/types";
import { Modal, NumberField } from "./controls";

/** Copy this design to another banner size: creates a NEW banner + document (basic proportional fit). The original is untouched and the two never sync. */
export default function DuplicateDialog({ documentId, campaignId, onClose }: { documentId: string; campaignId: string; onClose: () => void }) {
  const router = useRouter(); const view = useEditor(s => s.view)!;
  const reg = useQuery({ queryKey: ["formats"], queryFn: () => api<FormatRegistry>("/formats"), staleTime: Infinity });
  const sizes = [...new Map((reg.data?.formats ?? []).map(f => [`${f.width}x${f.height}`, f])).values()].filter(f => !(f.width === view.canvas.width && f.height === view.canvas.height));
  const [w, setW] = useState(1080), [h, setH] = useState(1080); const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setErr(null);
    try { await useEditor.getState().saveNow(); const nd = await api<DocView>(`/creative-documents/${documentId}/duplicate`, { method: "POST", json: { width: w, height: h } }); router.push(`/campaigns/${campaignId}/editor/${nd.creative_id}`); onClose(); }
    catch (e) { setErr((e as Error).message); setBusy(false); }
  };
  return (
    <Modal title="Copy design to another size" onClose={onClose}>
      <p className="mb-3 text-sm text-zinc-300">Creates a <b>new</b> banner from this design. Your current banner stays exactly as it is, and the two are never kept in sync afterwards. Layout is scaled proportionally — expect to adjust it.</p>
      <label className="label" htmlFor="dup-preset">Preset size</label>
      <select id="dup-preset" className="input mb-3" value="" onChange={e => { const f = sizes.find(x => x.id === e.target.value); if (f) { setW(f.width); setH(f.height); } }}>
        <option value="">Choose…</option>{sizes.map(f => <option key={f.id} value={f.id}>{f.width}×{f.height} · {f.name}</option>)}</select>
      <div className="mb-4 flex items-center gap-2 text-sm">Custom <NumberField label="New width" value={w} min={50} max={4096} onCommit={setW} />×<NumberField label="New height" value={h} min={50} max={4096} onCommit={setH} />px</div>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="flex justify-end gap-2"><button className="rounded-lg border border-line px-4 py-2 text-sm" onClick={onClose}>Cancel</button><button className="btn" disabled={busy} onClick={go}>Create {w}×{h} banner</button></div>
    </Modal>
  );
}
