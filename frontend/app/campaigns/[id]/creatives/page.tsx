"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { AlertTriangle, Heart, Pencil, RefreshCw } from "lucide-react";
import clsx from "clsx";
import Shell from "@/components/Shell";
import FormatPicker, { type Selection } from "@/features/creatives/FormatPicker";
import JobPanel from "@/features/creatives/JobPanel";
import ExportPanel from "@/features/creatives/ExportPanel";
import StartFromTemplateDialog from "@/features/editor/templates/StartFromTemplateDialog";
import QualityPanel, { QualityBadge } from "@/features/creatives/QualityPanel";
import { api } from "@/lib/api";
import { filterCreatives, platformLabel, type Filters } from "@/lib/creatives";
import { useSession } from "@/stores/session";
import type { Concept, Creative, FormatRegistry, Job, QualitySummary } from "@/types";

export default function CreativesPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const [showTpl, setShowTpl] = useState(false);
  const orgId = useSession(s => s.orgId);
  const [sel, setSel] = useState<Selection>({ formatIds: new Set(["instagram_feed_square_1080x1080", "google_display_medium_rectangle_300x250", "google_display_leaderboard_728x90"]), custom: [] });
  const [jobId, setJobId] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [checking, setChecking] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<Filters>({ platform: "all", concept: "all", favoritesOnly: false, attentionOnly: false, quality: "all" });
  const list = useQuery({ queryKey: ["creatives", id, orgId], queryFn: () => api<Creative[]>(`/campaigns/${id}/creatives`), enabled: !!orgId });
  const concepts = useQuery({ queryKey: ["concepts", id, orgId], queryFn: () => api<Concept[]>(`/campaigns/${id}/concepts`), enabled: !!orgId });
  const reg = useQuery({ queryKey: ["formats"], queryFn: () => api<FormatRegistry>("/formats"), staleTime: Infinity });
  const jobs = useQuery({ queryKey: ["jobs", id, orgId], queryFn: () => api<Job[]>(`/campaigns/${id}/jobs`), enabled: !!orgId });
  const summary = useQuery({ queryKey: ["quality-summary", id, orgId], queryFn: () => api<QualitySummary>(`/campaigns/${id}/quality`), enabled: !!orgId });
  const checkAll = useMutation({ mutationFn: () => api<Job>(`/campaigns/${id}/quality`, { method: "POST", json: { auto_correct: false } }), onSuccess: j => { setJobId(j.id); refresh(); } });
  const activeJob = jobId ?? jobs.data?.find(j => ["pending", "generating_assets", "composing", "rendering", "validating"].includes(j.status))?.id ?? null;
  const refresh = () => { qc.invalidateQueries({ queryKey: ["creatives", id] }); qc.invalidateQueries({ queryKey: ["jobs", id] }); qc.invalidateQueries({ queryKey: ["usage", id] }); qc.invalidateQueries({ queryKey: ["quality-summary", id] }); };
  const generate = useMutation({
    mutationFn: (regen: boolean) => api<Job>(`/campaigns/${id}/creatives/generate`, { method: "POST", json: { format_ids: [...sel.formatIds], sizes: sel.custom, regenerate_background: regen } }),
    onSuccess: j => { setJobId(j.id); refresh(); },
  });
  const regenerate = useMutation({ mutationFn: (v: { id: string; bg: boolean }) => api<Job>(`/creatives/${v.id}/regenerate?new_background=${v.bg}`, { method: "POST" }), onSuccess: j => { setJobId(j.id); refresh(); } });
  const fav = useMutation({ mutationFn: (c: Creative) => api(`/creatives/${c.id}`, { method: "PATCH", json: { favorite: !c.favorite } }), onSuccess: refresh });
  const shown = filterCreatives(list.data ?? [], filters);
  const platforms = (reg.data?.platforms ?? []).filter(p => (list.data ?? []).some(c => c.format_keys.some(k => k.startsWith(p + "_"))));
  const conceptName = (cid: string) => concepts.data?.find(c => c.id === cid)?.data.name ?? "Concept";
  const busy = generate.isPending || !!(activeJob && jobs.data?.find(j => j.id === activeJob && ["pending", "generating_assets", "composing", "rendering", "validating"].includes(j.status)));
  const total = sel.formatIds.size + sel.custom.length;
  return (
    <Shell>
      <Link href={`/campaigns/${id}/concepts`} className="text-xs text-zinc-400 hover:text-white">← Concepts</Link>
      <div className="mb-6 mt-2 flex items-center justify-between"><h1 className="text-2xl font-semibold">Banners</h1>
        <button className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent" onClick={() => setShowTpl(true)}>Start from a template</button></div>
      {showTpl && <StartFromTemplateDialog campaignId={id} onClose={() => setShowTpl(false)} />}
      <div className="grid gap-6 xl:grid-cols-[420px_1fr]">
        <div className="space-y-4">
          <FormatPicker value={sel} onChange={setSel} />
          <div className="flex gap-2">
            <button className="btn flex-1" disabled={busy || total === 0} onClick={() => generate.mutate(false)}>Generate {total} size{total === 1 ? "" : "s"}</button>
            {!!list.data?.length && <button className="rounded-lg border border-line px-3 text-sm text-zinc-300 hover:text-white" disabled={busy || total === 0} onClick={() => generate.mutate(true)}>New backgrounds</button>}
          </div>
          {generate.error && <p className="err">{(generate.error as Error).message}</p>}
          <p className="text-xs text-zinc-500">Backgrounds are made once per concept and reused for every size. Text, logo, product and button are always rendered by the layout engine, never by the image model.</p>
        </div>
        <div className="space-y-4">
          {activeJob && <JobPanel jobId={activeJob} onFinished={refresh} />}
          {!!list.data?.length && (<>
            <div className="flex flex-wrap items-center gap-3 text-xs">
              <select aria-label="Platform filter" className="input w-auto" value={filters.platform} onChange={e => setFilters({ ...filters, platform: e.target.value })}>
                <option value="all">All platforms</option>{platforms.map(p => <option key={p} value={p}>{platformLabel(p)}</option>)}<option value="custom">Custom sizes</option></select>
              <select aria-label="Concept filter" className="input w-auto" value={filters.concept} onChange={e => setFilters({ ...filters, concept: e.target.value })}>
                <option value="all">All concepts</option>{[...new Set(list.data.map(c => c.concept_id))].map(c => <option key={c} value={c}>{conceptName(c)}</option>)}</select>
              <label className="flex items-center gap-1"><input type="checkbox" checked={filters.favoritesOnly} onChange={e => setFilters({ ...filters, favoritesOnly: e.target.checked })} />Favourites</label>
              <label className="flex items-center gap-1"><input type="checkbox" checked={filters.attentionOnly} onChange={e => setFilters({ ...filters, attentionOnly: e.target.checked })} />Needs attention</label>
              <select aria-label="Quality filter" className="input w-auto" value={filters.quality} onChange={e => setFilters({ ...filters, quality: e.target.value as Filters["quality"] })}>
                <option value="all">Any quality</option><option value="pass">Passed</option><option value="warn">Warnings</option><option value="fail">Failed</option><option value="unassessed">Not checked</option></select>
              <button className="rounded-lg border border-line px-2 py-1 text-zinc-300 hover:text-white" disabled={busy || checkAll.isPending} onClick={() => checkAll.mutate()}>Check all</button>
              <span className="text-zinc-500">{shown.length} of {list.data.length}{summary.data && summary.data.total > 0 && ` · avg ${summary.data.average_score ?? "–"} · ${summary.data.counts.pass} pass / ${summary.data.counts.warn} warn / ${summary.data.counts.fail} fail${summary.data.counts.unassessed ? ` / ${summary.data.counts.unassessed} unchecked` : ""}`}</span>
              {checkAll.error && <span className="err">{(checkAll.error as Error).message}</span>}
            </div>
            <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
              {shown.map(c => (
                <div key={c.id} className={clsx("card space-y-2", picked.has(c.id) && "border-accent")}>
                  {c.url && /* eslint-disable-next-line @next/next/no-img-element */ <img src={c.url} alt={`${conceptName(c.concept_id)} ${c.width}x${c.height}`} className="w-full rounded-lg border border-line object-contain" style={{ aspectRatio: `${c.width}/${c.height}`, maxHeight: 360 }} />}
                  <div className="flex items-center justify-between text-xs text-zinc-400">
                    <label className="flex items-center gap-2"><input type="checkbox" aria-label={`Select ${c.width}x${c.height}`} checked={picked.has(c.id)} onChange={e => { const n = new Set(picked); e.target.checked ? n.add(c.id) : n.delete(c.id); setPicked(n); }} />{c.width}×{c.height} · v{c.spec_version}</label>
                    <div className="flex items-center gap-2">
                      <button aria-label="Favourite" onClick={() => fav.mutate(c)} className={c.favorite ? "text-pink-400" : "hover:text-white"}><Heart size={14} fill={c.favorite ? "currentColor" : "none"} /></button>
                      <button aria-label="Regenerate" title="Regenerate this banner" disabled={busy} onClick={() => regenerate.mutate({ id: c.id, bg: false })} className="hover:text-white"><RefreshCw size={14} /></button>
                      <Link aria-label="Open editor" href={`/campaigns/${id}/editor/${c.id}`} className="rounded border border-line px-1.5 text-[11px] hover:text-white">Open editor</Link>
                      <button aria-label="Edit text" onClick={() => setEditing(editing === c.id ? null : c.id)} className="hover:text-white"><Pencil size={14} /></button></div></div>
                  <p className="text-[11px] text-zinc-500">{conceptName(c.concept_id)}{c.format_keys.length > 0 && ` · ${c.format_keys.length} placement${c.format_keys.length > 1 ? "s" : ""}`}</p>
                  <p className="text-[11px]"><button className="hover:underline" onClick={() => setChecking(checking === c.id ? null : c.id)}><QualityBadge score={c.quality.score} status={c.quality.status} stale={c.quality.stale} /></button></p>
                  {checking === c.id && <QualityPanel id={c.id} onChanged={refresh} />}
                  {c.status === "needs_attention" && <p className="flex items-center gap-1 text-xs text-yellow-400"><AlertTriangle size={14} />{c.render_report.elements?.filter(e => e.text_overflow || e.out_of_bounds).map(e => `${e.id} ${e.text_overflow ? "text overflows" : "off canvas"}`).join(", ") || "Layout problem"}</p>}
                  {editing === c.id && <TextEditor id={c.id} onSaved={() => { setEditing(null); refresh(); }} />}
                  {c.url && <div className="flex gap-3 text-xs text-accent">{["png", "jpeg", "webp", "svg"].map(f => <DownloadLink key={f} id={c.id} fmt={f} />)}</div>}
                </div>))}
            </div>
            <ExportPanel campaignId={id} selected={[...picked]} total={list.data.length} />
          </>)}
        </div>
      </div>
    </Shell>
  );
}

