"use client";
import clsx from "clsx";
import { Ban, Star } from "lucide-react";
import { summarizeSizes } from "@/lib/templates/templateMappingClient";
import type { TemplateSummary } from "@/lib/templates/types";

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
export default function TemplateCard({ t, onOpen, onFavorite, busy }: { t: TemplateSummary; onOpen: () => void; onFavorite: (on: boolean) => void; busy?: boolean }) {
  const unsupported = t.compatible === false;
  return (
    <article className={clsx("group overflow-hidden rounded-lg border bg-ink/40", unsupported ? "border-line opacity-70" : "border-line hover:border-accent")} data-testid="template-card" aria-label={`Template ${t.name}`}>
      <div className="relative">
        <button className="block w-full" onClick={onOpen} aria-label={`Open ${t.name}`}>
          {t.thumbnail ? /* eslint-disable-next-line @next/next/no-img-element */ <img src={t.thumbnail.url} alt={`${t.name} preview`} className="aspect-[4/3] w-full bg-zinc-900 object-contain" loading="lazy" />
            : <div className="grid aspect-[4/3] w-full place-items-center bg-zinc-900 text-[10px] text-zinc-500">No preview</div>}
        </button>
        <button aria-label={t.favorite ? `Remove ${t.name} from favorites` : `Add ${t.name} to favorites`} aria-pressed={t.favorite} disabled={busy} onClick={() => onFavorite(!t.favorite)}
          className="absolute right-1.5 top-1.5 rounded-full bg-black/60 p-1 text-zinc-300 hover:text-white"><Star size={14} className={t.favorite ? "fill-amber-400 text-amber-400" : ""} /></button>
        {unsupported && <span className="absolute bottom-1.5 left-1.5 inline-flex items-center gap-1 rounded bg-black/70 px-1.5 py-0.5 text-[10px] text-amber-300"><Ban size={10} />Not available for this size</span>}
      </div>
      <div className="space-y-1 p-2">
        <h4 className="truncate text-xs font-medium" title={t.name}>{t.name}</h4>
        <p className="line-clamp-2 text-[10px] leading-snug text-zinc-400">{t.description}</p>
        <div className="flex flex-wrap gap-1 text-[10px]">
          <span className="rounded bg-zinc-800 px-1.5 py-0.5">{cap(t.category)}</span>
          {t.style_tags.map(s => <span key={s} className="rounded bg-zinc-800 px-1.5 py-0.5">{cap(s)}</span>)}
        </div>
        <p className="text-[10px] text-zinc-500" title={t.supported_formats.map(f => f.format_id).join(", ")}>{t.supported_formats.length} sizes · {summarizeSizes(t.supported_formats, 3)}</p>
        <p className="text-[10px] text-zinc-500">{t.owner}{t.status !== "published" ? ` · ${t.status}` : ""}{t.source === "saved_from_design" ? " · from a design" : ""}</p>
      </div>
    </article>
  );
}
