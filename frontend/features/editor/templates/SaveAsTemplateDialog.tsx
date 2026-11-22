"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { useSaveAsTemplate, useTemplates } from "@/hooks/templates";
import { ApiError } from "@/lib/api";
import { parseFormats, saveAsTemplateSchema, type SaveAsTemplateForm } from "@/lib/templates/schemas";
import { EMPTY_FILTERS, INDUSTRIES, LAYOUTS, STYLES, type SaveResult } from "@/lib/templates/types";
import { useEditor } from "@/stores/editor";
import { Modal } from "../controls";
import TemplateVisibilitySelector from "./TemplateVisibilitySelector";

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
/** Converts the current design into a reusable blueprint: copy becomes placeholders, product/customer assets are never included. */
export default function SaveAsTemplateDialog({ documentId, onClose }: { documentId: string; onClose: () => void }) {
  const view = useEditor(s => s.view)!; const save = useSaveAsTemplate(documentId);
  const mine = useTemplates({ ...EMPTY_FILTERS, tab: "mine" }, { enabled: true });
  const [result, setResult] = useState<SaveResult | null>(null); const [err, setErr] = useState<string | null>(null); const [asVersion, setAsVersion] = useState(false);
  const { register, handleSubmit, control, formState: { errors, isSubmitting } } = useForm<SaveAsTemplateForm>({
    resolver: zodResolver(saveAsTemplateSchema),
    defaultValues: { name: `${view.name.replace(/\s+\d+x\d+$/, "")} template`, description: "", category: "general", style_tags: [], layout_tags: [], visibility: "personal", extra_formats: "", include_background_assets: false, include_decorative_assets: false, preserve_brand_styling: true },
  });
  const owned = (mine.data?.items ?? []).filter(t => t.can_edit);
  const submit = handleSubmit(async v => {
    setErr(null);
    try {
      await useEditor.getState().saveNow();
      setResult(await save.mutateAsync({ name: v.name, description: v.description, category: v.category, style_tags: v.style_tags, layout_tags: v.layout_tags, visibility: v.visibility, formats: parseFormats(v.extra_formats),
        include_background_assets: v.include_background_assets, include_decorative_assets: v.include_decorative_assets, preserve_brand_styling: v.preserve_brand_styling, ...(asVersion && v.new_version_of ? { new_version_of: v.new_version_of } : {}) }));
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Could not save the template"); }
  });
  if (result) return (
    <Modal title="Template saved" onClose={onClose}>
      <p className="text-sm">“{result.template.name}” is now a {result.template.owner_type} template ({result.template.supported_formats.map(f => f.format_id).join(", ")}).</p>
      <p className="mt-1 text-xs text-zinc-400">Your headline, copy, product and logo were replaced by placeholders — only layout and styling were saved.</p>
      {result.notes.length > 0 && <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-zinc-300" aria-label="Left out">{result.notes.map(n => <li key={n}>{n}</li>)}</ul>}
      <div className="mt-4 flex justify-end"><button className="btn" onClick={onClose}>Done</button></div>
    </Modal>);
  return (
    <Modal title="Save as template" onClose={onClose} wide>
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2" noValidate>
        <div className="sm:col-span-2"><label className="label" htmlFor="tpl-name">Template name</label><input id="tpl-name" className="input" {...register("name")} />{errors.name && <p className="err" role="alert">{errors.name.message}</p>}</div>
        <div className="sm:col-span-2"><label className="label" htmlFor="tpl-desc">Description</label><textarea id="tpl-desc" rows={2} className="input" {...register("description")} />{errors.description && <p className="err">{errors.description.message}</p>}</div>
        <div><label className="label" htmlFor="tpl-ind">Industry</label><select id="tpl-ind" className="input" {...register("category")}>{INDUSTRIES.map(i => <option key={i} value={i}>{cap(i)}</option>)}</select></div>
        <div><label className="label" htmlFor="tpl-fmt">Also support these sizes</label><input id="tpl-fmt" className="input" placeholder={`same ratio as ${view.canvas.width}x${view.canvas.height}, e.g. ${view.canvas.width * 2}x${view.canvas.height * 2}`} {...register("extra_formats")} />{errors.extra_formats && <p className="err" role="alert">{errors.extra_formats.message}</p>}</div>
        <Controller control={control} name="style_tags" render={({ field }) => <fieldset><legend className="label">Design style</legend><div className="flex flex-wrap gap-1.5">{STYLES.map(s => <label key={s} className="flex items-center gap-1 text-xs"><input type="checkbox" checked={field.value.includes(s)} onChange={e => field.onChange(e.target.checked ? [...field.value, s] : field.value.filter(x => x !== s))} />{cap(s)}</label>)}</div></fieldset>} />
        <Controller control={control} name="layout_tags" render={({ field }) => <fieldset><legend className="label">Layout</legend><div className="flex flex-wrap gap-1.5">{LAYOUTS.map(s => <label key={s} className="flex items-center gap-1 text-xs"><input type="checkbox" checked={field.value.includes(s)} onChange={e => field.onChange(e.target.checked ? [...field.value, s] : field.value.filter(x => x !== s))} />{cap(s)}</label>)}</div></fieldset>} />
        <div className="sm:col-span-2"><span className="label">Visibility</span><Controller control={control} name="visibility" render={({ field }) => <TemplateVisibilitySelector value={field.value} onChange={field.onChange} />} /></div>
        <fieldset className="space-y-1 sm:col-span-2"><legend className="label">What to include</legend>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" {...register("include_background_assets")} />Include the background image (only your organization’s generated backgrounds; otherwise the background becomes a solid colour)</label>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" {...register("include_decorative_assets")} />Include decorative images (never product photos or logos)</label>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" {...register("preserve_brand_styling")} />Keep this design’s fonts and colours as the template’s defaults</label></fieldset>
        {owned.length > 0 && <div className="sm:col-span-2"><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={asVersion} onChange={e => setAsVersion(e.target.checked)} aria-label="Save as a new version of an existing template" />Save as a new version of a template I can edit</label>
          {asVersion && <select aria-label="Template to version" className="input mt-1 !py-1" {...register("new_version_of")}><option value="">Choose…</option>{owned.map(t => <option key={t.id} value={t.id}>{t.name} (v{t.current_version})</option>)}</select>}</div>}
        {err && <p className="err sm:col-span-2" role="alert">{err}</p>}
        <div className="flex justify-end gap-2 sm:col-span-2"><button type="button" className="rounded-lg border border-line px-4 py-2 text-sm" onClick={onClose}>Cancel</button><button className="btn" disabled={isSubmitting || save.isPending}>{isSubmitting || save.isPending ? "Saving…" : "Save template"}</button></div>
      </form>
    </Modal>
  );
}
