"use client";
import clsx from "clsx";
import { useState } from "react";

export default function TemplateBeforeAfterPreview({ before, after }: { before: string | null; after: string | null }) {
  const [view, setView] = useState<"split" | "before" | "after">("split");
  const img = (src: string | null, label: string) => src ? /* eslint-disable-next-line @next/next/no-img-element */ <figure className="min-w-0 flex-1"><img src={src} alt={label} className="max-h-72 w-full rounded border border-line bg-zinc-900 object-contain" /><figcaption className="mt-1 text-center text-[11px] text-zinc-400">{label}</figcaption></figure> : null;
  return (
    <div data-testid="before-after">
      <div role="group" aria-label="Comparison view" className="mb-2 inline-flex overflow-hidden rounded-lg border border-line">
        {(["split", "before", "after"] as const).map(v => <button key={v} aria-pressed={view === v} onClick={() => setView(v)} className={clsx("px-2 py-1 text-xs", view === v ? "bg-accent text-white" : "text-zinc-300")}>{v === "split" ? "Side by side" : v === "before" ? "Current" : "Proposed"}</button>)}
      </div>
      <div className="flex gap-3">{view !== "after" && img(before, "Current")}{view !== "before" && img(after, "Proposed")}</div>
    </div>
  );
}
