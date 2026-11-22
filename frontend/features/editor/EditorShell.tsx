"use client";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cmd } from "@/lib/editor/commands";
import { handleShortcut, SHORTCUTS } from "@/lib/editor/shortcuts";
import { loadFonts } from "@/lib/editor/textLayout";
import { useEditor } from "@/stores/editor";
import type { Concept, Creative } from "@/types";
import AiEditDialog from "./AiEditDialog";
import DuplicateDialog from "./DuplicateDialog";
import ExportDialog from "./ExportDialog";
import LeftPanel from "./LeftPanel";
import PropertiesPanel from "./PropertiesPanel";
import QuickEdit from "./QuickEdit";
import SaveAsTemplateDialog from "./templates/SaveAsTemplateDialog";
import TopBar from "./TopBar";
import VersionPanel from "./VersionPanel";
import { Modal } from "./controls";

const CanvasStage = dynamic(() => import("./CanvasStage"), { ssr: false, loading: () => <div className="grid h-full place-items-center text-sm text-zinc-500">Loading canvas…</div> });
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function ConflictBanner() {
  const c = useEditor(s => s.conflict); const resolve = useEditor(s => s.resolveConflict);
  if (!c) return null;
  return (
    <div role="alert" className="flex items-center gap-3 border-b border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-200" data-testid="conflict-banner">
      <span className="flex-1">This banner was changed somewhere else (another tab or teammate). Your {c.pending.length} unsaved change{c.pending.length === 1 ? " is" : "s are"} still here and nothing was overwritten.</span>
      <button className="btn !py-1 text-xs" onClick={() => resolve("reapply")}>Reapply my changes on the latest version</button>
      <button className="rounded-lg border border-red-400/50 px-3 py-1 text-xs hover:bg-red-500/10" onClick={() => resolve("reload")}>Discard mine, load latest</button>
    </div>
  );
}

function Notice() {
  const n = useEditor(s => s.notice); const dismiss = useEditor(s => s.dismissNotice);
  useEffect(() => { if (!n) return; const t = setTimeout(dismiss, 6000); return () => clearTimeout(t); }, [n, dismiss]);
  return n ? <div role="status" className="absolute bottom-4 left-1/2 z-40 -translate-x-1/2 rounded-lg border border-line bg-panel px-4 py-2 text-sm shadow-xl">{n} <button className="ml-2 text-zinc-400 hover:text-white" aria-label="Dismiss" onClick={dismiss}>✕</button></div> : null;
}

function ContextMenu({ at, onClose, onAi }: { at: { id: string | null; x: number; y: number }; onClose: () => void; onAi: (id: string) => void }) {
  const st = useEditor(); const l = st.doc?.elements.find(e => e.id === at.id);
  useEffect(() => { const c = () => onClose(); window.addEventListener("click", c); window.addEventListener("keydown", c); return () => { window.removeEventListener("click", c); window.removeEventListener("keydown", c); }; }, [onClose]);
  const d = (t: Parameters<typeof cmd>[0], p: Record<string, unknown> = {}) => st.dispatch(cmd(t, { layer_id: at.id, ...p }));
  const item = (label: string, fn: () => void, off = false) => <button key={label} role="menuitem" disabled={off || st.readOnly} onClick={() => { fn(); onClose(); }} className="block w-full px-3 py-1.5 text-left text-xs hover:bg-accent/20 disabled:opacity-40">{label}</button>;
  return (
    <div role="menu" aria-label="Layer menu" className="fixed z-50 min-w-44 overflow-hidden rounded-lg border border-line bg-panel py-1 shadow-xl" style={{ left: at.x, top: at.y }}>
      {l ? <>{item("Duplicate", () => d("DUPLICATE_ELEMENT"))}{item("Copy", () => st.copy())}{item("Delete", () => d("DELETE_ELEMENT"), l.locked)}{item("Bring to front", () => d("REORDER_LAYER", { direction: "front" }), l.locked)}{item("Send to back", () => d("REORDER_LAYER", { direction: "back" }), l.locked)}
        {item(l.locked ? "Unlock" : "Lock", () => d("TOGGLE_LOCK"))}{item(l.visible ? "Hide" : "Show", () => d("TOGGLE_VISIBILITY"))}{item("AI edit…", () => onAi(l.id), l.locked || (l.type === "image" && l.role === "logo"))}</>
        : <>{item("Paste", () => st.paste(), !st.clipboard)}{item("AI edit background…", () => onAi("background"))}</>}
    </div>
  );
}

function InlineTextEditor({ id, onClose }: { id: string; onClose: () => void }) {
  const st = useEditor(); const l = st.doc!.elements.find(e => e.id === id); const box = document.querySelector("[data-testid=canvas-container]")?.getBoundingClientRect();
  if (!l || (l.type !== "text" && l.type !== "button") || !box) return null; const z = st.stage.zoom; const val = l.type === "text" ? l.text : l.label;
  return (
    <textarea autoFocus aria-label="Edit text on canvas" value={val} maxLength={l.type === "text" ? 300 : 30}
      onChange={e => st.dispatch(cmd("UPDATE_TEXT", { layer_id: id, text: e.target.value.replace(l.type === "button" ? /\n/g : /$^/, " ") }), { coalesceKey: `text:${id}` })}
      onBlur={onClose} onKeyDown={e => { if (e.key === "Escape" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); onClose(); } }}
      className="fixed z-40 resize-none overflow-hidden border-2 border-accent bg-white/90 p-0 text-black outline-none"
      style={{ left: box.left + st.stage.x + l.x * z, top: box.top + st.stage.y + l.y * z, width: l.w * z, height: l.h * z, fontSize: l.font_size * z, fontFamily: l.font_family, fontWeight: l.font_weight, lineHeight: l.type === "text" ? l.line_height : 1, textAlign: l.type === "text" ? l.align : "center", transform: `rotate(${l.rotation}deg)` }} />
  );
}

