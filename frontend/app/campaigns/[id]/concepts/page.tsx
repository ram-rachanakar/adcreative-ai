"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { AlertTriangle, Check, Loader2 } from "lucide-react";
import clsx from "clsx";
import Shell from "@/components/Shell";
import { api, runJob } from "@/lib/api";
import { useSession } from "@/stores/session";
import type { Brief, Concept, Usage } from "@/types";

export default function ConceptsPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const orgId = useSession(s => s.orgId);
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const concepts = useQuery({ queryKey: ["concepts", id, orgId], queryFn: () => api<Concept[]>(`/campaigns/${id}/concepts`), enabled: !!orgId });
  const briefs = useQuery({ queryKey: ["briefs", id, orgId], queryFn: () => api<Brief[]>(`/campaigns/${id}/briefs`), enabled: !!orgId });
  const usage = useQuery({ queryKey: ["usage", id, orgId, concepts.data?.length], queryFn: () => api<Usage>(`/campaigns/${id}/usage`), enabled: !!orgId });
  const refresh = () => ["concepts", "briefs", "usage"].forEach(k => qc.invalidateQueries({ queryKey: [k, id] }));
  const generate = useMutation({ mutationFn: () => runJob(`/campaigns/${id}/concepts/generate`, { json: { count: 5 } }), onSuccess: () => { setPicked(null); refresh(); } });
  const selection = picked ?? new Set((concepts.data ?? []).filter(c => c.selected).map(c => c.id));
  const select = useMutation({ mutationFn: () => api(`/campaigns/${id}/concepts/select`, { method: "POST", json: { concept_ids: [...selection] } }), onSuccess: refresh });
  const makeBriefs = useMutation({ mutationFn: async () => { await api(`/campaigns/${id}/concepts/select`, { method: "POST", json: { concept_ids: [...selection] } }); return runJob(`/campaigns/${id}/briefs/generate`); }, onSuccess: refresh });
  const toggle = (cid: string) => { const n = new Set(selection); n.has(cid) ? n.delete(cid) : n.add(cid); setPicked(n); };
  const err = (generate.error ?? select.error ?? makeBriefs.error) as Error | null;

  return (
    <Shell>
      <Link href={`/campaigns/${id}`} className="text-xs text-zinc-400 hover:text-white">← Product review</Link>
      <div className="mb-6 mt-2 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Creative concepts</h1>
        <div className="flex gap-2">
          <button className="btn" disabled={generate.isPending} onClick={() => generate.mutate()}>
            {generate.isPending && <Loader2 className="animate-spin" size={16} />}{concepts.data?.length ? "Regenerate" : "Generate 5 concepts"}</button>
          <button className="btn" disabled={selection.size === 0 || makeBriefs.isPending} onClick={() => makeBriefs.mutate()}>
            {makeBriefs.isPending && <Loader2 className="animate-spin" size={16} />}Create briefs ({selection.size})</button>
        </div>
      </div>
      {err && <p className="err mb-4">{err.message}</p>}
      {usage.data && <p className="mb-4 text-xs text-zinc-500">LLM usage: {usage.data.by_kind.reduce((a, r) => a + r.input_tokens + r.output_tokens, 0)} tokens · cost {usage.data.fully_priced && usage.data.by_kind.length ? `$${usage.data.total_cost_usd.toFixed(4)}` : "not priced (set LLM cost rates)"}</p>}
      <p className="mb-4 text-xs text-zinc-500">Copy uses only facts from the reviewed product data. Anything the checker could not find in that data is flagged below.</p>
      <div className="grid gap-4 xl:grid-cols-2">
        {concepts.data?.map(c => <ConceptCard key={c.id + c.data.headline + c.status} c={c} chosen={selection.has(c.id)} onToggle={() => toggle(c.id)} onSaved={refresh} />)}
      </div>
      {(briefs.data?.length ?? 0) > 0 && (<section className="mt-10"><div className="mb-3 flex items-center justify-between"><h2 className="text-lg font-medium">Creative briefs</h2><Link href={`/campaigns/${id}/creatives`} className="btn">Generate banners →</Link></div>
        <div className="grid gap-4 xl:grid-cols-2">{briefs.data!.map(b => (
          <div key={b.id} className="card space-y-2 text-sm"><div className="label">Key message</div><p>{b.data.key_message}</p>
            <div className="label">Background prompt (no text / no product — those are rendered separately)</div><p className="text-zinc-300">{b.data.background_prompt_final}</p>
            <div className="label">Composition</div><p className="text-zinc-300">{b.data.composition_notes}</p></div>))}</div></section>)}
    </Shell>
  );
}

