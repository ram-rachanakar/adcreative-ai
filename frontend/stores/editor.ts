"use client";
import { create } from "zustand";
import { apply, batchId, CommandError, patchCommand } from "@/lib/editor/commands";
import { Autosaver, type QueueItem, type SaveStatus, type Transport } from "@/lib/editor/autosave";
import { diffPatch, mergePatches } from "@/lib/editor/patches";
import { BG, toDoc, type AssetInfo, type Command, type Doc, type DocView, type Layer, type Patch } from "@/lib/editor/types";
import type { Guide } from "@/lib/editor/geometry";

export interface HistoryEntry { before: Patch; after: Patch; label: string; key?: string; t: number }
export const HISTORY_LIMIT = 200;   // memory bound: oldest entries are dropped; server version checkpoints remain the long-term safety net
const COALESCE_MS = 1000;

export interface EditorState {
  view: DocView | null; doc: Doc | null; assets: Record<string, AssetInfo>;
  selection: string[]; mode: "quick" | "advanced"; zoom: number; fit: boolean; pan: { x: number; y: number };
  past: HistoryEntry[]; future: HistoryEntry[]; saveStatus: SaveStatus; pending: number; saveError?: string;
  conflict: { latest: DocView | null; pending: QueueItem[] } | null; notice: string | null;
  preview: Command | null;   // AI candidate shown on the canvas WITHOUT touching the document
  stage: { x: number; y: number; zoom: number };   // where the artboard sits on screen (set by the canvas; used by overlays)
  clipboard: Layer | null; guides: Guide[]; snapGrid: boolean; gridSize: number; showSafe: boolean; showGrid: boolean; readOnly: boolean;
  saver: Autosaver | null;
  load(view: DocView, transport: Transport, opts?: { readOnly?: boolean; debounceMs?: number; isOnline?: () => boolean; recover?: QueueItem[] }): void;
  dispatch(c: Command, o?: { coalesceKey?: string; label?: string }): boolean;
  dispatchBatch(cs: Command[], label?: string, coalesceKey?: string): boolean;
  undo(): void; redo(): void;
  select(ids: string[], additive?: boolean): void; clearSelection(): void;
  setMode(m: "quick" | "advanced"): void; setZoom(z: number, fit?: boolean): void; setPan(p: { x: number; y: number }): void;
  setGuides(g: Guide[]): void; toggle(k: "snapGrid" | "showSafe" | "showGrid"): void;
  copy(): void; paste(): void;
  adoptServer(view: DocView, label?: string): void;       // server-side change already persisted (AI accept, restore): becomes one undoable step
  resolveConflict(how: "reload" | "reapply"): void;
  setPreview(c: Command | null): void;
  assetsPut(a: { id: string; url: string; width: number; height: number; mime?: string }): void;
  dismissNotice(): void; saveNow(): Promise<void>; retrySave(): Promise<void>; reset(): void;
}

const initial = { view: null, doc: null, assets: {}, selection: [], mode: "quick" as const, zoom: 1, fit: true, pan: { x: 0, y: 0 }, past: [], future: [], saveStatus: "saved" as SaveStatus, pending: 0, saveError: undefined,
  conflict: null, notice: null, preview: null, stage: { x: 0, y: 0, zoom: 1 }, clipboard: null, guides: [], snapGrid: false, gridSize: 10, showSafe: true, showGrid: false, readOnly: false, saver: null };

const prune = (sel: string[], doc: Doc) => sel.filter(id => id === BG || doc.elements.some(e => e.id === id));

