"use client";
import { useState } from "react";
import { AlignCenter, AlignEndHorizontal, AlignEndVertical, AlignLeft, AlignRight, AlignStartHorizontal, AlignStartVertical, AlignVerticalJustifyCenter, ArrowDownToLine, ArrowUpToLine, ChevronDown, ChevronUp, Copy, Eye, EyeOff, Lock, Sparkles, Trash2, Unlock, AlignHorizontalSpaceAround, AlignVerticalSpaceAround } from "lucide-react";
import { cmd } from "@/lib/editor/commands";
import { BG, FONT_FAMILIES, type ButtonLayer, type ImageLayer, type Layer, type ShapeLayer, type TextLayer } from "@/lib/editor/types";
import { useEditor } from "@/stores/editor";
import AssetPicker, { useEditorAssets } from "./AssetPicker";
import { ColorField, IconBtn, NumberField, Row, Section, Segmented } from "./controls";

const dispatch = (c: ReturnType<typeof cmd>, key?: string) => useEditor.getState().dispatch(c, key ? { coalesceKey: key } : {});
export const style = (id: string, changes: Record<string, unknown>) => dispatch(cmd("UPDATE_STYLE", { layer_id: id, changes }));

function Geometry({ l, off }: { l: Layer; off: boolean }) {
  return (
    <Section title="Position & size">
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        <Row label="X"><NumberField label="X" value={l.x} disabled={off} onCommit={v => dispatch(cmd("MOVE_ELEMENT", { layer_id: l.id, x: v, y: l.y }))} /></Row>
        <Row label="Y"><NumberField label="Y" value={l.y} disabled={off} onCommit={v => dispatch(cmd("MOVE_ELEMENT", { layer_id: l.id, x: l.x, y: v }))} /></Row>
        <Row label="Width"><NumberField label="Width" value={l.w} min={1} disabled={off} onCommit={v => dispatch(cmd("RESIZE_ELEMENT", { layer_id: l.id, w: v, h: l.h }))} /></Row>
        <Row label="Height"><NumberField label="Height" value={l.h} min={1} disabled={off} onCommit={v => dispatch(cmd("RESIZE_ELEMENT", { layer_id: l.id, w: l.w, h: v }))} /></Row>
        <Row label="Rotation"><NumberField label="Rotation" value={l.rotation} min={-180} max={180} disabled={off} onCommit={v => dispatch(cmd("ROTATE_ELEMENT", { layer_id: l.id, rotation: v }))} /></Row>
        <Row label="Opacity %"><NumberField label="Opacity" value={Math.round(l.opacity * 100)} min={0} max={100} disabled={off} onCommit={v => style(l.id, { opacity: v / 100 })} /></Row>
      </div>
    </Section>
  );
}

export function LayerActions({ l }: { l: Layer }) {
  const d = (type: Parameters<typeof cmd>[0], p: Record<string, unknown> = {}) => dispatch(cmd(type, { layer_id: l.id, ...p }));
  return (
    <Section title="Layer">
      <div className="flex flex-wrap gap-1.5">
        <IconBtn label={l.visible ? "Hide layer" : "Show layer"} onClick={() => d("TOGGLE_VISIBILITY")}>{l.visible ? <Eye size={15} /> : <EyeOff size={15} />}</IconBtn>
        <IconBtn label={l.locked ? "Unlock layer" : "Lock layer"} onClick={() => d("TOGGLE_LOCK")} active={l.locked}>{l.locked ? <Lock size={15} /> : <Unlock size={15} />}</IconBtn>
        <IconBtn label="Duplicate layer" onClick={() => d("DUPLICATE_ELEMENT")}><Copy size={15} /></IconBtn>
        <IconBtn label="Delete layer" disabled={l.locked} onClick={() => d("DELETE_ELEMENT")}><Trash2 size={15} /></IconBtn>
        <IconBtn label="Bring to front" disabled={l.locked} onClick={() => d("REORDER_LAYER", { direction: "front" })}><ArrowUpToLine size={15} /></IconBtn>
        <IconBtn label="Bring forward" disabled={l.locked} onClick={() => d("REORDER_LAYER", { direction: "forward" })}><ChevronUp size={15} /></IconBtn>
        <IconBtn label="Send backward" disabled={l.locked} onClick={() => d("REORDER_LAYER", { direction: "backward" })}><ChevronDown size={15} /></IconBtn>
        <IconBtn label="Send to back" disabled={l.locked} onClick={() => d("REORDER_LAYER", { direction: "back" })}><ArrowDownToLine size={15} /></IconBtn>
      </div>
      {l.locked && <p className="text-xs text-amber-400">Locked: unlock to edit this layer.</p>}
    </Section>
  );
}

