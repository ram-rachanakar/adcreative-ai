"use client";
import clsx from "clsx";
import { MODE_INFO, type ApplyMode } from "@/lib/templates/types";

export default function TemplateApplicationModeSelector({ value, onChange, allowed }: { value: ApplyMode; onChange: (m: ApplyMode) => void; allowed: ApplyMode[] }) {
  return (
    <div role="radiogroup" aria-label="Application mode" className="grid gap-2 sm:grid-cols-3">
      {(Object.keys(MODE_INFO) as ApplyMode[]).map(m => {
        const info = MODE_INFO[m]; const off = !allowed.includes(m);
        return (
          <button key={m} role="radio" aria-checked={value === m} disabled={off} onClick={() => onChange(m)} className={clsx("rounded-lg border p-2.5 text-left text-xs disabled:opacity-40", value === m ? "border-accent bg-accent/15" : "border-line hover:border-zinc-500")}>
            <div className="font-medium">{info.label}</div><div className="mt-0.5 text-zinc-300">{info.summary}</div>
            <div className="mt-1.5 text-[11px] text-zinc-400"><b className="text-zinc-300">Changes:</b> {info.changes}</div>
            <div className="mt-1 text-[11px] text-zinc-400"><b className="text-zinc-300">Keeps:</b> {info.keeps}</div>
          </button>);
      })}
    </div>
  );
}
