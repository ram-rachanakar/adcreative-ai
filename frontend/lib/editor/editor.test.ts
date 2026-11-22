import { beforeEach, describe, expect, it, vi } from "vitest";
import parity from "../../../shared/editor-parity/text-layout.json";
import { apply, CommandError, cmd, patchCommand } from "./commands";
import { mergePatches } from "./patches";
import { fitText } from "./textLayout";
import { snapBox, fitZoom, safeArea } from "./geometry";
import { Autosaver, type QueueItem, type Transport, type TransportError } from "./autosave";
import { makeDoc, makeView, text } from "./fixtures";
import { makeTransport } from "./testkit";
import { useEditor, HISTORY_LIMIT } from "@/stores/editor";
import type { DocView, Patch } from "./types";

const el = (d: ReturnType<typeof makeDoc>, id: string) => d.elements.find(e => e.id === id)!;

describe("command engine", () => {
  it("never mutates its input and yields exact inverse patches for every command", () => {
    const d0 = makeDoc(); const snap = JSON.stringify(d0);
    const cmds = [cmd("UPDATE_TEXT", { layer_id: "head", text: "New" }), cmd("MOVE_ELEMENT", { layer_id: "prod", x: 5, y: 5 }), cmd("RESIZE_ELEMENT", { layer_id: "prod", w: 50, h: 60 }),
      cmd("ROTATE_ELEMENT", { layer_id: "prod", rotation: 33 }), cmd("CHANGE_COLOR", { layer_id: "cta", field: "fill", value: "#000000" }), cmd("CHANGE_COLOR", { layer_id: "background", field: "color", value: "#101010" }),
      cmd("REPLACE_IMAGE", { layer_id: "prod", asset_id: "a2" }), cmd("CROP_IMAGE", { layer_id: "prod", crop: { x: 0, y: 0, w: .5, h: .5 } }), cmd("REORDER_LAYER", { layer_id: "head", direction: "back" }),
      cmd("DUPLICATE_ELEMENT", { layer_id: "head", new_id: "dup" }), cmd("DELETE_ELEMENT", { layer_id: "cta" }), cmd("TOGGLE_VISIBILITY", { layer_id: "head" }), cmd("TOGGLE_LOCK", { layer_id: "head" }),
      cmd("ALIGN_ELEMENT", { layer_ids: ["head"], edge: "right" }), cmd("DISTRIBUTE_ELEMENTS", { layer_ids: ["prod", "head", "cta"], axis: "vertical" }),
      cmd("CHANGE_BACKGROUND", { background: { type: "gradient", color: "#000000", gradient_to: "#ffffff", angle: 90 } }), cmd("UPDATE_STYLE", { layer_id: "head", changes: { font_size: 40, align: "center" } }),
      cmd("ADD_LAYER", { layer: { type: "shape", id: "s1", role: "decor", name: "", x: 1, y: 1, w: 10, h: 10, opacity: 1, locked: false, visible: true, rotation: 0, shape: "rect", fill: "#abcdef", radius: 0, stroke: null, stroke_width: 0 } })];
    for (const c of cmds) {
      const r = apply(d0, c);
      expect(JSON.stringify(d0)).toBe(snap);                                         // immutable
      expect(apply(r.doc, patchCommand(r.before)).doc).toEqual(d0);                  // undo restores exactly
      expect(apply(d0, patchCommand(r.after)).doc).toEqual(r.doc);                   // redo reproduces exactly
    }
  });
  it("enforces locks, bounds, protected fields and no-ops", () => {
    const d = makeDoc();
    expect(() => apply({ ...d, elements: [text({ locked: true })] }, cmd("UPDATE_TEXT", { layer_id: "head", text: "x" }))).toThrowError(/locked/);
    expect(() => apply(d, cmd("MOVE_ELEMENT", { layer_id: "head", x: 9999, y: 0 }))).toThrowError(CommandError);
    expect(() => apply(d, cmd("UPDATE_STYLE", { layer_id: "head", changes: { asset_id: "x" } }))).toThrowError(/dedicated/);
    expect(() => apply(d, cmd("UPDATE_TEXT", { layer_id: "head", text: "Hello world" }))).toThrowError(/no change/);
    expect(() => apply(d, cmd("NOPE" as never, {}))).toThrowError(/Unknown/);
    expect(() => apply(d, cmd("REPLACE_IMAGE", { layer_id: "head", asset_id: "a" }))).toThrowError(/Only image/);
  });
  it("align to canvas / selection and distribute produce expected geometry", () => {
    const d = makeDoc();
    expect(el(apply(d, cmd("ALIGN_ELEMENT", { layer_ids: ["head"], edge: "center" })).doc, "head").x).toBe(50);
    const sel = apply(d, cmd("ALIGN_ELEMENT", { layer_ids: ["head", "cta"], edge: "right", to: "selection" })).doc;
    expect(el(sel, "cta").x).toBe(110);
    const dist = apply(d, cmd("DISTRIBUTE_ELEMENTS", { layer_ids: ["head", "prod", "cta"], axis: "horizontal" })).doc;
    expect(el(dist, "prod").x).toBeGreaterThanOrEqual(el(dist, "head").x);
  });
  it("replacing an image clears its stale crop; AI-accepted shape becomes an image in place", () => {
    const d = { ...makeDoc(), elements: [{ ...(makeDoc().elements[0]), crop: { x: 0, y: 0, w: .5, h: .5 } }] } as ReturnType<typeof makeDoc>;
    expect((apply(d, cmd("REPLACE_IMAGE", { layer_id: "prod", asset_id: "n" })).doc.elements[0] as { crop: unknown }).crop).toBeNull();
    const shape = { type: "shape", id: "s", role: "decor", name: "", x: 0, y: 0, w: 40, h: 40, opacity: 1, locked: false, visible: true, rotation: 0, shape: "rect", fill: "#fff", radius: 4, stroke: null, stroke_width: 0 } as never;
    const r = apply({ ...makeDoc(), elements: [shape] }, cmd("REGENERATE_ELEMENT_ACCEPTED", { layer_id: "s", asset_id: "gen" }));
    expect(r.doc.elements[0]).toMatchObject({ type: "image", id: "s", asset_id: "gen", w: 40 });
  });
});

