"use client";
import clsx from "clsx";
import { useThumbnail } from "@/hooks/templates";
import type { SupportedFormat } from "@/lib/templates/types";

/** Larger blueprint preview for one supported format (neutral placeholders; never anyone's product). Unsupported formats are not offered. */
export default function TemplatePreview({ templateId, version, formats, value, onChange, highlight }: { templateId: string; version?: number; formats: SupportedFormat[]; value: string | undefined; onChange: (id: string) => void; highlight?: string }) {
  const q = useThumbnail(templateId, value, version);
  return (
    <div>
      <div className="grid min-h-40 place-items-center rounded-lg border border-line bg-zinc-900 p-2" aria-busy={q.isLoading}>
        {q.isLoading ? <p className="text-xs text-zinc-500">Rendering preview…</p> : q.error ? <p className="err">Preview unavailable</p>
          : q.data ? /* eslint-disable-next-line @next/next/no-img-element */ <img src={q.data.url} alt={`Template preview at ${value}`} className="max-h-72 w-auto max-w-full object-contain" /> : null}
      </div>
      <div className="mt-2 flex max-h-24 flex-wrap gap-1 overflow-auto" role="radiogroup" aria-label="Supported formats">
        {formats.map(f => <button key={f.format_id} role="radio" aria-checked={value === f.format_id} onClick={() => onChange(f.format_id)}
          className={clsx("rounded border px-1.5 py-0.5 text-[10px]", value === f.format_id ? "border-accent bg-accent/20" : "border-line text-zinc-400 hover:text-white", highlight === f.format_id && "ring-1 ring-emerald-400")} title={f.inherits ? `Inherits from ${f.inherits}` : "Dedicated layout"}>{f.width}×{f.height}</button>)}
      </div>
    </div>
  );
}
