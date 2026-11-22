"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ExportJob } from "@/types";

export default function ExportPanel({ campaignId, selected, total }: { campaignId: string; selected: string[]; total: number }) {
  const [format, setFormat] = useState("png");
  const [quality, setQuality] = useState(90);
  const [gate, setGate] = useState(false);
  const [transparent, setTransparent] = useState(false);
  const canAlpha = format !== "jpeg";
  const [id, setId] = useState<string | null>(null);
  const start = useMutation({ mutationFn: () => api<ExportJob>(`/campaigns/${campaignId}/exports`, { method: "POST", json: { creative_ids: selected.length ? selected : null, format, quality, quality_gate: gate ? "block_failed" : "off", transparent: transparent && canAlpha } }), onSuccess: e => setId(e.id) });
  const exp = useQuery({ queryKey: ["export", id], enabled: !!id, queryFn: () => api<ExportJob>(`/exports/${id}`), refetchInterval: q => (q.state.data && ["completed", "failed"].includes(q.state.data.status) ? false : 1000) });
  const e = exp.data;
  const warnings = e?.manifest?.items.filter(i => i.warnings.length) ?? [];
  return (
    <div className="card space-y-3">
      <h2 className="font-medium">Export</h2>
      <div className="flex flex-wrap items-end gap-3">
        <div><label className="label">Format</label><select className="input" value={format} onChange={ev => setFormat(ev.target.value)}>{["png", "jpeg", "webp", "svg"].map(f => <option key={f}>{f}</option>)}</select></div>
        {(format === "jpeg" || format === "webp") && <div><label className="label">Quality {quality}</label><input type="range" min={40} max={100} value={quality} onChange={ev => setQuality(Number(ev.target.value))} /></div>}
        <label className={`flex items-center gap-1 text-xs ${canAlpha ? "text-zinc-300" : "text-zinc-600"}`} title={canAlpha ? "" : "JPEG has no transparency — choose PNG, WebP or SVG"}><input type="checkbox" checked={transparent && canAlpha} disabled={!canAlpha} onChange={ev => setTransparent(ev.target.checked)} />Transparent background</label>
        <label className="flex items-center gap-1 text-xs text-zinc-300"><input type="checkbox" checked={gate} onChange={ev => setGate(ev.target.checked)} />Leave out banners that failed the quality check</label>
        <button className="btn" disabled={total === 0 || start.isPending || (!!e && !["completed", "failed"].includes(e.status))} onClick={() => start.mutate()}>
          {(start.isPending || (e && !["completed", "failed"].includes(e.status))) && <Loader2 className="animate-spin" size={16} />}
          {selected.length ? `Export ${selected.length} selected (ZIP)` : `Export all ${total} (ZIP)`}</button>
      </div>
      <p className="text-xs text-zinc-500">Dimensions are never changed on export; every file is re-opened and checked against the exact size. The ZIP includes a manifest and thumbnails.</p>
      {start.error && <p className="err">{(start.error as Error).message}</p>}
      {e?.status === "failed" && <p className="err">{e.error}</p>}
      {e?.status === "completed" && e.url && (
        <div className="space-y-2 text-sm"><a href={e.url} download className="btn w-fit"><Download size={14} />Download ZIP</a>
          <p className="text-xs text-zinc-400">{e.file_count} files · {(e.size_bytes / 1024 / 1024).toFixed(2)} MB</p>
          {!!e.manifest?.skipped?.length && <p className="text-xs text-yellow-400">Left out ({e.manifest.skipped.length}): {e.manifest.skipped.map(x => `${x.width}×${x.height}`).join(", ")} — failed the quality gate.</p>}
          {warnings.length > 0 && <ul className="list-disc space-y-1 pl-5 text-xs text-yellow-400">{warnings.slice(0, 6).map((w, i) => <li key={i}>{w.file}: {w.warnings.join("; ")}</li>)}</ul>}</div>)}
    </div>
  );
}