describe("text layout parity with backend rendering/text.py", () => {
  const measure = (t: string, _f: string, _w: number, size: number) => t.length * size * 0.5;
  for (const [i, c] of (parity as { args: Record<string, unknown>; expect: Record<string, unknown> }[]).entries()) {
    it(`golden case ${i}`, () => {
      const a = c.args as Record<string, number | string | boolean>;
      const r = fitText((t, f, w, s) => measure(t, f, w, s), { text: a.text as string, family: a.family as string, weight: a.weight as number, size: a.size as number, minSize: a.min_size as number, maxLines: a.max_lines as number, lineHeight: a.line_height as number, boxW: a.box_w as number, boxH: a.box_h as number, spacing: a.spacing as number, uppercase: a.uppercase as boolean });
      // the same measure must be used on the Python side for letter spacing: width = n*size*.5 + spacing*(n-1)
      const m2 = (t: string, _f: string, _w: number, s: number) => t.length * s * 0.5;
      expect(r.lines).toEqual((c.expect as { lines: string[] }).lines);
      expect(r.fontSize).toBeCloseTo((c.expect as { font_size: number }).font_size, 6);
      expect(r.overflow).toBe((c.expect as { overflow: boolean }).overflow);
      expect(r.shrunk).toBe((c.expect as { shrunk: boolean }).shrunk);
      void m2;
    });
  }
  it("auto_fit=false keeps the size and reports overflow instead of shrinking", () => {
    const r = fitText((t, _f, _w, s) => t.length * s * 0.5, { text: "long long long long long", family: "x", weight: 400, size: 40, minSize: 10, maxLines: 1, lineHeight: 1, boxW: 100, boxH: 40, autoFit: false });
    expect(r.fontSize).toBe(40); expect(r.overflow).toBe(true);
  });
});

describe("geometry", () => {
  it("snaps to canvas centre and other layers, with guides, and to grid otherwise", () => {
    const c = { width: 300, height: 250 };
    const s = snapBox({ x: 98, y: 40, w: 100, h: 50 }, c, [], { threshold: 4 });
    expect(s.x).toBe(100); expect(s.guides).toContainEqual({ axis: "x", pos: 150 });
    const o = snapBox({ x: 11, y: 117, w: 40, h: 40 }, c, [{ x: 10, y: 200, w: 10, h: 10 }], { threshold: 4 });
    expect(o.x).toBe(10);
    const g = snapBox({ x: 33, y: 71, w: 20, h: 20 }, c, [], { threshold: 2, grid: 10 });
    expect([g.x, g.y]).toEqual([30, 70]);
    expect(fitZoom({ width: 1000, height: 500 }, { width: 548, height: 400 })).toBeCloseTo(0.45, 2);
    expect(safeArea(c, { top_pct: 10, bottom_pct: 20 })).toEqual({ x: 0, y: 25, w: 300, h: 175 });
    expect(safeArea(c, null)).toBeNull();
  });
});