export const useEditor = create<EditorState>((set, get) => ({
  ...initial,

  load(view, transport, opts = {}) {
    get().saver?.dispose();
    const saver = new Autosaver({
      transport, debounceMs: opts.debounceMs, isOnline: opts.isOnline,
      onStatus: (s, i) => set({ saveStatus: s, pending: i.pending, saveError: i.error }),
      onSaved: (srv, empty) => {
        set(st => ({ view: st.view ? { ...st.view, revision: srv.revision, assets: { ...st.view.assets, ...srv.assets }, rendered_revision: srv.rendered_revision } : srv, assets: { ...st.assets, ...srv.assets } }));
        if (empty) {                                           // safety net: the server's authoritative result must equal what the user sees
          const local = get().doc; const remote = toDoc(srv);
          if (local && JSON.stringify(local) !== JSON.stringify(remote)) set({ doc: remote, notice: "Your design was synchronised with the saved version." });
        }
      },
      onConflict: (latest, pend) => set({ conflict: { latest, pending: pend } }),
      onRejected: (_item, err) => {                           // the server refused this change: never keep showing something that is not saved
        set({ notice: `A change could not be saved (${err.message}). Reloading the last saved design.` });
        void transport.reload?.().then(v => { const st = get(); set({ doc: toDoc(v), view: v, assets: { ...st.assets, ...v.assets }, past: [], future: [], selection: prune(st.selection, toDoc(v)) }); st.saver?.resume(v.revision, []); });
      },
      onQueueChange: (q, rev) => { try { const k = `editor-pending:${get().view?.id}`; if (q.length) localStorage.setItem(k, JSON.stringify({ base: rev, items: q })); else localStorage.removeItem(k); } catch { /* storage unavailable */ } },
    }, view.revision);
    set({ ...initial, view, doc: toDoc(view), assets: view.assets, saver, readOnly: !!opts.readOnly, mode: get().mode, showSafe: get().showSafe, snapGrid: get().snapGrid });
    if (opts.recover?.length) {                                // replay unsaved work from a previous session on top of the loaded revision
      let doc = toDoc(view); const ok: QueueItem[] = [];
      for (const it of opts.recover) if (it.kind === "commands") { try { for (const c of it.commands) doc = apply(doc, c).doc; ok.push(it); } catch { /* stale command: skip */ } }
      set({ doc, notice: ok.length ? `Recovered ${ok.length} unsaved change${ok.length > 1 ? "s" : ""} from your last session.` : null }); ok.forEach(i => saver.enqueue(i));
    }
  },

  dispatch(c, o = {}) { return get().dispatchBatch([c], o.label ?? c.type, o.coalesceKey); },
  dispatchBatch(cs, label = cs[0]?.type ?? "edit", coalesceKey?: string) {
    const st = get(); if (!st.doc || !st.saver || st.readOnly || st.conflict) return false;
    let doc = st.doc; let merged: { before: Patch; after: Patch } | null = null;
    try {
      for (const c of cs) { const r = apply(doc, c); doc = r.doc; merged = merged ? mergePatches(merged, r) : r; }
    } catch (e) {
      if (e instanceof CommandError) { if (e.code !== "no_change") set({ notice: e.message }); return false; }
      throw e;
    }
    if (!merged) return false;
    const now = Date.now(); const last = st.past[st.past.length - 1];
    const canMerge = !!coalesceKey && last?.key === coalesceKey && now - last.t < COALESCE_MS && cs.length === 1 && st.saver.queue.length > 0 && st.saver.queue[st.saver.queue.length - 1].kind === "commands";
    const item: QueueItem = { kind: "commands", batchId: batchId(), commands: cs };
    let past: HistoryEntry[];
    if (canMerge && st.saver.replaceLast(item)) {            // burst of the same edit (typing, nudging): one history step, one saved command
      const m = mergePatches(last, merged); past = [...st.past.slice(0, -1), { ...m, label, key: coalesceKey, t: now }];
    } else {
      st.saver.enqueue(item); past = [...st.past, { ...merged, label, key: coalesceKey, t: now }].slice(-HISTORY_LIMIT);
    }
    set({ doc, past, future: [], selection: prune(st.selection, doc) });
    return true;
  },

  undo() {
    const st = get(); if (!st.doc || !st.saver || st.readOnly || st.conflict || !st.past.length) return;
    const e = st.past[st.past.length - 1]; const doc = apply(st.doc, patchCommand(e.before)).doc;
    st.saver.enqueue({ kind: "undo" }); set({ doc, past: st.past.slice(0, -1), future: [...st.future, e], selection: prune(st.selection, doc) });
  },
  redo() {
    const st = get(); if (!st.doc || !st.saver || st.readOnly || st.conflict || !st.future.length) return;
    const e = st.future[st.future.length - 1]; const doc = apply(st.doc, patchCommand(e.after)).doc;
    st.saver.enqueue({ kind: "redo" }); set({ doc, future: st.future.slice(0, -1), past: [...st.past, e], selection: prune(st.selection, doc) });
  },

  select(ids, additive) { set(st => ({ selection: additive ? [...new Set([...st.selection, ...ids])] : ids })); },
  clearSelection() { set({ selection: [] }); },
  setMode(mode) { set({ mode }); },
  setZoom(zoom, fit = false) { set({ zoom: Math.min(8, Math.max(0.05, zoom)), fit }); },
  setPan(pan) { set({ pan }); },
  setGuides(guides) { set({ guides }); },
  toggle(k) { set(st => ({ [k]: !st[k] }) as Partial<EditorState>); },
  copy() { const st = get(); const l = st.doc?.elements.find(e => e.id === st.selection[0]); if (l) set({ clipboard: structuredClone(l) }); },
  paste() {
    const st = get(); if (!st.clipboard) return;
    st.dispatch({ type: "ADD_LAYER", payload: { layer: { ...st.clipboard, id: "", x: st.clipboard.x + 16, y: st.clipboard.y + 16, locked: false } } }, { label: "Paste" });
    const doc = get().doc; const added = doc?.elements[doc.elements.length - 1]; if (added) set({ selection: [added.id] });
  },

  adoptServer(view, label = "Server change") {
    const st = get(); if (!st.doc) return;
    const next = toDoc(view); const d = diffPatch(st.doc, next);
    set({ doc: next, view: { ...(st.view ?? view), ...view }, assets: { ...st.assets, ...view.assets }, past: [...st.past, { ...d, label, t: Date.now() }].slice(-HISTORY_LIMIT), future: [], selection: prune(st.selection, next) });
    if (st.saver) st.saver.serverRevision = view.revision;
  },

  resolveConflict(how) {
    const st = get(); const c = st.conflict; if (!c || !st.saver) return;
    const latest = c.latest; if (!latest) { set({ conflict: null }); return; }
    let doc = toDoc(latest); const queue: QueueItem[] = [];
    let skipped = 0;
    if (how === "reapply") {                                // replay my unsent changes on top of the newest saved design; anything that no longer applies is reported, never forced
      for (const it of c.pending) { if (it.kind !== "commands") continue; try { for (const cm of it.commands) doc = apply(doc, cm).doc; queue.push({ ...it, batchId: batchId() }); } catch { skipped++; } }
    }
    set({ doc, view: latest, assets: { ...st.assets, ...latest.assets }, past: [], future: [], conflict: null, selection: prune(st.selection, doc),
      notice: how === "reapply" ? `Reapplied your changes on top of the latest version${skipped ? ` (${skipped} no longer applied)` : ""}.` : "Loaded the latest version; your unsaved changes were discarded." });
    st.saver.resume(latest.revision, queue);
  },

  setPreview(c) { set({ preview: c }); },
  assetsPut(a) { set(st => ({ assets: { ...st.assets, [a.id]: { id: a.id, url: a.url, width: a.width, height: a.height, mime: a.mime } } })); },
  dismissNotice() { set({ notice: null }); },
  async saveNow() { await get().saver?.flush(); },
  async retrySave() { await get().saver?.retry(); },
  reset() { get().saver?.dispose(); set({ ...initial }); },
}));
