"use client";
import { useState } from "react";
import { useTemplateActions, useTemplates } from "@/hooks/templates";
import { ApiError } from "@/lib/api";
import { EMPTY_FILTERS, type Filters, type TemplateSummary } from "@/lib/templates/types";
import { useEditor } from "@/stores/editor";
import TemplateApplicationDialog, { type Target } from "./TemplateApplicationDialog";
import TemplateCard from "./TemplateCard";
import TemplateCategoryTabs from "./TemplateCategoryTabs";
import TemplateDetailsPanel from "./TemplateDetailsPanel";
import TemplateFilters from "./TemplateFilters";
import TemplateSearch from "./TemplateSearch";

/** Template library. target.kind = "document": restyle the open banner (editor left panel); "campaign": start a new banner (picker dialog). */
export default function TemplatesPanel({ target, canUse = true, onDone }: { target: Target; canUse?: boolean; onDone?: () => void }) {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS); const [open, setOpen] = useState<TemplateSummary | null>(null); const [using, setUsing] = useState<TemplateSummary | null>(null);
  const docTarget = target.kind === "document" ? target : null; const readOnly = useEditor(s => s.readOnly);
  const q = useTemplates(filters, { documentId: docTarget?.documentId }); const a = useTemplateActions();
  const size = docTarget ? `${docTarget.width}x${docTarget.height}` : undefined;
  const items = (q.data?.items ?? []).filter(t => !(docTarget && filters.compatibleOnly) || t.compatible !== false);
  const grid = open ? null : (
    <div className="space-y-3 p-3">
      <TemplateSearch value={filters.q} onChange={v => setFilters(f => ({ ...f, q: v }))} />
      <TemplateCategoryTabs value={filters.tab} onChange={tab => setFilters(f => ({ ...f, tab }))} />
      <TemplateFilters filters={filters} onChange={setFilters} compatibleSize={size} />
      {q.isLoading && <p className="text-xs text-zinc-500" role="status">Loading templates…</p>}
      {q.error && <p className="err" role="alert">{q.error instanceof ApiError ? q.error.message : "Could not load templates"} <button className="underline" onClick={() => void q.refetch()}>Retry</button></p>}
      {!q.isLoading && !q.error && items.length === 0 && <p className="rounded-lg border border-dashed border-line p-4 text-center text-xs text-zinc-400" role="status">{filters.tab === "mine" ? "You haven’t saved any templates yet. Use “Save as template” in the toolbar." : "No templates match. Try clearing a filter."}</p>}
      <div className="grid grid-cols-2 gap-2" aria-label="Templates">{items.map(t => <TemplateCard key={t.id} t={t} onOpen={() => setOpen(t)} busy={a.favorite.isPending} onFavorite={on => a.favorite.mutate({ id: t.id, on })} />)}</div>
      {a.favorite.error && <p className="err" role="alert">Could not update favorite.</p>}
    </div>);
  return (
    <div data-testid="templates-panel">
      {grid}
      {open && <TemplateDetailsPanel summary={items.find(t => t.id === open.id) ?? open} bannerSize={docTarget ? { width: docTarget.width, height: docTarget.height } : undefined} canUse={canUse && !readOnly} onBack={() => setOpen(null)} onUse={setUsing} />}
      {using && <TemplateApplicationDialog template={using} target={target} onClose={() => setUsing(null)} onDone={() => { setUsing(null); onDone?.(); }} />}
    </div>
  );
}