const FontRow = ({ l, off }: { l: TextLayer | ButtonLayer; off: boolean }) => (
  <>
    <Row label="Font"><select aria-label="Font family" className="input !w-40 !py-1 text-xs" disabled={off} value={l.font_family} onChange={e => style(l.id, { font_family: e.target.value })}>{FONT_FAMILIES.map(f => <option key={f}>{f}</option>)}</select></Row>
    <Row label="Weight"><Segmented label="Weight" value={l.font_weight} disabled={off} options={[{ v: 400, label: "Regular" }, { v: 700, label: "Bold" }]} onChange={v => style(l.id, { font_weight: v })} /></Row>
    <Row label="Font size"><NumberField label="Font size" value={l.font_size} min={6} max={600} disabled={off} onCommit={v => style(l.id, { font_size: v, ...(l.min_font_size > v ? { min_font_size: v } : {}) })} /></Row>
  </>
);

function TextProps({ l, off }: { l: TextLayer; off: boolean }) {
  return (
    <>
      <Section title="Text">
        <textarea aria-label="Text content" className="input min-h-[64px] text-sm" disabled={off} maxLength={300} value={l.text} onChange={e => dispatch(cmd("UPDATE_TEXT", { layer_id: l.id, text: e.target.value }), `text:${l.id}`)} />
        <FontRow l={l} off={off} />
        <Row label="Line height"><NumberField label="Line height" value={l.line_height} min={0.9} max={2} step={0.05} disabled={off} onCommit={v => style(l.id, { line_height: v })} /></Row>
        <Row label="Letter spacing"><NumberField label="Letter spacing" value={l.letter_spacing} min={-5} max={40} step={0.5} disabled={off} onCommit={v => style(l.id, { letter_spacing: v })} /></Row>
        <Row label="Align"><Segmented label="Text align" value={l.align} disabled={off} options={[{ v: "left", label: <AlignLeft size={14} />, title: "Left" }, { v: "center", label: <AlignCenter size={14} />, title: "Center" }, { v: "right", label: <AlignRight size={14} />, title: "Right" }]} onChange={v => style(l.id, { align: v })} /></Row>
        <Row label="Vertical"><Segmented label="Vertical align" value={l.valign} disabled={off} options={[{ v: "top", label: "Top" }, { v: "middle", label: "Mid" }, { v: "bottom", label: "Bottom" }]} onChange={v => style(l.id, { valign: v })} /></Row>
        <Row label="Color"><ColorField label="Text color" value={l.color} disabled={off} onCommit={v => dispatch(cmd("CHANGE_COLOR", { layer_id: l.id, field: "color", value: v }))} /></Row>
        <Row label="Auto-fit"><input type="checkbox" aria-label="Auto-fit text" disabled={off} checked={l.auto_fit} onChange={e => style(l.id, { auto_fit: e.target.checked })} /></Row>
        {l.auto_fit && <Row label="Min size"><NumberField label="Minimum font size" value={l.min_font_size} min={6} max={l.font_size} disabled={off} onCommit={v => style(l.id, { min_font_size: v })} /></Row>}
        <Row label="Max lines"><NumberField label="Maximum lines" value={l.max_lines} min={1} max={8} disabled={off} onCommit={v => style(l.id, { max_lines: v })} /></Row>
        <Row label="UPPERCASE"><input type="checkbox" aria-label="Uppercase" disabled={off} checked={l.uppercase} onChange={e => style(l.id, { uppercase: e.target.checked })} /></Row>
      </Section>
    </>
  );
}

