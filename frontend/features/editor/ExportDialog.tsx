"use client";
import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useEditor } from "@/stores/editor";
import { Modal } from "./controls";
import type { PreflightWarning, RenderJobOut } from "./useEditorApi";

type Quality = { assessed: boolean; score?: number; status?: string; stale?: boolean; findings?: { severity: string; message: string }[] };

/** mode "preview" renders the saved document through the backend renderer (exactly what export produces); mode "export" adds preflight + format choice. */
export default function ExportDialog({ documentId, creativeId, mode, onClose }: { documentId: string; creativeId: string; mode: "preview" | "export"; onClose: () => void }) {
  const [warn, setWarn] = useState<PreflightWarning[] | null>(null); const [fmt, setFmt] = useState<"png" | "jpeg" | "webp">("png"); const [q, setQ] = useState(92);
  const [job, setJob] = useState<RenderJobOut | null>(null); const [err, setErr] = useState<string | null>(null); const [qual, setQual] = useState<Quality | null>(null); const [ack, setAck] = useState(false); const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null); const store = useEditor;
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  useEffect(() => { void (async () => { try { await store.getState().saveNow(); if (store.getState().saveStatus !== "saved") throw new Error("Your latest changes aren't saved yet."); const r = await api<{ warnings: PreflightWarning[] }>(`/creative-documents/${documentId}/preflight`); setWarn(r.warnings); if (mode === "preview") await render("png"); void qualityInfo(); } catch (e) { setErr((e as Error).message); } })(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const qualityInfo = async () => { try { const r = await api<Quality & { stale?: boolean }>(`/creatives/${creativeId}/quality`); setQual(r); } catch { /* quality is optional */ } };
  const runQuality = async () => { try { const r = await api<Quality>(`/creatives/${creativeId}/quality`, { method: "POST", json: { auto_correct: false } }); setQual(r); } catch (e) { setErr((e as Error).message); } };
  const render = async (f: string) => {
    setBusy(true); setErr(null);
    try { const j = await api<RenderJobOut>(`/creative-documents/${documentId}/render`, { method: "POST", json: { output_format: f, quality: q } }); setJob(j); const poll = async (id: string) => { const x = await api<RenderJobOut>(`/render-jobs/${id}`); setJob(x); if (x.status === "pending" || x.status === "processing") timer.current = setTimeout(() => poll(id), 700); else setBusy(false); }; if (j.status === "completed" || j.status === "failed") { setBusy(false); } else await poll(j.id); }
    catch (e) { setErr((e as Error).message); setBusy(false); }
  };
  const blocking = (warn ?? []).filter(w => w.severity === "error"); const soft = (warn ?? []).filter(w => w.severity !== "error");
  return (
    <Modal title={mode === "preview" ? "Preview" : "Export banner"} onClose={onClose} wide={mode === "preview"}>
      {!warn && !err && <p className="flex items-center gap-2 text-sm text-zinc-400"><Loader2 className="animate-spin" size={14} />Saving and checking…</p>}
      {err && <p className="err" role="alert">{err}</p>}
      {warn && (blocking.length > 0 || soft.length > 0) && (
        <ul className="mb-3 space-y-1" aria-label="Pre-export warnings">{[...blocking, ...soft].map((w, i) => <li key={i} className={`rounded-md border px-2 py-1 text-xs ${w.severity === "error" ? "border-red-500/50 text-red-300" : w.severity === "warning" ? "border-amber-500/40 text-amber-200" : "border-line text-zinc-400"}`}>{w.severity === "error" ? "Must fix: " : w.severity === "warning" ? "Check: " : "Note: "}{w.message}{w.layer_id && <button className="ml-2 underline" onClick={() => { store.getState().select([w.layer_id!]); onClose(); }}>Select layer</button>}</li>)}</ul>)}
      {warn && !warn.length && <p className="mb-3 text-xs text-emerald-400">No problems found before export.</p>}
      {qual && qual.assessed && <p className="mb-3 text-xs text-zinc-300">Quality check: <b>{qual.status}</b> ({qual.score}/100){qual.stale ? " — out of date since your edits" : ""}. <button className="underline" onClick={runQuality}>Re-run</button></p>}
      {qual && !qual.assessed && <p className="mb-3 text-xs text-zinc-400">Quality not assessed yet. <button className="underline" onClick={runQuality}>Run quality check</button></p>}
      {mode === "export" && warn && <>
        <div className="mb-3 flex items-center gap-3 text-sm"><label className="flex items-center gap-2">Format<select aria-label="Export format" className="input !w-28 !py-1" value={fmt} onChange={e => setFmt(e.target.value as typeof fmt)}><option value="png">PNG</option><option value="jpeg">JPEG</option><option value="webp">WebP</option></select></label>
          {fmt !== "png" && <label className="flex items-center gap-2">Quality<input aria-label="Quality" type="range" min={40} max={100} value={q} onChange={e => setQ(Number(e.target.value))} />{q}</label>}</div>
        {soft.some(w => w.severity === "warning") && <label className="mb-3 flex items-center gap-2 text-xs text-amber-200"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />I’ve seen the warnings and want to export anyway</label>}
        <button className="btn" disabled={busy || blocking.length > 0 || (soft.some(w => w.severity === "warning") && !ack)} onClick={() => render(fmt)}>{busy ? <Loader2 className="animate-spin" size={14} /> : null}Export {fmt.toUpperCase()}</button></>}
      {job?.status === "failed" && <p className="err" role="alert">Render failed: {job.error_message}</p>}
      {job?.status === "completed" && job.url && <div className="mt-4 space-y-2">
        {/* eslint-disable-next-line @next/next/no-img-element */}<img src={job.url} alt="Rendered banner" className="mx-auto max-h-[56vh] rounded-lg border border-line" />
        <p className="text-center text-xs text-zinc-400">{job.width}×{job.height}px · {job.output_format ?? fmt} · revision {job.revision}{job.warnings?.length ? ` · ${job.warnings.join("; ")}` : ""}</p>
        {mode === "export" && <div className="text-center"><a className="btn" href={job.url} download={`banner_${job.width}x${job.height}.${fmt === "jpeg" ? "jpg" : fmt}`}>Download</a></div>}</div>}
    </Modal>
  );
}
