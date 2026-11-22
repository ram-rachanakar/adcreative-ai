import { vi } from "vitest";
import { apply, patchCommand } from "./commands";
import { mergePatches } from "./patches";
import type { Transport } from "./autosave";
import type { Patch } from "./types";
import { makeDoc, makeView } from "./fixtures";
import { useEditor } from "@/stores/editor";

/** A tiny in-memory server that really applies commands (same engine), keeps a revision and per-batch undo units. */
export function makeTransport(over: Partial<Transport> = {}, startDoc = makeDoc()) {
  let rev = 0; let d = startDoc; const sent: { batch: string; expected: number }[] = [];
  const undo: { before: Patch; after: Patch }[] = []; const redo: { before: Patch; after: Patch }[] = [];
  const view = () => makeView("doc1", rev, d);
  const t: Transport = {
    async commands(b) {
      sent.push({ batch: b.batch_id, expected: b.expected_revision });
      let merged: { before: Patch; after: Patch } | null = null; let nd = d;
      for (const c of b.commands) { const r = apply(nd, c); nd = r.doc; merged = merged ? mergePatches(merged, r) : r; }
      d = nd; rev = b.expected_revision + b.commands.length; if (merged) { undo.push(merged); redo.length = 0; } return { document: view() };
    },
    async undo() { const u = undo.pop()!; d = apply(d, patchCommand(u.before)).doc; redo.push(u); rev += 1; return { document: view() }; },
    async redo() { const u = redo.pop()!; d = apply(d, patchCommand(u.after)).doc; undo.push(u); rev += 1; return { document: view() }; }, ...over,
  };
  return { t, sent };
}

export function boot(rev = 0, tr = makeTransport()) { useEditor.getState().load(makeView("doc1", rev), tr.t, { debounceMs: 50 }); return tr; }
void vi;