export default function EditorShell({ campaignId, creativeId }: { campaignId: string; creativeId: string }) {
  const router = useRouter(); const view = useEditor(s => s.view); const mode = useEditor(s => s.mode); const readOnly = useEditor(s => s.readOnly); const status = useEditor(s => s.saveStatus);
  const [dlg, setDlg] = useState<null | "history" | "preview" | "export" | "keys" | "duplicate" | "saveTemplate">(null); const [ai, setAi] = useState<string | null>(null); const [menu, setMenu] = useState<{ id: string | null; x: number; y: number } | null>(null); const [editing, setEditing] = useState<string | null>(null); const [fontsReady, setFontsReady] = useState(false);
  const list = useQuery({ queryKey: ["creatives", campaignId], queryFn: () => api<Creative[]>(`/campaigns/${campaignId}/creatives`) });
  const concepts = useQuery({ queryKey: ["concepts", campaignId], queryFn: () => api<Concept[]>(`/campaigns/${campaignId}/concepts`) });
  const names = useMemo(() => new Map((concepts.data ?? []).map(c => [c.id, c.data.name])), [concepts.data]);

  useEffect(() => { if (view) loadFonts(view.fonts, API).then(() => setFontsReady(true)).catch(() => setFontsReady(true)); }, [view?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const h = (e: KeyboardEvent) => { void handleShortcut(e, () => void useEditor.getState().saveNow()); }; window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h); }, []);
  useEffect(() => {                                                                            // never silently lose edits when leaving
    const warn = (e: BeforeUnloadEvent) => { const s = useEditor.getState(); if (s.pending > 0 || s.saveStatus === "failed" || s.saveStatus === "conflict") { e.preventDefault(); e.returnValue = ""; } };
    const hide = () => { if (document.visibilityState === "hidden") void useEditor.getState().saveNow(); };
    const online = () => void useEditor.getState().saver?.flush();
    window.addEventListener("beforeunload", warn); document.addEventListener("visibilitychange", hide); window.addEventListener("online", online);
    return () => { window.removeEventListener("beforeunload", warn); document.removeEventListener("visibilitychange", hide); window.removeEventListener("online", online); };
  }, []);
  useEffect(() => { if (!view) return; const id = view.id; return () => { void useEditor.getState().saveNow(); void id; }; }, [view?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!view) return null;
  const switchFormat = async (id: string) => { await useEditor.getState().saveNow(); if (useEditor.getState().saveStatus === "saved") router.push(`/campaigns/${campaignId}/editor/${id}`); else useEditor.setState({ notice: "Resolve the save problem before switching formats, so no edits are lost." }); };
  return (
    <div className="flex h-screen flex-col bg-ink" data-testid="editor-root" data-mode={mode}>
      <TopBar campaignId={campaignId} creativeId={creativeId} creatives={list.data ?? []} conceptName={id => names.get(id) ?? "Concept"} onFormat={switchFormat} onPreview={() => setDlg("preview")} onExport={() => setDlg("export")} onHistory={() => setDlg("history")} onKeys={() => setDlg("keys")} onSaveTemplate={() => setDlg("saveTemplate")} onDuplicate={() => setDlg("duplicate")} />
      <ConflictBanner />
      {readOnly && <div className="bg-zinc-800 px-4 py-1.5 text-center text-xs text-zinc-300">You have view-only access to this banner.</div>}
      <div className="relative flex min-h-0 flex-1">
        <LeftPanel campaignId={campaignId} />
        <main className="relative min-w-0 flex-1"><CanvasStage fontsReady={fontsReady} onContextMenu={(id, x, y) => setMenu({ id, x, y })} onEditText={setEditing} /><Notice /></main>
        <aside className="w-80 shrink-0 overflow-y-auto border-l border-line bg-panel" aria-label="Properties panel">
          {mode === "quick" ? <QuickEdit campaignId={campaignId} onAi={setAi} /> : <PropertiesPanel campaignId={campaignId} onAi={setAi} />}
        </aside>
      </div>
      {editing && <InlineTextEditor id={editing} onClose={() => setEditing(null)} />}
      {menu && <ContextMenu at={menu} onClose={() => setMenu(null)} onAi={setAi} />}
      {ai && <AiEditDialog layerId={ai} documentId={view.id} onClose={() => setAi(null)} />}
      {dlg === "duplicate" && <DuplicateDialog documentId={view.id} campaignId={campaignId} onClose={() => setDlg(null)} />}
      {dlg === "saveTemplate" && <SaveAsTemplateDialog documentId={view.id} onClose={() => setDlg(null)} />}
      {dlg === "history" && <VersionPanel documentId={view.id} onClose={() => setDlg(null)} />}
      {(dlg === "preview" || dlg === "export") && <ExportDialog documentId={view.id} creativeId={view.creative_id} mode={dlg} onClose={() => setDlg(null)} />}
      {dlg === "keys" && <Modal title="Keyboard shortcuts" onClose={() => setDlg(null)}><table className="w-full text-sm"><tbody>{SHORTCUTS.map(s => <tr key={s.keys} className="border-b border-line"><td className="py-1.5 pr-3 font-mono text-xs text-accent2">{s.keys}</td><td className="py-1.5 text-zinc-300">{s.action}</td></tr>)}</tbody></table></Modal>}
      <span className="sr-only" data-testid="status-live">{status}</span>
    </div>
  );
}
