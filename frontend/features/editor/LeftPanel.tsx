"use client";
import { useState } from "react";
import { Eye, EyeOff, GripVertical, ImageIcon, LayoutTemplate, Lock, Palette, Layers as LayersIcon, Shapes, Type, Unlock, Square, Circle as CircleIcon, MousePointerClick } from "lucide-react";
import clsx from "clsx";
import { cmd, newLayerId } from "@/lib/editor/commands";
import { BG, type Layer } from "@/lib/editor/types";
import { useEditor } from "@/stores/editor";
import AssetPicker, { useEditorAssets } from "./AssetPicker";
import TemplatesPanel from "./templates/TemplatesPanel";
import type { AssetItem } from "./useEditorApi";

type Tab = "elements" | "layers" | "assets" | "brand" | "backgrounds" | "templates";
const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: "elements", label: "Elements", icon: <Shapes size={15} /> }, { id: "layers", label: "Layers", icon: <LayersIcon size={15} /> }, { id: "assets", label: "Assets", icon: <ImageIcon size={15} /> },
  { id: "brand", label: "Brand", icon: <Palette size={15} /> }, { id: "backgrounds", label: "Backgrounds", icon: <Square size={15} /> },
  { id: "templates", label: "Templates", icon: <LayoutTemplate size={15} /> },
];
const base = { role: "decor", name: "", opacity: 1, locked: false, visible: true, rotation: 0 };
const send = (c: ReturnType<typeof cmd>, label?: string) => useEditor.getState().dispatch(c, { label });
const selectLast = () => { const d = useEditor.getState().doc; const l = d?.elements[d.elements.length - 1]; if (l) useEditor.getState().select([l.id]); };

function Elements() {
  const doc = useEditor(s => s.doc)!; const { width: W, height: H } = doc.canvas; const ro = useEditor(s => s.readOnly);
  const add = (layer: Record<string, unknown>) => { if (send(cmd("ADD_LAYER", { layer: { id: newLayerId(), ...layer } }), "Add layer")) selectLast(); };
  const c = (w: number, h: number) => ({ x: Math.round((W - w) / 2), y: Math.round((H - h) / 2), w, h });
  const fs = Math.max(12, Math.round(Math.min(W, H) * 0.08));
  const items = [
    { label: "Text", icon: <Type size={18} />, add: () => add({ ...base, ...c(Math.round(W * 0.6), Math.round(fs * 1.6)), type: "text", role: "body", name: "Text", text: "Your text here", font_family: "Liberation Sans", font_weight: 400, font_size: fs, min_font_size: Math.max(6, Math.round(fs / 2)), max_lines: 3, auto_fit: true, line_height: 1.15, color: "#111111", align: "left", valign: "top", letter_spacing: 0, uppercase: false }) },
    { label: "Button", icon: <MousePointerClick size={18} />, add: () => add({ ...base, ...c(Math.round(W * 0.35), Math.round(H * 0.12)), type: "button", role: "cta", name: "CTA", label: "Learn more", fill: "#ff5a1f", text_color: "#ffffff", font_family: "Liberation Sans", font_weight: 700, font_size: Math.max(10, Math.round(H * 0.05)), min_font_size: 8, radius: 8, uppercase: false, padding_x: null }) },
    { label: "Rectangle", icon: <Square size={18} />, add: () => add({ ...base, ...c(Math.round(W * 0.3), Math.round(W * 0.3)), type: "shape", name: "Rectangle", shape: "rect", fill: "#7c5cff", radius: 0, stroke: null, stroke_width: 0 }) },
    { label: "Ellipse", icon: <CircleIcon size={18} />, add: () => add({ ...base, ...c(Math.round(W * 0.3), Math.round(W * 0.3)), type: "shape", name: "Ellipse", shape: "ellipse", fill: "#22d3a6", radius: 0, stroke: null, stroke_width: 0 }) },
  ];
  return <div className="grid grid-cols-2 gap-2 p-3">{items.map(i => <button key={i.label} disabled={ro} onClick={i.add} className="flex flex-col items-center gap-1 rounded-lg border border-line py-4 text-xs text-zinc-300 hover:border-accent hover:text-white disabled:opacity-40">{i.icon}{i.label}</button>)}</div>;
}

