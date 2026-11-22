/** Client mirror of backend app/editor/commands.py (the server is authoritative and re-validates everything).
 *  Pure + immutable: apply(doc, cmd) never mutates its input; it returns before/after patches that are exact inverses. */
import { BG, type Background, type Command, type Doc, type Layer, type Patch } from "./types";

export class CommandError extends Error { constructor(public code: string, message: string) { super(message); } }
export interface Applied { doc: Doc; before: Patch; after: Patch }

const clone = <T,>(x: T): T => structuredClone(x);
const FORBIDDEN_STYLE = new Set(["id", "type", "asset_id", "x", "y", "w", "h", "rotation", "locked", "visible"]);
let counter = 0;
export const newLayerId = () => `l${Date.now().toString(36)}${(counter++).toString(36)}${Math.random().toString(36).slice(2, 6)}`;
export const batchId = () => `b${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;

export function apply(doc: Doc, cmd: Command): Applied {
  const p = cmd.payload as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  const next: Doc = clone(doc);
  const els = next.elements;
  const find = (id: string) => { const e = els.find(x => x.id === id); if (!e) throw new CommandError("layer_not_found", `Layer ${id} not found`); return e; };
  const free = (id: string) => { const e = find(id); if (e.locked) throw new CommandError("layer_locked", `Layer ${id} is locked`); return e; };
  const { width: W, height: H } = next.canvas;
  const touched = new Set<string>();
  let bgTouched = false;
  const inCanvas = (e: Layer) => { if (e.x + e.w <= 0 || e.y + e.h <= 0 || e.x >= W || e.y >= H) throw new CommandError("out_of_canvas", "Layer would be entirely outside the canvas"); };
  const orderBefore = els.map(e => e.id);

  switch (cmd.type) {
    case "ADD_LAYER": { const l = { ...p.layer, id: p.layer.id && !els.some(e => e.id === p.layer.id) ? p.layer.id : newLayerId() } as Layer; inCanvas(l); els.splice(p.index ?? els.length, 0, l); touched.add(l.id); break; }
    case "UPDATE_TEXT": { const e = free(p.layer_id); if (e.type === "text") e.text = p.text; else if (e.type === "button") e.label = p.text; else throw new CommandError("wrong_type", "Only text and CTA layers have text"); touched.add(e.id); break; }
    case "UPDATE_STYLE": {
      if (p.layer_id === BG) { if (next.background.locked) throw new CommandError("layer_locked", "Background is locked"); Object.assign(next.background, p.changes); bgTouched = true; break; }
      const e = free(p.layer_id);
      for (const k of Object.keys(p.changes)) if (FORBIDDEN_STYLE.has(k)) throw new CommandError("forbidden_field", `Use a dedicated command for ${k}`);
      Object.assign(e, p.changes); touched.add(e.id); break;
    }
    case "MOVE_ELEMENT": { const e = free(p.layer_id); e.x = p.x; e.y = p.y; inCanvas(e); touched.add(e.id); break; }
    case "RESIZE_ELEMENT": { const e = free(p.layer_id); e.w = p.w; e.h = p.h; if (p.x != null) e.x = p.x; if (p.y != null) e.y = p.y; inCanvas(e); touched.add(e.id); break; }
    case "ROTATE_ELEMENT": { const e = free(p.layer_id); e.rotation = ((((p.rotation + 180) % 360) + 360) % 360) - 180; touched.add(e.id); break; }
    case "CHANGE_COLOR": {
      if (p.layer_id === BG) { (next.background as any)[p.field] = p.value; bgTouched = true; break; }
      const e = free(p.layer_id) as any; if (!(p.field in e) && !(p.field === "stroke" && e.type === "shape")) throw new CommandError("wrong_field", `${e.type} layers have no ${p.field}`);
      e[p.field] = p.value; touched.add(e.id); break;
    }
    case "REPLACE_IMAGE": { const e = free(p.layer_id); if (e.type !== "image") throw new CommandError("wrong_type", "Only image layers can be replaced"); e.asset_id = p.asset_id; e.crop = null; touched.add(e.id); break; }
    case "CHANGE_BACKGROUND": { if (next.background.locked) throw new CommandError("layer_locked", "Background is locked"); next.background = { scrims: next.background.scrims, ...p.background, locked: next.background.locked } as Background; bgTouched = true; break; }
    case "CROP_IMAGE": { const e = free(p.layer_id); if (e.type !== "image") throw new CommandError("wrong_type", "Only image layers can be cropped"); e.crop = p.crop; touched.add(e.id); break; }
    case "REORDER_LAYER": {
      const e = free(p.layer_id); const i = els.indexOf(e);
      const j = p.to_index != null ? Math.max(0, Math.min(p.to_index, els.length - 1)) : ({ front: els.length - 1, back: 0, forward: Math.min(i + 1, els.length - 1), backward: Math.max(i - 1, 0) } as Record<string, number>)[p.direction];
      els.splice(i, 1); els.splice(j, 0, e); break;
    }
    case "DUPLICATE_ELEMENT": {
      const s = find(p.layer_id); if (els.length >= 40) throw new CommandError("too_many_layers", "A banner can have at most 40 layers");
      const d = clone(s); d.id = p.new_id && !els.some(e => e.id === p.new_id) ? p.new_id : newLayerId(); d.x += p.dx ?? 12; d.y += p.dy ?? 12; d.locked = false; d.name = `${(s.name || s.id).slice(0, 70)} copy`;
      els.splice(els.indexOf(s) + 1, 0, d); touched.add(d.id); break;
    }
    case "DELETE_ELEMENT": { const e = free(p.layer_id); els.splice(els.indexOf(e), 1); touched.add(e.id); break; }
    case "TOGGLE_VISIBILITY": { const e = find(p.layer_id); e.visible = p.visible ?? !e.visible; touched.add(e.id); break; }
    case "TOGGLE_LOCK": { if (p.layer_id === BG) { next.background.locked = p.locked ?? !next.background.locked; bgTouched = true; } else { const e = find(p.layer_id); e.locked = p.locked ?? !e.locked; touched.add(e.id); } break; }
    case "ALIGN_ELEMENT": {
      const t = (p.layer_ids as string[]).map(free);
      const sel = p.to === "selection" && t.length > 1;
      const x0 = sel ? Math.min(...t.map(a => a.x)) : 0, y0 = sel ? Math.min(...t.map(a => a.y)) : 0, x1 = sel ? Math.max(...t.map(a => a.x + a.w)) : W, y1 = sel ? Math.max(...t.map(a => a.y + a.h)) : H;
      for (const e of t) { if (p.edge === "left") e.x = x0; else if (p.edge === "right") e.x = x1 - e.w; else if (p.edge === "center") e.x = (x0 + x1) / 2 - e.w / 2; else if (p.edge === "top") e.y = y0; else if (p.edge === "bottom") e.y = y1 - e.h; else e.y = (y0 + y1) / 2 - e.h / 2; touched.add(e.id); }
      break;
    }
    case "DISTRIBUTE_ELEMENTS": {
      const t = (p.layer_ids as string[]).map(free); const [k, s] = p.axis === "horizontal" ? (["x", "w"] as const) : (["y", "h"] as const);
      t.sort((a, b) => a[k] - b[k]); const span = t[t.length - 1][k] + t[t.length - 1][s] - t[0][k]; const gap = (span - t.reduce((n, e) => n + e[s], 0)) / (t.length - 1);
      let cur = t[0][k]; for (const e of t) { e[k] = cur; cur += e[s] + gap; touched.add(e.id); } break;
    }
    case "REGENERATE_ELEMENT_ACCEPTED": {
      if (p.layer_id === BG) { next.background = { ...next.background, type: "image", asset_id: p.asset_id }; bgTouched = true; break; }
      const e = free(p.layer_id) as any;
      if (e.type === "image" && p.asset_id) { e.asset_id = p.asset_id; e.crop = null; }
      else if (e.type === "shape" && p.asset_id) { const keep = { id: e.id, role: e.role, name: e.name, x: e.x, y: e.y, w: e.w, h: e.h, opacity: e.opacity, locked: e.locked, visible: e.visible, rotation: e.rotation }; els[els.indexOf(e)] = { ...keep, type: "image", asset_id: p.asset_id, fit: "cover", shadow: "none", corner_radius: e.radius ?? 0, crop: null } as Layer; }
      else if ((e.type === "text" || e.type === "button") && p.text != null) e[e.type === "text" ? "text" : "label"] = p.text;
      else throw new CommandError("invalid_payload", "Replacement does not fit the selected layer type");
      touched.add(p.layer_id); break;
    }
    case "APPLY_PATCH": {
      const patch = p.patch as Patch; const by = new Map(els.map(e => [e.id, e] as const));
      for (const [id, v] of Object.entries(patch.layers)) { if (v === null) by.delete(id); else by.set(id, clone(v)); touched.add(id); }
      if (patch.background) { next.background = clone(patch.background); bgTouched = true; }
      next.elements = (patch.order ?? orderBefore).filter(i => by.has(i)).map(i => by.get(i)!); break;
    }
    default: throw new CommandError("unknown_command", `Unknown command ${cmd.type}`);
  }
  const oldBy = new Map(doc.elements.map(e => [e.id, e] as const)); const newBy = new Map(next.elements.map(e => [e.id, e] as const));
  for (const id of newBy.keys()) if (!oldBy.has(id)) touched.add(id);
  for (const id of oldBy.keys()) if (!newBy.has(id)) touched.add(id);
  const ids = [...touched];
  const before: Patch = { layers: Object.fromEntries(ids.map(i => [i, oldBy.get(i) ?? null])), order: orderBefore };
  const after: Patch = { layers: Object.fromEntries(ids.map(i => [i, newBy.get(i) ?? null])), order: next.elements.map(e => e.id) };
  if (bgTouched) { before.background = clone(doc.background); after.background = clone(next.background); }
  if (JSON.stringify(next) === JSON.stringify(doc)) throw new CommandError("no_change", "Command made no change");
  return { doc: next, before, after };
}

export const patchCommand = (patch: Patch): Command => ({ type: "APPLY_PATCH", payload: { patch } });
export const cmd = (type: Command["type"], payload: Record<string, unknown>): Command => ({ type, payload });
