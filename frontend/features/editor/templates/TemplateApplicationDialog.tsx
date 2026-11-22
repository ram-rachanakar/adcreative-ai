"use client";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { useTemplateApplication, useTemplatePreview } from "@/hooks/templates";
import { SLOT_LABEL, sizeLabel, supports } from "@/lib/templates/templateMappingClient";
import { MODE_INFO, type ApplyMode, type Preview, type SupportedFormat, type TemplateSummary } from "@/lib/templates/types";
import { useEditor } from "@/stores/editor";
import { Modal } from "../controls";
import TemplateApplicationModeSelector from "./TemplateApplicationModeSelector";
import TemplateBeforeAfterPreview from "./TemplateBeforeAfterPreview";
import TemplateCompatibilityWarnings from "./TemplateCompatibilityWarnings";
import TemplateMappingSummary from "./TemplateMappingSummary";

export type Target = { kind: "document"; documentId: string; width: number; height: number } | { kind: "campaign"; campaignId: string };

/** Non-destructive flow: choose mode -> preview (candidate document, nothing is changed) -> compare, read mapping/warnings -> apply or cancel. */
export default function TemplateApplicationDialog({ template, target, onClose, onDone }: { template: TemplateSummary; target: Target; onClose: () => void; onDone?: () => void }) {
  const router = useRouter();
  const [mode, setMode] = useState<ApplyMode>(target.kind === "campaign" ? "full" : "layout_only");
  const sizes: SupportedFormat[] = template.supported_formats;
  const [formatId, setFormatId] = useState<string>(sizes.find(f => f.format_id === "1080x1080")?.format_id ?? sizes[0]?.format_id ?? "");
  const [confirmReplace, setConfirmReplace] = useState<string[]>([]); const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [pv, setPv] = useState<Preview | null>(null); const [reviewed, setReviewed] = useState(false); const [err, setErr] = useState<string | null>(null); const [stale, setStale] = useState(false);
  const { apply, reject } = useTemplateApplication(template.id); const previewM = useTemplatePreview(template.id);
  const openApp = useRef<string | null>(null);

  const supported = target.kind === "document" ? supports(template, target.width, target.height) : sizes.length > 0;
  const modes = template.application_modes;
  useEffect(() => { openApp.current = pv?.status === "previewed" ? pv.application_id : null; }, [pv]);
  useEffect(() => () => { if (openApp.current) void reject.mutateAsync(openApp.current).catch(() => {}); }, []); // eslint-disable-line react-hooks/exhaustive-deps  (closing discards the candidate)

  const run = async (over?: { confirm?: string[]; slots?: Record<string, string> }) => {
    setErr(null); setStale(false); setReviewed(false);
    if (target.kind === "document") {
      await useEditor.getState().saveNow();                                    // preview the SAVED state: pending edits are flushed first
      if (useEditor.getState().saveStatus !== "saved") { setErr("Your latest edits are not saved yet. Resolve the save problem first, then preview again."); return; }
    }
    if (pv?.status === "previewed") void reject.mutateAsync(pv.application_id).catch(() => {});
    try {
      const body = { mode, confirm_replace: over?.confirm ?? confirmReplace, slot_overrides: over?.slots ?? overrides, ...(target.kind === "document" ? { document_id: target.documentId } : { campaign_id: target.campaignId, format_id: formatId }) };
      setPv(await previewM.mutateAsync(body));
    } catch (e) { setPv(null); setErr(e instanceof ApiError ? e.message : "Preview failed"); }
  };
  const toggleReplace = (role: string) => { const next = confirmReplace.includes(role) ? confirmReplace.filter(r => r !== role) : [...confirmReplace, role]; setConfirmReplace(next); void run({ confirm: next }); };
  const needsReview = pv?.requires_confirmation;
  const canApply = !!pv && pv.can_apply && (!needsReview || reviewed) && !apply.isPending && !stale;

  const commit = async () => {
    if (!pv) return; setErr(null);
    try {
      const res = await apply.mutateAsync({ application_id: pv.application_id, expected_revision: pv.source_revision ?? undefined, confirm: reviewed });
      openApp.current = null;
      if (res.created && res.creative_id) { router.push(`/campaigns/${(target as { campaignId: string }).campaignId}/editor/${res.creative_id}`); return; }
      useEditor.getState().adoptServer(res.document, `Template: ${template.name}`);          // one undoable step; the server already holds the op + version checkpoint
      useEditor.setState({ notice: `Applied “${template.name}” (${MODE_INFO[pv.mode].label}). Undo reverts it.` });
      onDone?.(); onClose();
    } catch (e) {
      if (e instanceof ApiError && e.code === "revision_conflict") { setStale(true); setErr("The banner changed after this preview was made. Nothing was applied; preview again."); }
      else setErr(e instanceof ApiError ? e.message : "Could not apply the template");
    }
  };
  const cancel = () => { onClose(); };

  return (
    <Modal title={`${target.kind === "campaign" ? "Start a banner from" : "Apply"} “${template.name}”`} onClose={cancel} wide>
      {!supported && target.kind === "document" && <p role="alert" className="mb-3 rounded border border-amber-500/50 bg-amber-500/10 p-2 text-xs text-amber-200">This template has no {target.width}×{target.height} layout, so it can’t be applied to this banner (templates are never stretched). Supported: {template.supported_formats.map(sizeLabel).join(", ")}.</p>}
      {target.kind === "campaign" && <div className="mb-3"><label className="label" htmlFor="tpl-format">Banner format</label>
        <select id="tpl-format" className="input" value={formatId} onChange={e => { setFormatId(e.target.value); setPv(null); }}>{sizes.map(f => <option key={f.format_id} value={f.format_id}>{sizeLabel(f)}{f.placements.length ? ` · ${f.placements.length} placement${f.placements.length > 1 ? "s" : ""}` : ""}</option>)}</select></div>}
      <TemplateApplicationModeSelector value={mode} onChange={m => { setMode(m); setPv(null); setConfirmReplace([]); setReviewed(false); }} allowed={modes} />
      <div className="mt-3 flex items-center gap-2"><button className="btn" disabled={!supported || previewM.isPending} onClick={() => void run()}>{previewM.isPending ? "Building preview…" : pv ? "Refresh preview" : "Preview"}</button>
        <span className="text-xs text-zinc-400">Preview never changes your banner.</span></div>
      {err && <p className="err mt-2" role="alert">{err}</p>}
      {pv && <div className="mt-4 space-y-4" aria-live="polite">
        <TemplateBeforeAfterPreview before={pv.before_url} after={pv.after_url} />
        <TemplateMappingSummary mapping={pv.mapping} emphasise={needsReview} />
        {pv.mapping.ambiguities.map(a => <label key={a.slot} className="block text-xs">Several layers match “{SLOT_LABEL(a.slot)}”. Use:
          <select className="input mt-1 !py-1" value={overrides[a.slot] ?? a.chosen} onChange={e => { const o = { ...overrides, [a.slot]: e.target.value }; setOverrides(o); void run({ slots: o }); }}>{a.candidates.map(c => <option key={c} value={c}>{pv.mapping.items.find(i => i.layer_id === c)?.layer_name ?? c}</option>)}</select></label>)}
        {pv.needs_confirmation.length > 0 && <fieldset className="rounded-lg border border-amber-500/50 p-2 text-xs"><legend className="px-1 text-amber-200">The template brings its own content for:</legend>
          {pv.needs_confirmation.map(r => <label key={r} className="flex items-center gap-2 py-0.5"><input type="checkbox" checked={confirmReplace.includes(r)} onChange={() => toggleReplace(r)} />Allow the template to replace “{SLOT_LABEL(r)}” (otherwise yours is kept)</label>)}</fieldset>}
        <TemplateCompatibilityWarnings warnings={pv.warnings} quality={pv.quality} />
        {needsReview && <label className="flex items-start gap-2 rounded-lg border border-line p-2 text-xs"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} aria-label="I have reviewed the changes" />
          <span>I’ve reviewed what will be replaced, added and removed. You can undo this afterwards, and the original is kept in version history.</span></label>}
      </div>}
      <div className="mt-5 flex justify-end gap-2"><button className="rounded-lg border border-line px-4 py-2 text-sm" onClick={cancel}>Cancel</button>
        <button className="btn" disabled={!canApply} onClick={() => void commit()}>{apply.isPending ? "Applying…" : target.kind === "campaign" ? "Create banner" : "Apply template"}</button></div>
    </Modal>
  );
}