// ------------------------------------------------------------------------------------------------ autosave
const mk = (t: Transport, extra: Record<string, unknown> = {}) => {
  const log = { status: [] as string[], conflicts: [] as unknown[], rejected: [] as unknown[], saved: [] as number[] };
  const s = new Autosaver({ transport: t, debounceMs: 100, baseDelayMs: 10, maxRetries: 2, onStatus: st => log.status.push(st), onSaved: d => log.saved.push(d.revision), onConflict: (l, p) => log.conflicts.push([l, p]), onRejected: (i, e) => log.rejected.push([i, e]), ...extra }, 0);
  return { s, log };
};
const item = (n: string): QueueItem => ({ kind: "commands", batchId: "batch-" + n, commands: [cmd("UPDATE_TEXT", { layer_id: "head", text: n })] });

describe("autosave", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  it("debounces, then sends strictly in order, each with the revision returned by the previous save", async () => {
    const { t, sent } = makeTransport(); const { s, log } = mk(t);
    s.enqueue(item("a")); s.enqueue(item("b")); s.enqueue(item("c"));
    expect(log.status.at(-1)).toBe("unsaved"); expect(sent).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(150);
    expect(sent.map(x => x.batch)).toEqual(["batch-a", "batch-b", "batch-c"]); expect(sent.map(x => x.expected)).toEqual([0, 1, 2]);
    expect(s.status).toBe("saved"); expect(s.pending).toBe(0);
  });
  it("transient failures retry with backoff keeping the edits; persistent failure needs an explicit retry", async () => {
    let fail = 5; const { t, sent } = makeTransport({ async commands(b) { if (fail-- > 0) throw { status: 0, message: "network" } as TransportError; return { document: makeView("doc1", b.expected_revision + 1) }; } });
    const { s } = mk(t); s.enqueue(item("a")); await vi.advanceTimersByTimeAsync(2000);
    expect(s.status).toBe("failed"); expect(s.pending).toBe(1);                      // edit preserved
    await s.retry(); await vi.advanceTimersByTimeAsync(2000); expect(s.status).toBe("saved"); expect(s.pending).toBe(0); void sent;
  });
  it("shows offline and resumes when back online without losing edits", async () => {
    let online = false; const { t } = makeTransport(); const { s, log } = mk(t, { isOnline: () => online });
    s.enqueue(item("a")); await vi.advanceTimersByTimeAsync(150); expect(s.status).toBe("offline"); expect(s.pending).toBe(1);
    online = true; await vi.advanceTimersByTimeAsync(100); expect(s.status).toBe("saved"); expect(log.saved).toEqual([1]);
  });
  it("a lost response is retried with the SAME batch id (idempotent) and never double-applies", async () => {
    const seen: string[] = []; let calls = 0;
    const { t } = makeTransport({ async commands(b) { seen.push(b.batch_id); if (calls++ === 0) throw { status: 503, message: "lost" } as TransportError; return { document: makeView("doc1", 1), duplicate: true }; } });
    const { s } = mk(t); s.enqueue(item("a")); await vi.advanceTimersByTimeAsync(500);
    expect(seen).toEqual(["batch-a", "batch-a"]); expect(s.pending).toBe(0);
  });
  it("a revision conflict pauses saving, keeps pending edits and hands over the latest document", async () => {
    const latest = makeView("doc1", 7); const { t, sent } = makeTransport({ async commands() { throw { status: 409, code: "revision_conflict", message: "changed", body: { document: latest } } as TransportError; } });
    const { s, log } = mk(t); s.enqueue(item("a")); s.enqueue(item("b")); await vi.advanceTimersByTimeAsync(300);
    expect(s.status).toBe("conflict"); expect(s.pending).toBe(2); expect(log.conflicts).toHaveLength(1); expect((log.conflicts[0] as [DocView])[0].revision).toBe(7);
    s.enqueue(item("c")); await vi.advanceTimersByTimeAsync(300); expect(sent).toHaveLength(0);       // nothing more is sent until resolved
    const ok = makeTransport(); (s as unknown as { o: { transport: Transport } }).o.transport = ok.t; s.resume(7, s.queue.slice()); await vi.advanceTimersByTimeAsync(50);
    expect(ok.sent[0].expected).toBe(7);
  });
  it("permanent rejections drop only that change and are reported", async () => {
    let first = true; const { t } = makeTransport({ async commands(b) { if (first) { first = false; throw { status: 423, code: "layer_locked", message: "locked" } as TransportError; } return { document: makeView("doc1", b.expected_revision + 1) }; } });
    const { s, log } = mk(t); s.enqueue(item("a")); s.enqueue(item("b")); await vi.advanceTimersByTimeAsync(400);
    expect(log.rejected).toHaveLength(1); expect(s.pending).toBe(0); expect(s.status).toBe("saved");
  });
  it("flush() saves immediately (Cmd/Ctrl+S)", async () => { const { t, sent } = makeTransport(); const { s } = mk(t); s.enqueue(item("a")); await s.flush(); expect(sent).toHaveLength(1); });
});

