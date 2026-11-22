"use client";
import { Search, X } from "lucide-react";
import { useEffect, useState } from "react";

/** Debounced search box: filters by name, description, industry, style, layout type and supported format/dimensions. */
export default function TemplateSearch({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [txt, setTxt] = useState(value);
  useEffect(() => setTxt(value), [value]);
  useEffect(() => { const t = setTimeout(() => txt !== value && onChange(txt), 250); return () => clearTimeout(t); }, [txt]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="relative">
      <Search size={14} className="pointer-events-none absolute left-2.5 top-2.5 text-zinc-500" />
      <input aria-label="Search templates" placeholder="Search name, style, industry, size…" className="input !py-1.5 !pl-8 !pr-7 text-xs" value={txt} onChange={e => setTxt(e.target.value)} maxLength={100} />
      {txt && <button aria-label="Clear search" className="absolute right-2 top-2 text-zinc-500 hover:text-white" onClick={() => { setTxt(""); onChange(""); }}><X size={14} /></button>}
    </div>
  );
}
