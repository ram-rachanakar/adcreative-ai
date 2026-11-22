"use client";
import Link from "next/link";
import { AlertTriangle, ArrowLeft, Check, Cloud, Copy, CloudOff, Download, Eye, History, Keyboard, LayoutTemplate, Loader2, Maximize, Redo2, Undo2, ZoomIn, ZoomOut } from "lucide-react";
import clsx from "clsx";
import { useEditor } from "@/stores/editor";
import { IconBtn, Segmented } from "./controls";
import type { Creative } from "@/types";

export function SaveStatus() {
  const { saveStatus, pending, saveError } = useEditor();
  const map = {
    saved: { t: "Saved", c: "text-emerald-400", i: <Check size={14} /> }, unsaved: { t: `Unsaved changes${pending ? ` (${pending})` : ""}`, c: "text-amber-300", i: <Cloud size={14} /> }, saving: { t: "Saving…", c: "text-zinc-300", i: <Loader2 size={14} className="animate-spin" /> },
    failed: { t: "Save failed", c: "text-red-400", i: <AlertTriangle size={14} /> }, offline: { t: "Offline — changes pending", c: "text-amber-300", i: <CloudOff size={14} /> }, conflict: { t: "Conflict", c: "text-red-400", i: <AlertTriangle size={14} /> },
  }[saveStatus];
  return (
    <span role="status" aria-live="polite" data-testid="save-status" data-state={saveStatus} className={clsx("inline-flex items-center gap-1.5 text-xs", map.c)} title={saveError}>
      {map.i}{map.t}{saveStatus === "failed" && <button className="ml-1 rounded border border-red-400/50 px-2 py-0.5 text-red-300 hover:bg-red-500/10" onClick={() => void useEditor.getState().retrySave()}>Retry</button>}
    </span>
  );
}

export default function TopBar({ campaignId, creativeId, creatives, conceptName, onFormat, onPreview, onExport, onHistory, onKeys, onDuplicate, onSaveTemplate }: {
  campaignId: string; creativeId: string; creatives: Creative[]; conceptName: (id: string) => string; onFormat: (creativeId: string) => void; onPreview: () => void; onExport: () => void; onHistory: () => void; onKeys: () => void; onSaveTemplate: () => void; onDuplicate: () => void }) {
  const s = useEditor(); const view = s.view!; const zoom = s.fit ? s.stage.zoom : s.zoom;
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-panel px-3" aria-label="Editor toolbar">
      <Link href={`/campaigns/${campaignId}/creatives`} aria-label="Back to banners" className="text-zinc-400 hover:text-white"><ArrowLeft size={18} /></Link>
      <div className="min-w-0"><div className="truncate text-sm font-medium" data-testid="doc-name">{view.name}</div><div className="text-[11px] text-zinc-400" data-testid="doc-size">{view.canvas.width} × {view.canvas.height}px</div></div>
      <select aria-label="Banner format" className="input !w-52 !py-1 text-xs" value={creativeId} onChange={e => onFormat(e.target.value)}>
        {creatives.map(c => <option key={c.id} value={c.id}>{c.width}×{c.height} · {conceptName(c.concept_id)}</option>)}</select>
      <Segmented label="Edit mode" value={s.mode} options={[{ v: "quick", label: "Quick Edit" }, { v: "advanced", label: "Advanced Edit" }]} onChange={s.setMode} />
      <div className="ml-2 flex gap-1"><IconBtn label="Undo" onClick={s.undo} disabled={!s.past.length || s.readOnly || !!s.conflict}><Undo2 size={15} /></IconBtn><IconBtn label="Redo" onClick={s.redo} disabled={!s.future.length || s.readOnly || !!s.conflict}><Redo2 size={15} /></IconBtn></div>
      <div className="flex items-center gap-1"><IconBtn label="Zoom out" onClick={() => s.setZoom(zoom / 1.2)}><ZoomOut size={15} /></IconBtn><span className="w-12 text-center text-xs tabular-nums" data-testid="zoom-label">{Math.round(zoom * 100)}%</span><IconBtn label="Zoom in" onClick={() => s.setZoom(zoom * 1.2)}><ZoomIn size={15} /></IconBtn><IconBtn label="Fit to canvas" active={s.fit} onClick={() => s.setZoom(zoom, true)}><Maximize size={15} /></IconBtn></div>
      <label className="flex items-center gap-1 text-xs text-zinc-400"><input type="checkbox" checked={s.snapGrid} onChange={() => s.toggle("snapGrid")} />Snap to grid</label>
      <label className="flex items-center gap-1 text-xs text-zinc-400"><input type="checkbox" checked={s.showGrid} onChange={() => s.toggle("showGrid")} />Grid</label>
      <label className="flex items-center gap-1 text-xs text-zinc-400"><input type="checkbox" checked={s.showSafe} onChange={() => s.toggle("showSafe")} />Safe area</label>
      <div className="ml-auto flex items-center gap-3"><SaveStatus />
        <IconBtn label="Keyboard shortcuts" onClick={onKeys}><Keyboard size={15} /></IconBtn>
        <button className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40" disabled={s.readOnly} onClick={onDuplicate}><Copy size={14} />Copy to size</button>
        <button className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40" disabled={s.readOnly} onClick={onSaveTemplate}><LayoutTemplate size={14} />Save as template</button>
        <button className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent" onClick={onHistory}><History size={14} />History</button>
        <button className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent" onClick={onPreview}><Eye size={14} />Preview</button>
        <button className="btn !py-1.5 text-xs" onClick={onExport}><Download size={14} />Export</button></div>
    </header>
  );
}