function ButtonProps({ l, off }: { l: ButtonLayer; off: boolean }) {
  return (
    <Section title="Call to action">
      <Row label="Label"><input aria-label="CTA text" className="input !w-44 !py-1 text-sm" disabled={off} maxLength={30} value={l.label} onChange={e => dispatch(cmd("UPDATE_TEXT", { layer_id: l.id, text: e.target.value }), `text:${l.id}`)} /></Row>
      <Row label="Background"><ColorField label="CTA background" value={l.fill} disabled={off} onCommit={v => dispatch(cmd("CHANGE_COLOR", { layer_id: l.id, field: "fill", value: v }))} /></Row>
      <Row label="Text color"><ColorField label="CTA text color" value={l.text_color} disabled={off} onCommit={v => dispatch(cmd("CHANGE_COLOR", { layer_id: l.id, field: "text_color", value: v }))} /></Row>
      <FontRow l={l} off={off} />
      <Row label="Padding"><NumberField label="CTA padding" value={l.padding_x ?? Math.round(l.w * 0.08)} min={0} max={500} disabled={off} onCommit={v => style(l.id, { padding_x: v })} /></Row>
      <Row label="Radius"><NumberField label="CTA radius" value={l.radius} min={0} max={2000} disabled={off} onCommit={v => style(l.id, { radius: v })} /></Row>
      <Row label="UPPERCASE"><input type="checkbox" aria-label="CTA uppercase" disabled={off} checked={l.uppercase} onChange={e => style(l.id, { uppercase: e.target.checked })} /></Row>
    </Section>
  );
}