function ConceptCard({ c, chosen, onToggle, onSaved }: { c: Concept; chosen: boolean; onToggle: () => void; onSaved: () => void }) {
  const [edit, setEdit] = useState({ headline: c.data.headline, supporting_copy: c.data.supporting_copy, cta: c.data.cta });
  const dirty = edit.headline !== c.data.headline || edit.supporting_copy !== c.data.supporting_copy || edit.cta !== c.data.cta;
  const save = useMutation({ mutationFn: (ack: boolean) => api(`/concepts/${c.id}`, { method: "PATCH", json: { ...edit, acknowledge_flags: ack } }), onSuccess: onSaved });
  const blocked = c.status === "flagged";
  return (
    <div className={clsx("card space-y-3", chosen && "border-accent")}>
      <div className="flex items-start justify-between gap-3">
        <div><div className="text-xs uppercase tracking-wide text-accent2">{c.approach.replace(/_/g, " ")}</div><h3 className="font-medium">{c.data.name}</h3></div>
        <button onClick={onToggle} disabled={blocked} title={blocked ? "Resolve flagged claims first" : "Select"} aria-pressed={chosen}
          className={clsx("grid h-7 w-7 place-items-center rounded-md border", chosen ? "border-accent bg-accent" : "border-line", blocked && "opacity-40")}>{chosen && <Check size={14} />}</button>
      </div>
      <div className="flex gap-1">{c.data.color_palette.map(p => <span key={p} title={p} className="h-5 w-5 rounded border border-line" style={{ background: p }} />)}</div>
      <div><label className="label">Headline</label><input className="input" value={edit.headline} maxLength={80} onChange={e => setEdit({ ...edit, headline: e.target.value })} /></div>
      <div><label className="label">Supporting copy</label><textarea rows={2} className="input" value={edit.supporting_copy} maxLength={220} onChange={e => setEdit({ ...edit, supporting_copy: e.target.value })} /></div>
      <div><label className="label">CTA</label><input className="input" value={edit.cta} maxLength={30} onChange={e => setEdit({ ...edit, cta: e.target.value })} /></div>
      <p className="text-xs text-zinc-400"><b>Angle:</b> {c.data.advertising_angle}<br /><b>Visual:</b> {c.data.visual_direction}<br /><b>Audience:</b> {c.data.target_audience}</p>
      {c.data.assumptions.length > 0 && <p className="text-xs text-yellow-500">Assumptions: {c.data.assumptions.join("; ")}</p>}
      {c.flags.length > 0 && (<div className="rounded-lg border border-yellow-700/50 p-3 text-xs"><div className="mb-1 flex items-center gap-1 text-yellow-400"><AlertTriangle size={14} />Unsupported claims{c.status === "acknowledged" && " (acknowledged)"}</div>
        <ul className="list-disc pl-4 text-zinc-300">{c.flags.map((f, i) => <li key={i}><b>{f.text}</b> in {f.field}: {f.rule}</li>)}</ul></div>)}
      {(dirty || blocked) && (<div className="flex gap-2">
        <button className="btn" disabled={!dirty || save.isPending} onClick={() => save.mutate(false)}>Save &amp; re-check</button>
        {blocked && <button className="rounded-lg border border-line px-3 text-xs text-zinc-300 hover:text-white" disabled={save.isPending} onClick={() => save.mutate(true)}>I verified these claims</button>}</div>)}
      {save.error && <p className="err">{(save.error as Error).message}</p>}
    </div>
  );
}
