"use client";
import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { cmd } from "@/lib/editor/commands";
import { BG, type DocView } from "@/lib/editor/types";
import { useEditor } from "@/stores/editor";
import { Modal } from "./controls";
import type { RegenJob } from "./useEditorApi";

const HINT: Record<string, string> = { background: "Describe the scene you want. Your product, logo, text and button stay exactly as they are.", product: "Your product photo is never redrawn by AI. You'll get cleaner versions of your own original photo.",
  image: "Describe the image you want in place of this one. Nothing else on the banner changes.", shape: "Describe a decorative element. It replaces only this shape.", text: "Say how you'd like this text reworded. Your current text stays until you accept an alternative." };
const TERMINAL = new Set(["ready", "failed", "rejected", "accepted", "cancelled"]);

/** Selected-element-only AI edit. Candidates are previewed on the canvas but nothing is saved until the user clicks Accept. */
export default function AiEditDialog({ layerId, documentId, onClose }: { layerId: string; documentId: string; onClose: () => void }) {
  const st = useEditor; const doc = useEditor(s => s.doc)!;
  const layer = layerId === BG ? null : doc.elements.find(e => e.id === layerId);
  const kind = layerId === BG ? "background" : !layer ? "?" : layer.type === "text" || layer.type === "button" ? "text" : layer.type === "shape" ? "shape" : layer.role === "product" ? "product" : "image";
  const [prompt, setPrompt] = useState(kind === "product" ? "Use my original photo" : ""); const [aiProduct, setAiProduct] = useState(false);
  const [job, setJob] = useState<RegenJob | null>(null); const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [picked, setPicked] = useState(0); const [needAck, setNeedAck] = useState(false); const [previewOn, setPreviewOn] = useState(true);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = (id: string) => { timer.current = setTimeout(async () => { try { const j = await api<RegenJob>(`/regeneration-jobs/${id}`); setJob(j); if (!TERMINAL.has(j.status)) poll(id); } catch (e) { setErr((e as Error).message); } }, 900); };
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); st.getState().setPreview(null); }, [st]);
  // show the chosen candidate on the canvas (never persisted)
  useEffect(() => {
    const c = job?.status === "ready" ? job.candidates[picked] : undefined; if (!c || !previewOn) { st.getState().setPreview(null); return; }
    if (c.asset_id && c.url) st.getState().assetsPut({ id: c.asset_id, url: c.url, width: 1, height: 1 });
    st.getState().setPreview(cmd("REGENERATE_ELEMENT_ACCEPTED", c.asset_id ? { layer_id: layerId, asset_id: c.asset_id } : { layer_id: layerId, text: c.text }));
  }, [job, picked, previewOn, layerId, st]);

  const start = async () => {
    setBusy(true); setErr(null);
    try {
      await st.getState().saveNow();                                                               // the job must be based on the saved design
      const s = st.getState(); if (s.saveStatus !== "saved") throw new Error("Save your changes first (see the save status above).");
      const j = await api<RegenJob>(`/creative-documents/${documentId}/regenerate`, { method: "POST", json: { layer_id: layerId, prompt, expected_revision: s.saver!.serverRevision, candidates: kind === "text" ? 3 : 2, allow_ai_product: aiProduct } });
      setJob(j); poll(j.id);
    } catch (e) { setErr(e instanceof ApiError ? e.message : (e as Error).message); } finally { setBusy(false); }
  };
  const accept = async (acknowledge = false) => {
    if (!job) return; setBusy(true); setErr(null);
    try {
      st.getState().setPreview(null);
      const r = await api<{ document: DocView }>(`/regeneration-jobs/${job.id}/accept`, { method: "POST", json: { candidate_index: picked, acknowledge_changes: acknowledge } });
      st.getState().adoptServer(r.document, "AI edit"); onClose();
    } catch (e) { if (e instanceof ApiError && e.code === "target_changed") setNeedAck(true); setErr((e as Error).message); setPreviewOn(true); } finally { setBusy(false); }
  };
  const reject = async () => { st.getState().setPreview(null); if (job && !["rejected", "accepted", "failed"].includes(job.status)) { try { await api(`/regeneration-jobs/${job.id}/reject`, { method: "POST" }); } catch { /* already closed */ } } onClose(); };
  const running = job && !TERMINAL.has(job.status);

  return (
    <Modal title={`AI edit · ${layerId === BG ? "Background" : layer?.name || layer?.role || "layer"}`} onClose={reject} wide>
      <p className="mb-3 text-sm text-zinc-300">{HINT[kind] ?? ""}</p>
      {!job && <>
        <label className="label" htmlFor="ai-prompt">Instruction</label>
        <textarea id="ai-prompt" className="input min-h-[80px]" maxLength={500} value={prompt} onChange={e => setPrompt(e.target.value)} placeholder={kind === "text" ? "e.g. shorter and friendlier" : "e.g. a bright kitchen counter with morning light"} />
        {kind === "product" && <label className="mt-3 flex items-start gap-2 text-xs text-amber-300"><input type="checkbox" checked={aiProduct} onChange={e => setAiProduct(e.target.checked)} className="mt-0.5" /><span>Let AI create a <b>different</b> product image instead of using my photo. It may not match your real product.</span></label>}
        <div className="mt-4 flex justify-end gap-2"><button className="rounded-lg border border-line px-4 py-2 text-sm" onClick={onClose}>Cancel</button><button className="btn" disabled={busy || prompt.trim().length < 3} onClick={start}>{busy ? <Loader2 size={14} className="animate-spin" /> : null}Generate options</button></div></>}
      {running && <p className="flex items-center gap-2 text-sm text-zinc-300" role="status"><Loader2 size={16} className="animate-spin" />Generating… your banner is untouched until you accept an option.</p>}
      {job?.status === "failed" && <p className="err" role="alert">Couldn’t generate: {job.error_message}. Your banner was not changed.</p>}
      {job?.status === "ready" && <>
        {job.stale && <p className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-200" role="status">The design changed while this was generating. {job.target_changed ? "This layer itself was edited, so you’ll be asked to confirm." : "Only other layers changed; they will be kept."} Review the preview on the current design.</p>}
        <div className="grid gap-3 sm:grid-cols-3" role="radiogroup" aria-label="Candidates">
          {job.candidates.map(c => (
            <button key={c.index} role="radio" aria-checked={picked === c.index} onClick={() => setPicked(c.index)} className={`rounded-xl border p-2 text-left ${picked === c.index ? "border-accent bg-accent/10" : "border-line hover:border-zinc-500"}`}>
              {c.url ? /* eslint-disable-next-line @next/next/no-img-element */ <img src={c.url} alt={`Option ${c.index + 1}`} className="aspect-video w-full rounded-md bg-black/30 object-contain" /> : <p className="min-h-[64px] text-sm">“{c.text}”</p>}
              <span className="mt-1 block text-xs text-zinc-400">Option {c.index + 1}{c.label ? ` · ${c.label}` : ""}</span></button>))}
        </div>
        <label className="mt-3 flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" checked={previewOn} onChange={e => setPreviewOn(e.target.checked)} />Preview on the banner (not saved)</label>
        {err && <p className="err" role="alert">{err}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button className="rounded-lg border border-line px-4 py-2 text-sm" onClick={reject}>Reject all</button>
          {needAck ? <button className="btn" disabled={busy} onClick={() => accept(true)}>I’ve reviewed it — apply anyway</button> : <button className="btn" disabled={busy} onClick={() => accept(false)}>Accept option {picked + 1}</button>}
        </div></>}
      {(err && job?.status !== "ready") && <p className="err" role="alert">{err}</p>}
      {job && ["failed", "cancelled"].includes(job.status) && <div className="mt-3 flex justify-end"><button className="btn" onClick={() => { setJob(null); setErr(null); }}>Try again</button></div>}
    </Modal>
  );
}
