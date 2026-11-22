"use client";
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Modal } from "./controls";
import type { AssetItem, EditorAssets } from "./useEditorApi";
import { useEditor } from "@/stores/editor";

export const MAX_UPLOAD = 10 * 1024 * 1024;
export function useEditorAssets(campaignId: string | undefined) {
  return useQuery({ queryKey: ["editor-assets", campaignId], queryFn: () => api<EditorAssets>(`/campaigns/${campaignId}/editor-assets`), enabled: !!campaignId, staleTime: 15_000 });
}

/** Validates a file client-side (type + size) before the server's authoritative checks (MIME sniffing, pixel limits, org ownership). */
export function validateUpload(f: File): string | null {
  if (!/^image\/(png|jpeg|webp)$/.test(f.type)) return "Use a PNG, JPEG or WebP image";
  if (f.size > MAX_UPLOAD) return "Image is larger than 10 MB";
  return null;
}

export default function AssetPicker({ campaignId, kinds, title, onPick, onClose }: { campaignId: string; kinds: ("products" | "logos" | "backgrounds")[]; title: string; onPick: (a: AssetItem) => void; onClose: () => void }) {
  const { data, isLoading } = useEditorAssets(campaignId); const qc = useQueryClient(); const file = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const addAssets = (a: AssetItem) => useEditor.setState(s => ({ assets: { ...s.assets, [a.id]: { id: a.id, url: a.url, width: a.width, height: a.height, mime: a.mime } } }));
  const pick = (a: AssetItem) => { addAssets(a); onPick(a); onClose(); };
  const upload = async (f: File) => {
    const bad = validateUpload(f); if (bad) { setErr(bad); return; }
    setBusy(true); setErr(null);
    try {
      const before = new Set([...(data?.products ?? []), ...(data?.logos ?? [])].map(a => a.id)); const fd = new FormData(); fd.append("file", f);
      await api(`/campaigns/${campaignId}/product/assets?kind=${kinds.includes("logos") && !kinds.includes("products") ? "logo" : "image"}`, { method: "POST", body: fd });
      const fresh = await qc.fetchQuery({ queryKey: ["editor-assets", campaignId], queryFn: () => api<EditorAssets>(`/campaigns/${campaignId}/editor-assets`), staleTime: 0 });
      const added = [...fresh.products, ...fresh.logos].find(a => !before.has(a.id)); if (added) pick(added);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const groups = kinds.map(k => ({ k, items: data?.[k] ?? [] }));
  return (
    <Modal title={title} onClose={onClose} wide>
      {isLoading && <p className="text-sm text-zinc-400">Loading…</p>}
      {groups.map(g => (
        <div key={g.k} className="mb-4"><h4 className="label capitalize">{g.k}</h4>
          {g.items.length === 0 ? <p className="text-xs text-zinc-500">Nothing here yet.</p> :
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">{g.items.map(a => (
              <button key={a.id} onClick={() => pick(a)} aria-label={`Use ${g.k} ${a.id.slice(0, 6)}`} className="group overflow-hidden rounded-lg border border-line bg-[repeating-conic-gradient(#222_0%_25%,#2a2a2a_0%_50%)] bg-[length:16px_16px] hover:border-accent">
                {/* eslint-disable-next-line @next/next/no-img-element */}<img src={a.url} alt="" className="aspect-square w-full object-contain" /><span className="block px-1 py-0.5 text-[10px] text-zinc-400">{a.width}×{a.height}</span></button>))}</div>}
        </div>))}
      <div className="flex items-center gap-3 border-t border-line pt-3">
        <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={e => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void upload(f); }} />
        <button className="btn" disabled={busy} onClick={() => file.current?.click()}>{busy ? "Uploading…" : "Upload image"}</button>
        <span className="text-xs text-zinc-500">PNG, JPEG or WebP up to 10 MB. Uploads are checked on the server; your originals are never overwritten.</span>
      </div>
      {err && <p className="err" role="alert">{err}</p>}
    </Modal>
  );
}