function ImageProps({ l, off, campaignId }: { l: ImageLayer; off: boolean; campaignId: string }) {
  const [pick, setPick] = useState(false); const assets = useEditorAssets(campaignId).data; const info = useEditor(s => s.assets[l.asset_id]);
  const cutout = assets?.cutouts.find(c => c.parent_asset_id === l.asset_id);
  const crop = l.crop ?? { x: 0, y: 0, w: 1, h: 1 };
  const setCrop = (k: "x" | "y" | "w" | "h", v: number) => { const n = { ...crop, [k]: v / 100 }; if (n.x + n.w > 1.0001 || n.y + n.h > 1.0001) return; dispatch(cmd("CROP_IMAGE", { layer_id: l.id, crop: n })); };
  return (
    <>
      <Section title="Image">
        <div className="flex items-center gap-2">{/* eslint-disable-next-line @next/next/no-img-element */}{info?.url && <img src={info.url} alt="" className="h-12 w-12 rounded border border-line object-contain" />}
          <button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40" disabled={off} onClick={() => setPick(true)}>Replace image…</button>
          {cutout && <button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40" disabled={off} onClick={() => dispatch(cmd("REPLACE_IMAGE", { layer_id: l.id, asset_id: cutout.id }))}>Use background-removed</button>}</div>
        {info?.missing && <p className="err">This image is missing. Replace it before exporting.</p>}
        <Row label="Fit"><Segmented label="Image fit" value={l.fit} disabled={off} options={[{ v: "contain", label: "Contain" }, { v: "cover", label: "Cover" }, { v: "fill", label: "Fill" }]} onChange={v => style(l.id, { fit: v })} /></Row>
        <Row label="Corner radius"><NumberField label="Corner radius" value={l.corner_radius} min={0} max={2000} disabled={off} onCommit={v => style(l.id, { corner_radius: v })} /></Row>
        <Row label="Shadow"><Segmented label="Shadow" value={l.shadow} disabled={off} options={[{ v: "none", label: "None" }, { v: "contact", label: "Contact" }, { v: "drop", label: "Drop" }]} onChange={v => style(l.id, { shadow: v })} /></Row>
      </Section>
      <Section title="Crop (% of original)">
        <div className="grid grid-cols-2 gap-x-3 gap-y-2">{(["x", "y", "w", "h"] as const).map(k => <Row key={k} label={{ x: "Left", y: "Top", w: "Width", h: "Height" }[k]}><NumberField label={`Crop ${k}`} value={Math.round(crop[k] * 100)} min={k === "w" || k === "h" ? 1 : 0} max={100} disabled={off} onCommit={v => setCrop(k, v)} /></Row>)}</div>
        {l.crop && <button className="text-xs text-accent2 underline" disabled={off} onClick={() => dispatch(cmd("CROP_IMAGE", { layer_id: l.id, crop: null }))}>Reset crop</button>}
      </Section>
      {pick && <AssetPicker campaignId={campaignId} title="Replace image" kinds={l.role === "logo" ? ["logos"] : ["products", "logos"]} onPick={a => dispatch(cmd("REPLACE_IMAGE", { layer_id: l.id, asset_id: a.id }))} onClose={() => setPick(false)} />}
    </>
  );
}

function ShapeProps({ l, off }: { l: ShapeLayer; off: boolean }) {
  return (
    <Section title="Shape">
      <Row label="Type"><Segmented label="Shape type" value={l.shape} disabled={off} options={[{ v: "rect", label: "Rectangle" }, { v: "ellipse", label: "Ellipse" }]} onChange={v => style(l.id, { shape: v })} /></Row>
      <Row label="Fill"><ColorField label="Fill" value={l.fill} disabled={off} onCommit={v => dispatch(cmd("CHANGE_COLOR", { layer_id: l.id, field: "fill", value: v }))} /></Row>
      <Row label="Stroke"><ColorField label="Stroke" value={l.stroke ?? "#000000"} disabled={off} onCommit={v => style(l.id, { stroke: v, stroke_width: l.stroke_width || 2 })} /></Row>
      <Row label="Stroke width"><NumberField label="Stroke width" value={l.stroke_width} min={0} max={100} disabled={off} onCommit={v => style(l.id, v === 0 ? { stroke_width: 0, stroke: null } : { stroke_width: v, stroke: l.stroke ?? "#000000" })} /></Row>
      {l.shape === "rect" && <Row label="Corner radius"><NumberField label="Shape radius" value={l.radius} min={0} max={2000} disabled={off} onCommit={v => style(l.id, { radius: v })} /></Row>}
    </Section>
  );
}

function BackgroundProps({ campaignId }: { campaignId: string }) {
  const doc = useEditor(s => s.doc)!; const bg = doc.background; const off = bg.locked; const [pick, setPick] = useState(false); const info = useEditor(s => (bg.asset_id ? s.assets[bg.asset_id] : undefined));
  const setBg = (b: Record<string, unknown>) => dispatch(cmd("CHANGE_BACKGROUND", { background: { ...bg, ...b } }));
  const scrim = bg.scrims[0];
  return (
    <>
      <Section title="Background">
        <Row label="Type"><Segmented label="Background type" value={bg.type} disabled={off} options={[{ v: "solid", label: "Solid" }, { v: "gradient", label: "Gradient" }, { v: "image", label: "Image", title: bg.asset_id ? "" : "Pick an image first" }]}
          onChange={v => v === "image" ? (bg.asset_id ? setBg({ type: "image" }) : setPick(true)) : setBg({ type: v, ...(v === "gradient" && !bg.gradient_to ? { gradient_to: "#000000" } : {}) })} /></Row>
        <Row label="Color"><ColorField label="Background color" value={bg.color} disabled={off} onCommit={v => dispatch(cmd("CHANGE_COLOR", { layer_id: BG, field: "color", value: v }))} /></Row>
        {bg.type === "gradient" && <><Row label="To color"><ColorField label="Gradient end color" value={bg.gradient_to ?? "#000000"} disabled={off} onCommit={v => dispatch(cmd("CHANGE_COLOR", { layer_id: BG, field: "gradient_to", value: v }))} /></Row>
          <Row label="Angle"><NumberField label="Gradient angle" value={bg.angle} min={0} max={360} disabled={off} onCommit={v => setBg({ angle: v })} /></Row></>}
        {bg.type === "image" && <>
          <div className="flex items-center gap-2">{/* eslint-disable-next-line @next/next/no-img-element */}{info?.url && <img src={info.url} alt="" className="h-12 w-16 rounded border border-line object-cover" />}<button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent" disabled={off} onClick={() => setPick(true)}>Replace image…</button></div>
          <Row label="Fit"><Segmented label="Background fit" value={bg.fit} disabled={off} options={[{ v: "cover", label: "Cover" }, { v: "contain", label: "Contain" }, { v: "fill", label: "Fill" }]} onChange={v => dispatch(cmd("UPDATE_STYLE", { layer_id: BG, changes: { fit: v } }))} /></Row></>}
      </Section>
      <Section title="Overlay">
        {scrim && scrim.fade === "none" ? <>
          <Row label="Color"><ColorField label="Overlay color" value={scrim.color} disabled={off} onCommit={v => dispatch(cmd("UPDATE_STYLE", { layer_id: BG, changes: { scrims: [{ ...scrim, color: v }, ...bg.scrims.slice(1)] } }))} /></Row>
          <Row label="Opacity %"><NumberField label="Overlay opacity" value={Math.round(scrim.opacity * 100)} min={0} max={100} disabled={off} onCommit={v => dispatch(cmd("UPDATE_STYLE", { layer_id: BG, changes: { scrims: [{ ...scrim, opacity: v / 100 }, ...bg.scrims.slice(1)] } }))} /></Row>
          <button className="text-xs text-accent2 underline" disabled={off} onClick={() => dispatch(cmd("UPDATE_STYLE", { layer_id: BG, changes: { scrims: bg.scrims.slice(1) } }))}>Remove overlay</button></>
          : <button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40" disabled={off || bg.scrims.length >= 4} onClick={() => dispatch(cmd("UPDATE_STYLE", { layer_id: BG, changes: { scrims: [{ color: "#000000", opacity: 0.35, x: 0, y: 0, w: null, h: null, fade: "none", solid_until: 0.7 }, ...bg.scrims] } }))}>Add colour overlay</button>}
        {scrim && scrim.fade !== "none" && <p className="text-xs text-zinc-500">This design uses a gradient scrim for text legibility; it stays as generated.</p>}
      </Section>
      <Section title="Layer"><div className="flex gap-1.5"><IconBtn label={bg.locked ? "Unlock background" : "Lock background"} active={bg.locked} onClick={() => dispatch(cmd("TOGGLE_LOCK", { layer_id: BG }))}>{bg.locked ? <Lock size={15} /> : <Unlock size={15} />}</IconBtn></div></Section>
      {pick && <AssetPicker campaignId={campaignId} title="Background image" kinds={["backgrounds", "products"]} onPick={a => setBg({ type: "image", asset_id: a.id })} onClose={() => setPick(false)} />}
    </>
  );
}

export function AlignTools({ ids }: { ids: string[] }) {
  const [to, setTo] = useState<"canvas" | "selection">(ids.length > 1 ? "selection" : "canvas");
  const al = (edge: string) => dispatch(cmd("ALIGN_ELEMENT", { layer_ids: ids, edge, to }));
  const dist = (axis: string) => dispatch(cmd("DISTRIBUTE_ELEMENTS", { layer_ids: ids, axis }));
  return (
    <Section title="Align & distribute">
      {ids.length > 1 && <Row label="Relative to"><Segmented label="Align relative to" value={to} options={[{ v: "selection", label: "Selection" }, { v: "canvas", label: "Canvas" }]} onChange={setTo} /></Row>}
      <div className="flex flex-wrap gap-1.5">
        <IconBtn label="Align left" onClick={() => al("left")}><AlignStartVertical size={15} /></IconBtn><IconBtn label="Align horizontal center" onClick={() => al("center")}><AlignVerticalJustifyCenter size={15} /></IconBtn><IconBtn label="Align right" onClick={() => al("right")}><AlignEndVertical size={15} /></IconBtn>
        <IconBtn label="Align top" onClick={() => al("top")}><AlignStartHorizontal size={15} /></IconBtn><IconBtn label="Align vertical middle" onClick={() => al("middle")}><AlignCenter size={15} className="rotate-90" /></IconBtn><IconBtn label="Align bottom" onClick={() => al("bottom")}><AlignEndHorizontal size={15} /></IconBtn>
        <IconBtn label="Distribute horizontally" disabled={ids.length < 3} onClick={() => dist("horizontal")}><AlignHorizontalSpaceAround size={15} /></IconBtn><IconBtn label="Distribute vertically" disabled={ids.length < 3} onClick={() => dist("vertical")}><AlignVerticalSpaceAround size={15} /></IconBtn>
      </div>
    </Section>
  );
}

export default function PropertiesPanel({ campaignId, onAi }: { campaignId: string; onAi: (layerId: string) => void }) {
  const doc = useEditor(s => s.doc)!; const sel = useEditor(s => s.selection); const readOnly = useEditor(s => s.readOnly);
  const layers = doc.elements.filter(e => sel.includes(e.id));
  if (sel.length === 0 || (sel.length === 1 && sel[0] === BG)) return <div aria-label="Properties"><BackgroundProps campaignId={campaignId} /><div className="p-3"><button className="btn w-full" disabled={readOnly || doc.background.locked} onClick={() => onAi(BG)}><Sparkles size={14} />AI edit background</button></div></div>;
  if (layers.length > 1) return <div aria-label="Properties"><p className="px-3 pt-3 text-xs text-zinc-400">{layers.length} layers selected</p><AlignTools ids={layers.map(l => l.id)} /></div>;
  const l = layers[0]; if (!l) return null; const off = l.locked || readOnly;
  const aiOk = !readOnly && !l.locked && !(l.type === "image" && l.role === "logo");
  return (
    <div aria-label="Properties">
      <div className="border-b border-line px-3 py-2 text-sm font-medium">{l.name || l.role || l.type} <span className="text-xs font-normal text-zinc-500">· {l.type}</span></div>
      {l.type === "text" && <TextProps l={l} off={off} />}{l.type === "button" && <ButtonProps l={l} off={off} />}{l.type === "image" && <ImageProps l={l} off={off} campaignId={campaignId} />}{l.type === "shape" && <ShapeProps l={l} off={off} />}
      <Geometry l={l} off={off} /><AlignTools ids={[l.id]} /><LayerActions l={l} />
      {l.type === "image" && (
        <Section title="Name"><input aria-label="Layer name" className="input !py-1 text-xs" disabled={off} value={l.name} maxLength={80} onChange={e => dispatch(cmd("UPDATE_STYLE", { layer_id: l.id, changes: { name: e.target.value } }), `name:${l.id}`)} /></Section>)}
      <div className="p-3"><button className="btn w-full" disabled={!aiOk} title={aiOk ? "" : "Unlock the layer first (logos are replaced from Brand assets)"} onClick={() => onAi(l.id)}><Sparkles size={14} />AI edit this layer</button></div>
    </div>
  );
}
