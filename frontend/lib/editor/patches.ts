import type { Doc, Layer, Patch } from "./types";

/** Merge two consecutive patches (a then b) into one: earliest `before`, latest `after`. */
export function mergePatches(a: { before: Patch; after: Patch }, b: { before: Patch; after: Patch }): { before: Patch; after: Patch } {
  const bl = { ...b.before.layers, ...a.before.layers }, al = { ...a.after.layers, ...b.after.layers };
  const before: Patch = { layers: bl, order: a.before.order }, after: Patch = { layers: al, order: b.after.order };
  const bg0 = a.before.background ?? b.before.background, bg1 = b.after.background ?? a.after.background;
  if (bg0 && bg1) { before.background = bg0; after.background = bg1; }
  return { before, after };
}

/** Generic diff between two documents as an undoable pair of patches (used for server-side changes such as AI accept / restore). */
export function diffPatch(a: Doc, b: Doc): { before: Patch; after: Patch } {
  const am = new Map(a.elements.map(e => [e.id, e] as const)), bm = new Map(b.elements.map(e => [e.id, e] as const));
  const ids = new Set<string>();
  for (const [id, e] of bm) if (JSON.stringify(am.get(id)) !== JSON.stringify(e)) ids.add(id);
  for (const id of am.keys()) if (!bm.has(id)) ids.add(id);
  const pick = (m: Map<string, Layer>) => Object.fromEntries([...ids].map(i => [i, m.get(i) ?? null]));
  const before: Patch = { layers: pick(am), order: a.elements.map(e => e.id) }, after: Patch = { layers: pick(bm), order: b.elements.map(e => e.id) };
  if (JSON.stringify(a.background) !== JSON.stringify(b.background)) { before.background = a.background; after.background = b.background; }
  return { before, after };
}
