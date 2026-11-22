import { cmd } from "./commands";
import { nudge } from "./geometry";
import { useEditor } from "@/stores/editor";

export const SHORTCUTS: { keys: string; action: string }[] = [
  { keys: "Ctrl/⌘ + Z", action: "Undo" }, { keys: "Ctrl/⌘ + Shift + Z (or Ctrl + Y)", action: "Redo" }, { keys: "Ctrl/⌘ + D", action: "Duplicate selected layer(s)" },
  { keys: "Delete / Backspace", action: "Delete selected layer(s) (locked layers are protected; undo restores)" }, { keys: "Arrow keys", action: "Move selection 1 px" }, { keys: "Shift + Arrow", action: "Move selection 10 px" },
  { keys: "Escape", action: "Clear selection / close menu" }, { keys: "Ctrl/⌘ + S", action: "Save now" }, { keys: "Ctrl/⌘ + C / V", action: "Copy / paste layer" }, { keys: "Space + drag", action: "Pan" }, { keys: "Ctrl/⌘ + scroll", action: "Zoom at pointer" },
];

export interface KeyLike { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey?: boolean; target: EventTarget | null; preventDefault(): void }
const isTextTarget = (t: EventTarget | null) => { const e = t as HTMLElement | null; if (!e || !("tagName" in e)) return false; const tag = e.tagName; return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.isContentEditable === true; };

/** Returns true when the key was handled. Inside text fields only Save is intercepted, so normal typing/undo/delete keep working. */
export function handleShortcut(e: KeyLike, save: () => void): boolean {
  const mod = e.ctrlKey || e.metaKey; const k = e.key.toLowerCase(); const st = useEditor.getState();
  if (mod && k === "s") { e.preventDefault(); save(); return true; }
  if (isTextTarget(e.target) || !st.doc) return false;
  const act = (fn: () => void) => { e.preventDefault(); fn(); return true; };
  if (mod && k === "z") return act(() => (e.shiftKey ? st.redo() : st.undo()));
  if (mod && k === "y") return act(() => st.redo());
  if (mod && k === "c") return act(() => st.copy());
  if (mod && k === "v") return act(() => st.paste());
  if (mod && k === "d") return act(() => { const ids = st.selection.filter(i => i !== "background"); if (ids.length) { st.dispatchBatch(ids.map(id => cmd("DUPLICATE_ELEMENT", { layer_id: id })), "Duplicate"); } });
  if (k === "escape") return act(() => { st.clearSelection(); st.setPreview(null); });
  const ids = st.selection.filter(i => i !== "background");
  if ((k === "delete" || k === "backspace") && ids.length) {
    return act(() => {
      const deletable = ids.filter(id => !st.doc!.elements.find(l => l.id === id)?.locked);
      if (!deletable.length) { useEditor.setState({ notice: "Locked layers can't be deleted. Unlock them first." }); return; }
      st.dispatchBatch(deletable.map(id => cmd("DELETE_ELEMENT", { layer_id: id })), "Delete");
      useEditor.setState({ notice: `Deleted ${deletable.length} layer${deletable.length > 1 ? "s" : ""}. Press Ctrl/⌘+Z to undo.` });
    });
  }
  const dir = { arrowleft: [-1, 0], arrowright: [1, 0], arrowup: [0, -1], arrowdown: [0, 1] }[k];
  if (dir && ids.length && !mod) {
    return act(() => { const step = nudge(e.shiftKey); st.dispatchBatch(ids.flatMap(id => { const l = st.doc!.elements.find(x => x.id === id); return l && !l.locked ? [cmd("MOVE_ELEMENT", { layer_id: id, x: l.x + dir[0] * step, y: l.y + dir[1] * step })] : []; }), "Nudge", `nudge:${ids.join(",")}`); });
  }
  return false;
}