function LayerList() {
  const doc = useEditor(s => s.doc)!; const sel = useEditor(s => s.selection); const select = useEditor(s => s.select); const ro = useEditor(s => s.readOnly);
  const [drag, setDrag] = useState<string | null>(null); const [over, setOver] = useState<number | null>(null);
  const rows = [...doc.elements].reverse();                                                       // top of the stack first
  const drop = (target: number) => { if (!drag) return; const toIndex = doc.elements.length - 1 - target; send(cmd("REORDER_LAYER", { layer_id: drag, to_index: toIndex }), "Reorder layer"); setDrag(null); setOver(null); };
  const row = (l: Layer, i: number) => (
    <li key={l.id} draggable={!ro && !l.locked} onDragStart={() => setDrag(l.id)} onDragOver={e => { e.preventDefault(); setOver(i); }} onDrop={() => drop(i)} onDragEnd={() => { setDrag(null); setOver(null); }}
      className={clsx("flex items-center gap-1 rounded-md border px-1.5 py-1 text-xs", sel.includes(l.id) ? "border-accent bg-accent/10" : "border-transparent hover:bg-white/5", over === i && drag && drag !== l.id && "border-t-2 !border-t-accent2")}>
      <GripVertical size={13} className="shrink-0 text-zinc-600" />
      <button className="flex-1 truncate text-left" onClick={e => select([l.id], e.shiftKey)} aria-label={`Select layer ${l.name || l.id}`}>{l.name || `${l.role} ${l.type}`}<span className="ml-1 text-zinc-500">{l.type}</span></button>
      <button aria-label={l.visible ? "Hide layer" : "Show layer"} disabled={ro} onClick={() => send(cmd("TOGGLE_VISIBILITY", { layer_id: l.id }))} className="p-1 text-zinc-400 hover:text-white">{l.visible ? <Eye size={13} /> : <EyeOff size={13} />}</button>
      <button aria-label={l.locked ? "Unlock layer" : "Lock layer"} disabled={ro} onClick={() => send(cmd("TOGGLE_LOCK", { layer_id: l.id }))} className="p-1 text-zinc-400 hover:text-white">{l.locked ? <Lock size={13} /> : <Unlock size={13} />}</button>
    </li>
  );
  return (
    <ul className="space-y-0.5 p-2" aria-label="Layers">
      {rows.map((l, i) => row(l, i))}
      <li className={clsx("flex items-center gap-1 rounded-md border px-1.5 py-1 text-xs", sel.includes(BG) ? "border-accent bg-accent/10" : "border-transparent hover:bg-white/5")}>
        <span className="w-[13px]" /><button className="flex-1 text-left" onClick={() => select([BG])} aria-label="Select layer Background">Background<span className="ml-1 text-zinc-500">{doc.background.type}</span></button>
        <button aria-label={doc.background.locked ? "Unlock background" : "Lock background"} disabled={ro} onClick={() => send(cmd("TOGGLE_LOCK", { layer_id: BG }))} className="p-1 text-zinc-400 hover:text-white">{doc.background.locked ? <Lock size={13} /> : <Unlock size={13} />}</button>
      </li>
    </ul>
  );
}

function AssetGrid({ items, onUse, extra }: { items: AssetItem[]; onUse: (a: AssetItem) => void; extra?: (a: AssetItem) => React.ReactNode }) {
  if (!items.length) return <p className="p-3 text-xs text-zinc-500">Nothing here yet.</p>;
  return <div className="grid grid-cols-2 gap-2 p-3">{items.map(a => (
    <div key={a.id} className="overflow-hidden rounded-lg border border-line">
      <button onClick={() => onUse(a)} aria-label={`Add ${a.kind} ${a.id.slice(0, 6)}`} className="block w-full bg-[repeating-conic-gradient(#222_0%_25%,#2a2a2a_0%_50%)] bg-[length:14px_14px] hover:opacity-90">{/* eslint-disable-next-line @next/next/no-img-element */}<img src={a.url} alt="" className="aspect-square w-full object-contain" /></button>
      <div className="flex items-center justify-between px-1.5 py-1 text-[10px] text-zinc-400"><span>{a.width}×{a.height}</span>{extra?.(a)}</div>
    </div>))}</div>;
}

export function addImage(a: AssetItem, role: string) {
  const st = useEditor.getState(); const d = st.doc!; const { width: W, height: H } = d.canvas;
  st.assetsPut(a); const s = Math.min((W * 0.5) / a.width, (H * 0.5) / a.height); const w = Math.max(8, Math.round(a.width * s)), h = Math.max(8, Math.round(a.height * s));
  if (st.dispatch(cmd("ADD_LAYER", { layer: { ...base, id: newLayerId(), type: "image", role, name: role === "logo" ? "Logo" : "Image", x: Math.round((W - w) / 2), y: Math.round((H - h) / 2), w, h, asset_id: a.id, fit: "contain", shadow: "none", corner_radius: 0, crop: null } }), { label: "Add image" })) selectLast();
}
const applyBg = (b: Record<string, unknown>) => send(cmd("CHANGE_BACKGROUND", { background: { ...useEditor.getState().doc!.background, ...b } }), "Change background");