function DownloadLink({ id, fmt }: { id: string; fmt: string }) {
  const { orgId } = useSession.getState();
  const get = async () => {
    const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    const csrf = useSession.getState().me?.csrf_token ?? "";
    const r = await fetch(`${base}/creatives/${id}/download?format=${fmt}`, { credentials: "include", headers: { "x-org-id": orgId ?? "", "x-csrf-token": csrf } });
    if (!r.ok) return alert((await r.json().catch(() => ({ detail: "Download failed" }))).detail);
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = /filename="([^"]+)"/.exec(r.headers.get("content-disposition") ?? "")?.[1] ?? `banner.${fmt}`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return <button onClick={get} className="uppercase hover:underline">{fmt}</button>;
}

function TextEditor({ id, onSaved }: { id: string; onSaved: () => void }) {
  const detail = useQuery({ queryKey: ["creative", id], queryFn: () => api<Creative>(`/creatives/${id}`) });
  const [v, setV] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: async () => {
      const spec = structuredClone(detail.data!.spec!);
      for (const el of spec.elements) if (el.id in v) { if (el.type === "button") el.label = v[el.id]; else el.text = v[el.id]; }
      return api(`/creatives/${id}/spec`, { method: "PUT", json: spec });  // deterministic re-render; no AI call
    }, onSuccess: onSaved,
  });
  if (!detail.data?.spec) return <p className="text-xs text-zinc-500">Loading…</p>;
  const fields = detail.data.spec.elements.filter(e => ["headline", "body", "cta"].includes(e.role));
  return (
    <div className="space-y-2">
      {fields.map(e => <div key={e.id}><label className="label">{e.role}</label>
        <input className="input" defaultValue={String(e.text ?? e.label ?? "")} maxLength={e.type === "button" ? 30 : 300} onChange={ev => setV({ ...v, [e.id]: ev.target.value })} /></div>)}
      {save.error && <p className="err">{(save.error as Error).message}</p>}
      <button className="btn" disabled={!Object.keys(v).length || save.isPending} onClick={() => save.mutate()}>Re-render (free, no AI)</button>
    </div>
  );
}
