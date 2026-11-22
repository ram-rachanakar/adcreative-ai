import { beforeEach, describe, expect, it, vi } from "vitest";
import { handleShortcut, type KeyLike } from "./shortcuts";
import { makeTransport, boot } from "./testkit";
import { useEditor } from "@/stores/editor";

const key = (k: string, o: Partial<KeyLike> = {}): KeyLike & { prevented: boolean } => { const e = { key: k, ctrlKey: false, metaKey: false, shiftKey: false, target: null, prevented: false, preventDefault() { e.prevented = true; }, ...o }; return e; };
const doc = () => useEditor.getState().doc!; const head = () => doc().elements.find(e => e.id === "head")!;

describe("keyboard shortcuts", () => {
  beforeEach(() => { vi.useFakeTimers(); useEditor.getState().reset(); boot(); });
  it("undo / redo with ctrl or meta, shift for redo", () => {
    useEditor.getState().select(["head"]); handleShortcut(key("ArrowRight"), () => {}); const x = head().x;
    expect(handleShortcut(key("z", { metaKey: true }), () => {})).toBe(true); expect(head().x).toBe(x - 1);
    handleShortcut(key("z", { ctrlKey: true, shiftKey: true }), () => {}); expect(head().x).toBe(x);
  });
  it("arrows move 1px, shift+arrow 10px, bursts merge into one undo step, locked layers stay put", () => {
    useEditor.getState().select(["head"]); const x0 = head().x;
    handleShortcut(key("ArrowRight"), () => {}); handleShortcut(key("ArrowRight", { shiftKey: true }), () => {}); expect(head().x).toBe(x0 + 11); expect(useEditor.getState().past).toHaveLength(1);
    useEditor.getState().dispatch({ type: "TOGGLE_LOCK", payload: { layer_id: "head" } }); handleShortcut(key("ArrowLeft"), () => {}); expect(head().x).toBe(x0 + 11);
  });
  it("delete removes the selection, refuses locked layers, and is undoable; escape clears selection", () => {
    useEditor.getState().select(["cta"]); expect(handleShortcut(key("Delete"), () => {})).toBe(true); expect(doc().elements.some(e => e.id === "cta")).toBe(false);
    useEditor.getState().undo(); expect(doc().elements.some(e => e.id === "cta")).toBe(true);
    useEditor.getState().dispatch({ type: "TOGGLE_LOCK", payload: { layer_id: "cta" } }); useEditor.getState().select(["cta"]); handleShortcut(key("Backspace"), () => {}); expect(doc().elements.some(e => e.id === "cta")).toBe(true); expect(useEditor.getState().notice).toMatch(/Locked/);
    handleShortcut(key("Escape"), () => {}); expect(useEditor.getState().selection).toEqual([]);
  });
  it("duplicate (ctrl+d) and ctrl+s save", () => {
    useEditor.getState().select(["head"]); handleShortcut(key("d", { ctrlKey: true }), () => {}); expect(doc().elements).toHaveLength(4);
    const save = vi.fn(); const e = key("s", { metaKey: true }); handleShortcut(e, save); expect(save).toHaveBeenCalled(); expect(e.prevented).toBe(true);
  });
  it("never intercepts normal editing inside inputs (only save)", () => {
    useEditor.getState().select(["head"]); const input = { tagName: "INPUT" } as unknown as EventTarget; const before = JSON.stringify(doc());
    for (const k of ["Delete", "Backspace", "ArrowRight", "z"]) { const e = key(k, { target: input, ctrlKey: k === "z" }); expect(handleShortcut(e, () => {})).toBe(false); expect(e.prevented).toBe(false); }
    expect(JSON.stringify(doc())).toBe(before);
    const save = vi.fn(); expect(handleShortcut(key("s", { ctrlKey: true, target: input }), save)).toBe(true); expect(save).toHaveBeenCalled();
    const ta = { tagName: "DIV", isContentEditable: true } as unknown as EventTarget; expect(handleShortcut(key("Delete", { target: ta }), () => {})).toBe(false);
  });
});