export default function LeftPanel({ campaignId }: { campaignId: string }) {
  const [tab, setTab] = useState<Tab>("layers"); const { data } = useEditorAssets(campaignId); const [pick, setPick] = useState<null | "image" | "logo" | "background">(null);
  const sel = useEditor(s => s.selection); const doc = useEditor(s => s.doc)!; const view = useEditor(s => s.view)!; const ro = useEditor(s => s.readOnly); const mode = useEditor(s => s.mode);
  const selected = doc.elements.find(e => e.id === sel[0]);
  const colors = data?.brand.colors ?? [];
  const applyColor = (hex: string) => {
    if (!selected) return applyBg({ type: "solid", color: hex });
    const field = selected.type === "text" ? "color" : selected.type === "button" || selected.type === "shape" ? "fill" : null; if (field) send(cmd("CHANGE_COLOR", { layer_id: selected.id, field, value: hex }), "Brand colour");
  };
  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-line bg-panel" aria-label="Left panel">
      <div role="tablist" className="flex border-b border-line">{TABS.filter(t => mode === "advanced" || t.id !== "layers" || true).map(t => <button key={t.id} role="tab" aria-selected={tab === t.id} title={t.label} onClick={() => setTab(t.id)} className={clsx("flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]", tab === t.id ? "border-b-2 border-accent text-white" : "text-zinc-400 hover:text-white")}>{t.icon}{t.label}</button>)}</div>
      <div className="min-h-0 flex-1 overflow-y-auto" role="tabpanel">
        {tab === "templates" && <TemplatesPanel target={{ kind: "document", documentId: view.id, width: doc.canvas.width, height: doc.canvas.height }} />}
        {tab === "elements" && <Elements />}
        {tab === "layers" && <LayerList />}
        {tab === "assets" && <><div className="p-3 pb-0"><button className="btn w-full" disabled={ro} onClick={() => setPick("image")}>Upload / browse images</button></div>
          <AssetGrid items={data?.products ?? []} onUse={a => addImage(a, "decor")} extra={a => selected?.type === "image" && !selected.locked ? <button className="text-accent2 hover:underline" onClick={() => send(cmd("REPLACE_IMAGE", { layer_id: selected.id, asset_id: a.id }), "Replace image")}>Replace selected</button> : null} /></>}
        {tab === "brand" && <>
          <h4 className="label px-3 pt-3">Colors</h4>
          <div className="flex flex-wrap gap-2 px-3 py-2">{colors.length ? colors.map(c => <button key={c} aria-label={`Apply brand color ${c}`} title={`${c}${selected ? " → selected layer" : " → background"}`} disabled={ro} onClick={() => applyColor(c)} className="h-8 w-8 rounded-full border border-white/20" style={{ background: c }} />) : <p className="text-xs text-zinc-500">No brand colours detected yet.</p>}</div>
          <h4 className="label px-3 pt-2">Logos</h4>
          <AssetGrid items={data?.logos ?? []} onUse={a => addImage(a, "logo")} extra={a => selected?.type === "image" && selected.role === "logo" && !selected.locked ? <button className="text-accent2 hover:underline" onClick={() => send(cmd("REPLACE_IMAGE", { layer_id: selected.id, asset_id: a.id }), "Replace logo")}>Replace selected</button> : null} />
          <div className="px-3 pb-3"><button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent" disabled={ro} onClick={() => setPick("logo")}>Upload logo…</button></div>
          <h4 className="label px-3">Fonts</h4>
          <div className="space-y-1 px-3 pb-3">{(data?.brand.fonts ?? []).map(f => <button key={f} disabled={ro || !(selected?.type === "text" || selected?.type === "button")} onClick={() => selected && send(cmd("UPDATE_STYLE", { layer_id: selected.id, changes: { font_family: f } }), "Brand font")} className="block w-full rounded-md border border-line px-2 py-1 text-left text-xs hover:border-accent disabled:opacity-50" style={{ fontFamily: f }}>{f}</button>)}</div></>}
        {tab === "backgrounds" && <>
          <h4 className="label px-3 pt-3">Generated backgrounds</h4>
          <AssetGrid items={data?.backgrounds ?? []} onUse={a => { useEditor.getState().assetsPut(a); applyBg({ type: "image", asset_id: a.id }); }} />
          <h4 className="label px-3">Solid colors</h4>
          <div className="flex flex-wrap gap-2 px-3 py-2">{["#ffffff", "#111111", "#f5f5f5", ...colors].map(c => <button key={c} aria-label={`Solid ${c}`} disabled={ro} onClick={() => applyBg({ type: "solid", color: c })} className="h-8 w-8 rounded-md border border-white/20" style={{ background: c }} />)}</div>
          <div className="px-3 pb-3"><button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent" disabled={ro} onClick={() => setPick("background")}>Upload background…</button></div></>}
      </div>
      {pick && <AssetPicker campaignId={campaignId} title={pick === "logo" ? "Logos" : pick === "background" ? "Pick or upload a background" : "Images"} kinds={pick === "logo" ? ["logos"] : pick === "background" ? ["backgrounds", "products"] : ["products", "logos"]}
        onPick={a => pick === "background" ? applyBg({ type: "image", asset_id: a.id }) : addImage(a, pick === "logo" ? "logo" : "decor")} onClose={() => setPick(null)} />}
    </aside>
  );
}
