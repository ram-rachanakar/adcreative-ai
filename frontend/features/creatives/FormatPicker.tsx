"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Plus, Save, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { groupByPlatform, parseCustomSize, platformLabel, toggleMany } from "@/lib/creatives";
import type { FormatRegistry, Preset } from "@/types";

export interface Selection { formatIds: Set<string>; custom: { width: number; height: number }[] }

export default function FormatPicker({ value, onChange }: { value: Selection; onChange: (s: Selection) => void }) {
  const qc = useQueryClient();
  const reg = useQuery({ queryKey: ["formats"], queryFn: () => api<FormatRegistry>("/formats"), staleTime: Infinity });
  const presets = useQuery({ queryKey: ["presets"], queryFn: () => api<Preset[]>("/format-presets") });
  const [custom, setCustom] = useState("");
  const [err, setErr] = useState("");
  const [name, setName] = useState("");
  const savePreset = useMutation({
    mutationFn: () => api<Preset>("/format-presets", { method: "POST", json: { name, format_ids: [...value.formatIds], custom_sizes: value.custom } }),
    onSuccess: () => { setName(""); qc.invalidateQueries({ queryKey: ["presets"] }); },
  });
  const delPreset = useMutation({ mutationFn: (id: string) => api(`/format-presets/${id}`, { method: "DELETE" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["presets"] }) });
  if (!reg.data) return <p className="text-sm text-zinc-500">Loading formats…</p>;
  const all = reg.data.formats.map(f => f.id);
  const set = (ids: string[], on: boolean) => onChange({ ...value, formatIds: toggleMany(value.formatIds, ids, on) });
  const addCustom = () => {
    const r = parseCustomSize(custom, reg.data!.custom_limits.min, reg.data!.custom_limits.max);
    if (typeof r === "string") return setErr(r);
    setErr(""); setCustom("");
    if (!value.custom.some(c => c.width === r.width && c.height === r.height)) onChange({ ...value, custom: [...value.custom, r] });
  };
  const total = value.formatIds.size + value.custom.length;
  return (
    <div className="card space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-medium">Banner sizes <span className="text-xs text-zinc-500">({total} selected)</span></h2>
        <div className="flex gap-2 text-xs">
          <button className="text-accent" onClick={() => set(all, true)}>Select all</button>
          <button className="text-zinc-400" onClick={() => onChange({ formatIds: new Set(), custom: [] })}>Clear</button>
        </div>
      </div>
      <p className="flex items-start gap-1 text-xs text-yellow-500"><AlertTriangle size={14} className="mt-0.5 shrink-0" />{reg.data.note}</p>
      {presets.data && presets.data.length > 0 && (
        <div className="flex flex-wrap gap-2">{presets.data.map(p => (
          <span key={p.id} className="flex items-center gap-1 rounded-full border border-line px-3 py-1 text-xs">
            <button onClick={() => onChange({ formatIds: new Set(p.format_ids), custom: p.custom_sizes })}>{p.name}</button>
            <button aria-label={`Delete preset ${p.name}`} onClick={() => delPreset.mutate(p.id)} className="text-zinc-500 hover:text-red-400"><Trash2 size={12} /></button>
          </span>))}</div>)}
      {groupByPlatform(reg.data.formats).map(([plat, fs]) => {
        const ids = fs.map(f => f.id);
        const allOn = ids.every(i => value.formatIds.has(i));
        return (
          <fieldset key={plat}>
            <legend className="mb-1 flex w-full items-center gap-2 text-xs font-medium uppercase tracking-wide text-zinc-400">
              <label className="flex items-center gap-2"><input type="checkbox" checked={allOn} onChange={e => set(ids, e.target.checked)} />{platformLabel(plat)}</label></legend>
            <div className="grid gap-1 sm:grid-cols-2">{fs.map(f => (
              <label key={f.id} className="flex items-start gap-2 rounded-md px-2 py-1 text-sm hover:bg-white/5">
                <input type="checkbox" className="mt-1" checked={value.formatIds.has(f.id)} onChange={e => set([f.id], e.target.checked)} />
                <span>{f.name} <span className="text-zinc-500">{f.width}×{f.height}</span>
                  {f.eligibility_notes && <span className="block text-[11px] text-yellow-500">{f.eligibility_notes}</span>}
                  {f.max_file_kb && <span className="block text-[11px] text-zinc-500">max {f.max_file_kb} KB</span>}</span>
              </label>))}</div>
          </fieldset>);
      })}
      <div>
        <div className="label">Custom size</div>
        <div className="flex gap-2"><input className="input max-w-[180px]" placeholder="1080x1350" value={custom} onChange={e => setCustom(e.target.value)} onKeyDown={e => e.key === "Enter" && addCustom()} />
          <button className="btn" onClick={addCustom}><Plus size={14} />Add</button></div>
        {err && <p className="err">{err}</p>}
        <div className="mt-2 flex flex-wrap gap-2">{value.custom.map(c => (
          <button key={`${c.width}x${c.height}`} className="rounded-full border border-line px-3 py-1 text-xs hover:border-red-400" title="Remove"
            onClick={() => onChange({ ...value, custom: value.custom.filter(x => x !== c) })}>{c.width}×{c.height} ✕</button>))}</div>
      </div>
      <div className="flex gap-2 border-t border-line pt-3"><input className="input max-w-[200px]" placeholder="Preset name" value={name} onChange={e => setName(e.target.value)} />
        <button className="btn" disabled={!name.trim() || total === 0 || savePreset.isPending} onClick={() => savePreset.mutate()}><Save size={14} />Save preset</button></div>
      {savePreset.error && <p className="err">{(savePreset.error as Error).message}</p>}
    </div>
  );
}
