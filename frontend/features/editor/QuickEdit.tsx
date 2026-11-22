"use client";
import { useState } from "react";
import { AlignCenter, AlignLeft, AlignRight, Sparkles } from "lucide-react";
import { cmd } from "@/lib/editor/commands";
import { BG, type ButtonLayer, type ImageLayer, type TextLayer } from "@/lib/editor/types";
import { useEditor } from "@/stores/editor";
import AssetPicker, { useEditorAssets } from "./AssetPicker";
import { ColorField, NumberField, Row, Section, Segmented } from "./controls";

const send = (c: ReturnType<typeof cmd>, key?: string) => useEditor.getState().dispatch(c, key ? { coalesceKey: key } : {});

/** Non-designer view: everything a marketer wants to change, found by role, with no layers, handles or jargon. */
export default function QuickEdit({ campaignId, onAi }: { campaignId: string; onAi: (id: string) => void }) {
  const doc = useEditor(s => s.doc)!; const ro = useEditor(s => s.readOnly); const brand = useEditorAssets(campaignId).data?.brand.colors ?? [];
  const [pick, setPick] = useState<null | "product" | "logo" | "background">(null);
  const byRole = <T,>(role: string, type: string) => doc.elements.find(e => e.role === role && e.type === type) as T | undefined;
  const head = byRole<TextLayer>("headline", "text"), body = byRole<TextLayer>("body", "text"), cta = byRole<ButtonLayer>("cta", "button"), prod = byRole<ImageLayer>("product", "image"), logo = byRole<ImageLayer>("logo", "image");
  const textBlock = (l: TextLayer | undefined, title: string) => l && (
    <Section title={title}>
      <textarea aria-label={`${title} text`} className="input min-h-[56px] text-sm" disabled={ro || l.locked} maxLength={300} value={l.text} onChange={e => send(cmd("UPDATE_TEXT", { layer_id: l.id, text: e.target.value }), `text:${l.id}`)} />
      <Row label="Size"><NumberField label={`${title} size`} value={l.font_size} min={6} max={600} disabled={ro || l.locked} onCommit={v => send(cmd("UPDATE_STYLE", { layer_id: l.id, changes: { font_size: v, ...(l.min_font_size > v ? { min_font_size: v } : {}) } }))} /></Row>
      <Row label="Weight"><Segmented label={`${title} weight`} value={l.font_weight} disabled={ro || l.locked} options={[{ v: 400, label: "Regular" }, { v: 700, label: "Bold" }]} onChange={v => send(cmd("UPDATE_STYLE", { layer_id: l.id, changes: { font_weight: v } }))} /></Row>
      <Row label="Align"><Segmented label={`${title} alignment`} value={l.align} disabled={ro || l.locked} options={[{ v: "left", label: <AlignLeft size={14} /> }, { v: "center", label: <AlignCenter size={14} /> }, { v: "right", label: <AlignRight size={14} /> }]} onChange={v => send(cmd("UPDATE_STYLE", { layer_id: l.id, changes: { align: v } }))} /></Row>
      <Row label="Color"><ColorField label={`${title} color`} value={l.color} disabled={ro || l.locked} onCommit={v => send(cmd("CHANGE_COLOR", { layer_id: l.id, field: "color", value: v }))} /></Row>
      <button className="text-xs text-accent2 underline disabled:opacity-40" disabled={ro || l.locked} onClick={() => onAi(l.id)}>Suggest alternative wording…</button>
    </Section>);
  return (
    <div aria-label="Quick edit">
      {textBlock(head, "Headline")}{textBlock(body, "Body copy")}
      {cta && <Section title="Button">
        <Row label="Text"><input aria-label="Button text" className="input !w-44 !py-1 text-sm" maxLength={30} disabled={ro || cta.locked} value={cta.label} onChange={e => send(cmd("UPDATE_TEXT", { layer_id: cta.id, text: e.target.value }), `text:${cta.id}`)} /></Row>
        <Row label="Button color"><ColorField label="Button color" value={cta.fill} disabled={ro || cta.locked} onCommit={v => send(cmd("CHANGE_COLOR", { layer_id: cta.id, field: "fill", value: v }))} /></Row>
        <Row label="Label color"><ColorField label="Button label color" value={cta.text_color} disabled={ro || cta.locked} onCommit={v => send(cmd("CHANGE_COLOR", { layer_id: cta.id, field: "text_color", value: v }))} /></Row>
      </Section>}
      <Section title="Images">
        {prod && <div className="flex gap-2"><button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40" disabled={ro || prod.locked} onClick={() => setPick("product")}>Replace product image…</button><button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40" disabled={ro || prod.locked} onClick={() => onAi(prod.id)}><Sparkles size={12} className="mr-1 inline" />Re-process</button></div>}
        {logo && <button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40" disabled={ro || logo.locked} onClick={() => setPick("logo")}>Replace logo…</button>}
        <div className="flex gap-2"><button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40" disabled={ro || doc.background.locked} onClick={() => setPick("background")}>Replace background…</button><button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-40" disabled={ro || doc.background.locked} onClick={() => onAi(BG)}><Sparkles size={12} className="mr-1 inline" />New background with AI</button></div>
      </Section>
      <Section title="Brand colors">
        {brand.length ? <><div className="flex flex-wrap gap-2">{brand.map(c => <button key={c} aria-label={`Use ${c} for background`} title="Set as background colour" disabled={ro || doc.background.locked} onClick={() => send(cmd("CHANGE_BACKGROUND", { background: { ...doc.background, type: "solid", color: c } }))} className="h-8 w-8 rounded-full border border-white/20" style={{ background: c }} />)}</div>
          {cta && <div className="flex flex-wrap gap-2"><span className="w-full text-[11px] text-zinc-500">Button</span>{brand.map(c => <button key={c} aria-label={`Use ${c} for button`} disabled={ro || cta.locked} onClick={() => send(cmd("CHANGE_COLOR", { layer_id: cta.id, field: "fill", value: c }))} className="h-6 w-6 rounded-md border border-white/20" style={{ background: c }} />)}</div>}</>
          : <p className="text-xs text-zinc-500">No brand colours yet.</p>}
      </Section>
      <p className="p-3 text-xs text-zinc-500">Need to move things around, rotate or work with layers? Switch to <b>Advanced</b> at the top.</p>
      {pick && <AssetPicker campaignId={campaignId} title={pick === "logo" ? "Replace logo" : pick === "background" ? "Replace background" : "Replace product image"} kinds={pick === "logo" ? ["logos"] : pick === "background" ? ["backgrounds", "products"] : ["products"]}
        onPick={a => pick === "product" && prod ? send(cmd("REPLACE_IMAGE", { layer_id: prod.id, asset_id: a.id })) : pick === "logo" && logo ? send(cmd("REPLACE_IMAGE", { layer_id: logo.id, asset_id: a.id })) : send(cmd("CHANGE_BACKGROUND", { background: { ...doc.background, type: "image", asset_id: a.id } }))} onClose={() => setPick(null)} />}
    </div>
  );
}
