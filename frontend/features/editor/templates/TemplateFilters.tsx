"use client";
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { ASPECTS, EMPTY_FILTERS, INDUSTRIES, LAYOUTS, STYLES, type Filters } from "@/lib/templates/types";

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const Sel = ({ label, value, options, onChange }: { label: string; value: string; options: readonly string[] | { v: string; label: string }[]; onChange: (v: string) => void }) => (
  <label className="block text-[11px] text-zinc-400">{label}
    <select aria-label={label} className="input mt-0.5 !py-1 text-xs" value={value} onChange={e => onChange(e.target.value)}>
      <option value="">Any</option>{(options as (string | { v: string; label: string })[]).map(o => typeof o === "string" ? <option key={o} value={o}>{cap(o)}</option> : <option key={o.v} value={o.v}>{o.label}</option>)}</select></label>
);

/** compatibleSize: the banner's own size when restyling an existing banner (enables the "fits this banner" filter). */
export default function TemplateFilters({ filters, onChange, compatibleSize }: { filters: Filters; onChange: (f: Filters) => void; compatibleSize?: string }) {
  const [open, setOpen] = useState(false); const set = (p: Partial<Filters>) => onChange({ ...filters, ...p });
  const active = (["industry", "style", "layout", "aspect", "owner", "source", "dimensions"] as const).filter(k => filters[k]).length + Number(filters.favorites) + Number(filters.compatibleOnly);
  return (
    <div>
      <button className="flex items-center gap-1 text-xs text-zinc-300 hover:text-white" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}Filters{active ? ` (${active})` : ""}</button>
      {open && <div className="mt-2 grid grid-cols-2 gap-2" role="group" aria-label="Template filters">
        <Sel label="Industry" value={filters.industry} options={INDUSTRIES} onChange={v => set({ industry: v })} />
        <Sel label="Style" value={filters.style} options={STYLES} onChange={v => set({ style: v })} />
        <Sel label="Layout" value={filters.layout} options={LAYOUTS} onChange={v => set({ layout: v })} />
        <Sel label="Aspect ratio" value={filters.aspect} options={ASPECTS} onChange={v => set({ aspect: v })} />
        <Sel label="Owner" value={filters.owner} options={[{ v: "system", label: "Built-in" }, { v: "organization", label: "Organization" }, { v: "personal", label: "Mine" }]} onChange={v => set({ owner: v })} />
        <Sel label="Source" value={filters.source} options={[{ v: "built_in", label: "Built-in" }, { v: "saved_from_design", label: "Saved from a design" }, { v: "duplicated", label: "Duplicated" }, { v: "manual", label: "Hand-made" }]} onChange={v => set({ source: v })} />
        <label className="col-span-2 block text-[11px] text-zinc-400">Exact dimensions
          <input aria-label="Exact dimensions" placeholder="e.g. 300x250" className="input mt-0.5 !py-1 text-xs" value={filters.dimensions} onChange={e => set({ dimensions: e.target.value.trim() })} /></label>
        <label className="col-span-2 flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" checked={filters.favorites} onChange={e => set({ favorites: e.target.checked })} />Favorites only</label>
        {compatibleSize && <label className="col-span-2 flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" checked={filters.compatibleOnly} onChange={e => set({ compatibleOnly: e.target.checked })} />Only templates for {compatibleSize.replace("x", "×")}</label>}
        <button className="col-span-2 text-left text-xs text-accent2 hover:underline" onClick={() => onChange({ ...EMPTY_FILTERS, q: filters.q, tab: filters.tab })}>Clear filters</button>
      </div>}
    </div>
  );
}