// ------------------------------------------------------------------------------------------------ store
function boot(rev = 0, tr = makeTransport()) { useEditor.getState().load(makeView("doc1", rev), tr.t, { debounceMs: 50 }); return tr; }
const doc = () => useEditor.getState().doc!;

describe("editor store: undo/redo, history, conflicts, independence", () => {
  beforeEach(() => { vi.useFakeTimers(); useEditor.getState().reset(); });
  it("undo/redo for text, position, size, colour, visibility and layer order; new edit invalidates redo", async () => {
    boot(); const s = useEditor.getState();
    const steps = [cmd("UPDATE_TEXT", { layer_id: "head", text: "T" }), cmd("MOVE_ELEMENT", { layer_id: "prod", x: 1, y: 1 }), cmd("RESIZE_ELEMENT", { layer_id: "prod", w: 60, h: 60 }), cmd("CHANGE_COLOR", { layer_id: "cta", field: "fill", value: "#123456" }),
      cmd("TOGGLE_VISIBILITY", { layer_id: "head" }), cmd("REORDER_LAYER", { layer_id: "head", direction: "back" })];
    const start = structuredClone(doc());
    steps.forEach(c => expect(s.dispatch(c)).toBe(true));
    const end = structuredClone(doc());
    for (let i = 0; i < steps.length; i++) useEditor.getState().undo();
    expect(doc()).toEqual(start);
    for (let i = 0; i < steps.length; i++) useEditor.getState().redo();
    expect(doc()).toEqual(end);
    useEditor.getState().undo(); expect(useEditor.getState().future).toHaveLength(1);
    useEditor.getState().dispatch(cmd("UPDATE_TEXT", { layer_id: "head", text: "branch" })); expect(useEditor.getState().future).toHaveLength(0);
  });
  it("sends edits, undo and redo to the server in order and ends saved", async () => {
    const calls: string[] = []; const base = makeTransport();
    const t: Transport = { async commands(b) { calls.push("cmd"); return base.t.commands(b); }, async undo(e) { calls.push("undo"); return base.t.undo(e); }, async redo(e) { calls.push("redo"); return base.t.redo(e); } };
    boot(0, { t, sent: base.sent }); const s = useEditor.getState();
    s.dispatch(cmd("MOVE_ELEMENT", { layer_id: "prod", x: 3, y: 3 })); useEditor.getState().undo(); useEditor.getState().redo();
    await vi.advanceTimersByTimeAsync(300); expect(calls).toEqual(["cmd", "undo", "redo"]); expect(useEditor.getState().saveStatus).toBe("saved");
  });
  it("typing bursts coalesce into one history step and one saved command; later edits do not", async () => {
    const tr = boot(); const s = useEditor.getState();
    for (const ch of ["H", "He", "Hel", "Hell", "Hello"]) s.dispatch(cmd("UPDATE_TEXT", { layer_id: "head", text: ch }), { coalesceKey: "text:head" });
    expect(useEditor.getState().past).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(300); expect(tr.sent).toHaveLength(1);
    useEditor.getState().undo(); expect((el(doc(), "head") as { text: string }).text).toBe("Hello world");
  });
  it("memory is bounded", () => { boot(); for (let i = 0; i < HISTORY_LIMIT + 30; i++) useEditor.getState().dispatch(cmd("MOVE_ELEMENT", { layer_id: "prod", x: i % 100, y: 3 + (i % 7) })); expect(useEditor.getState().past.length).toBeLessThanOrEqual(HISTORY_LIMIT); });
  it("read-only viewers cannot edit", () => { useEditor.getState().load(makeView(), makeTransport().t, { readOnly: true }); expect(useEditor.getState().dispatch(cmd("UPDATE_TEXT", { layer_id: "head", text: "x" }))).toBe(false); expect(useEditor.getState().pending).toBe(0); });
  it("rejected local commands surface a message instead of corrupting state", () => { boot(); const before = structuredClone(doc()); useEditor.getState().dispatch(cmd("MOVE_ELEMENT", { layer_id: "head", x: 9999, y: 9999 })); expect(doc()).toEqual(before); expect(useEditor.getState().notice).toMatch(/outside/); expect(useEditor.getState().pending).toBe(0); });
  it("copy / paste adds an independent duplicate, selection follows deletion", () => {
    boot(); const s = useEditor.getState(); s.select(["head"]); s.copy(); useEditor.getState().paste();
    expect(doc().elements).toHaveLength(4); expect(useEditor.getState().selection[0]).not.toBe("head");
    useEditor.getState().dispatch(cmd("DELETE_ELEMENT", { layer_id: useEditor.getState().selection[0] })); expect(useEditor.getState().selection).toEqual([]);
  });
  it("conflict: local edits are preserved; reapply replays them on the newer revision, reload discards them", async () => {
    const theirs = makeDoc(); (theirs.elements[2] as { fill: string }).fill = "#00ff00"; const latest = makeView("doc1", 5, theirs);
    const t = makeTransport({ async commands() { throw { status: 409, code: "revision_conflict", message: "changed", body: { document: latest } } as TransportError; } });
    boot(0, t); useEditor.getState().dispatch(cmd("UPDATE_TEXT", { layer_id: "head", text: "Mine" })); await vi.advanceTimersByTimeAsync(300);
    let st = useEditor.getState(); expect(st.saveStatus).toBe("conflict"); expect(st.conflict?.pending).toHaveLength(1); expect((el(doc(), "head") as { text: string }).text).toBe("Mine");
    expect(st.dispatch(cmd("UPDATE_TEXT", { layer_id: "head", text: "blocked" }))).toBe(false);          // editing is paused until the user chooses
    const ok = makeTransport({}, structuredClone(theirs)); (st.saver as unknown as { o: { transport: Transport } }).o.transport = ok.t;
    st.resolveConflict("reapply"); await vi.advanceTimersByTimeAsync(300); st = useEditor.getState();
    expect((el(doc(), "head") as { text: string }).text).toBe("Mine"); expect((el(doc(), "cta") as { fill: string }).fill).toBe("#00ff00");   // both mine and theirs
    expect(ok.sent[0].expected).toBe(5); expect(st.conflict).toBeNull(); expect(st.saveStatus).toBe("saved");
  });
  it("conflict reload discards local unsaved changes", async () => {
    const latest = makeView("doc1", 5);
    const t = makeTransport({ async commands() { throw { status: 409, code: "revision_conflict", message: "changed", body: { document: latest } } as TransportError; } });
    boot(0, t); useEditor.getState().dispatch(cmd("UPDATE_TEXT", { layer_id: "head", text: "Mine" })); await vi.advanceTimersByTimeAsync(300);
    useEditor.getState().resolveConflict("reload"); expect((el(doc(), "head") as { text: string }).text).toBe("Hello world"); expect(useEditor.getState().pending).toBe(0);
  });
  it("adoptServer (AI accept / restore) becomes one undoable step without re-sending anything", async () => {
    const tr = boot(); const next = makeDoc(); (next.elements[1] as { text: string }).text = "From AI";
    useEditor.getState().adoptServer(makeView("doc1", 1, next), "AI edit"); expect(tr.sent).toHaveLength(0);
    expect((el(doc(), "head") as { text: string }).text).toBe("From AI"); useEditor.getState().undo(); expect((el(doc(), "head") as { text: string }).text).toBe("Hello world");
  });
  it("loading another format resets everything: edits to one format never leak into another", async () => {
    const trA = boot(0); useEditor.getState().dispatch(cmd("UPDATE_TEXT", { layer_id: "head", text: "Only A" })); await useEditor.getState().saveNow();
    const trB = makeTransport(); useEditor.getState().load(makeView("doc2", 0, makeDoc(1080, 1080)), trB.t);
    expect(doc().canvas).toEqual({ width: 1080, height: 1080 }); expect((el(doc(), "head") as { text: string }).text).toBe("Hello world"); expect(useEditor.getState().past).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(500); expect(trB.sent).toHaveLength(0); expect(trA.sent).toHaveLength(1);
  });
  it("recovers unsaved commands from a previous session", () => {
    const rec: QueueItem[] = [{ kind: "commands", batchId: "r1", commands: [cmd("UPDATE_TEXT", { layer_id: "head", text: "Recovered" })] }, { kind: "commands", batchId: "r2", commands: [cmd("UPDATE_TEXT", { layer_id: "ghost", text: "x" })] }];
    useEditor.getState().load(makeView(), makeTransport().t, { recover: rec }); expect((el(doc(), "head") as { text: string }).text).toBe("Recovered"); expect(useEditor.getState().pending).toBe(1);
  });
});
