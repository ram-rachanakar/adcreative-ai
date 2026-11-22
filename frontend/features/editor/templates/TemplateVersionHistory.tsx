"use client";
import { useTemplateVersions } from "@/hooks/templates";

export default function TemplateVersionHistory({ templateId }: { templateId: string }) {
  const q = useTemplateVersions(templateId);
  if (q.isLoading) return <p className="text-xs text-zinc-500">Loading versions…</p>;
  if (q.error) return <p className="err" role="alert">Could not load versions.</p>;
  return (
    <ul aria-label="Template versions" className="space-y-1">
      {(q.data ?? []).map(v => <li key={v.version} className="rounded border border-line px-2 py-1 text-[11px]"><b>v{v.version}</b>{v.current && <span className="ml-1 rounded bg-accent/30 px-1">current</span>} · {new Date(v.created_at).toLocaleDateString()}
        {v.change_note && <div className="text-zinc-400">{v.change_note}</div>}<div className="text-zinc-500">{v.formats.length} sizes</div></li>)}
      <li className="text-[10px] text-zinc-500">Versions are immutable. Banners keep the version they were built from.</li>
    </ul>
  );
}
