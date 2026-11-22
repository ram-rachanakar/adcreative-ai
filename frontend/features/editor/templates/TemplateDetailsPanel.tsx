"use client";
import { ArrowLeft, Copy, Star } from "lucide-react";
import { useState } from "react";
import { useTemplateActions, useTemplateDetails } from "@/hooks/templates";
import { ApiError } from "@/lib/api";
import { defaultFormat, sizeLabel } from "@/lib/templates/templateMappingClient";
import type { TemplateSummary } from "@/lib/templates/types";
import { Modal } from "../controls";
import TemplatePreview from "./TemplatePreview";
import TemplateVersionHistory from "./TemplateVersionHistory";

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
function Confirm({ title, body, action, onYes, onNo }: { title: string; body: string; action: string; onYes: () => void; onNo: () => void }) {
  return <Modal title={title} onClose={onNo}><p className="text-sm text-zinc-300">{body}</p><div className="mt-4 flex justify-end gap-2"><button className="rounded-lg border border-line px-4 py-2 text-sm" onClick={onNo}>Cancel</button><button className="btn !bg-red-600" onClick={onYes}>{action}</button></div></Modal>;
}

export default function TemplateDetailsPanel({ summary, bannerSize, canUse, onBack, onUse }: { summary: TemplateSummary; bannerSize?: { width: number; height: number }; canUse: boolean; onBack: () => void; onUse: (t: TemplateSummary) => void }) {
  const q = useTemplateDetails(summary.id); const a = useTemplateActions(); const t = q.data ?? summary;
  const fits = bannerSize ? t.supported_formats.find(f => f.width === bannerSize.width && f.height === bannerSize.height) : undefined;
  const [fmt, setFmt] = useState<string | undefined>(fits?.format_id ?? defaultFormat(t.supported_formats)); const [confirm, setConfirm] = useState<null | "archive">(null);
  const [editing, setEditing] = useState(false); const [name, setName] = useState(t.name); const [desc, setDesc] = useState(t.description); const [msg, setMsg] = useState<string | null>(null); const [showVersions, setShowVersions] = useState(false);
  const fail = (e: unknown) => setMsg(e instanceof ApiError ? e.message : "Something went wrong");
  return (
    <div className="space-y-3 p-3" data-testid="template-details">
      <button className="flex items-center gap-1 text-xs text-zinc-400 hover:text-white" onClick={onBack}><ArrowLeft size={13} />All templates</button>
      <div className="flex items-start justify-between gap-2"><div><h3 className="text-sm font-semibold">{t.name}</h3><p className="text-[11px] text-zinc-400">{t.owner} · v{t.current_version} · {t.status}</p></div>
        <button aria-label={t.favorite ? "Remove from favorites" : "Add to favorites"} aria-pressed={t.favorite} onClick={() => a.favorite.mutate({ id: t.id, on: !t.favorite })} className="rounded p-1 text-zinc-300 hover:text-white"><Star size={16} className={t.favorite ? "fill-amber-400 text-amber-400" : ""} /></button></div>
      <p className="text-xs text-zinc-300">{t.description}</p>
      <TemplatePreview templateId={t.id} formats={t.supported_formats} value={fmt} onChange={setFmt} highlight={fits?.format_id} />
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]"><dt className="text-zinc-500">Industry</dt><dd>{cap(t.category)}</dd><dt className="text-zinc-500">Style</dt><dd>{t.style_tags.map(cap).join(", ") || "–"}</dd>
        <dt className="text-zinc-500">Layout</dt><dd>{t.layout_tags.map(cap).join(", ") || "–"}</dd><dt className="text-zinc-500">Modes</dt><dd>{t.application_modes.length} available</dd></dl>
      {bannerSize && (fits ? <p className="text-[11px] text-emerald-300" role="status">Has a dedicated {sizeLabel(fits)} layout for this banner.</p>
        : <p className="text-[11px] text-amber-300" role="status">No {bannerSize.width}×{bannerSize.height} layout: unavailable for this banner.</p>)}
      {msg && <p className="err" role="alert">{msg}</p>}
      <div className="flex flex-wrap gap-2">
        {canUse && <button className="btn !py-1.5 text-xs" disabled={(bannerSize && !fits) || t.status === "archived"} onClick={() => onUse(t)}>Use this template</button>}
        <button className="inline-flex items-center gap-1 rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent" disabled={a.duplicate.isPending} onClick={() => a.duplicate.mutate({ id: t.id, visibility: "personal" }, { onSuccess: () => setMsg("Copied to My templates."), onError: fail })}><Copy size={12} />Duplicate</button>
        {t.can_edit && <button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent" onClick={() => setEditing(!editing)}>Edit details</button>}
        {t.can_edit && t.status === "draft" && <button className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent" onClick={() => a.publish.mutate(t.id, { onError: fail })}>Publish</button>}
        {t.can_edit && t.status !== "archived" && <button className="rounded-lg border border-red-500/50 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/10" onClick={() => setConfirm("archive")}>Archive</button>}
      </div>
      {editing && <form className="space-y-2 rounded-lg border border-line p-2" onSubmit={e => { e.preventDefault(); a.patch.mutate({ id: t.id, json: { name, description: desc } }, { onSuccess: () => { setEditing(false); setMsg("Saved."); }, onError: fail }); }}>
        <label className="label" htmlFor="tpl-edit-name">Name</label><input id="tpl-edit-name" className="input" value={name} onChange={e => setName(e.target.value)} maxLength={200} required />
        <label className="label" htmlFor="tpl-edit-desc">Description</label><textarea id="tpl-edit-desc" className="input" rows={2} value={desc} onChange={e => setDesc(e.target.value)} maxLength={1000} />
        <button className="btn !py-1 text-xs" disabled={a.patch.isPending}>Save changes</button></form>}
      <button className="text-xs text-accent2 hover:underline" aria-expanded={showVersions} onClick={() => setShowVersions(!showVersions)}>{showVersions ? "Hide" : "Show"} version history</button>
      {showVersions && <TemplateVersionHistory templateId={t.id} />}
      {confirm === "archive" && <Confirm title="Archive this template?" body="Archived templates disappear from the library and can’t be applied again. Banners already made from it are not affected." action="Archive" onNo={() => setConfirm(null)} onYes={() => a.archive.mutate(t.id, { onSuccess: () => { setConfirm(null); onBack(); }, onError: e => { setConfirm(null); fail(e); } })} />}
    </div>
  );
}
