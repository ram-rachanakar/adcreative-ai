"use client";
import clsx from "clsx";
import type { Tab } from "@/lib/templates/types";

const TABS: { id: Tab; label: string }[] = [{ id: "all", label: "All" }, { id: "layouts", label: "Layouts" }, { id: "styles", label: "Creative styles" }, { id: "industry", label: "Industry templates" }, { id: "mine", label: "My templates" }];
export default function TemplateCategoryTabs({ value, onChange }: { value: Tab; onChange: (t: Tab) => void }) {
  return (
    <div role="tablist" aria-label="Template categories" className="flex flex-wrap gap-1">
      {TABS.map(t => <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)} className={clsx("rounded-full border px-2.5 py-1 text-[11px]", value === t.id ? "border-accent bg-accent/20 text-white" : "border-line text-zinc-400 hover:text-white")}>{t.label}</button>)}
    </div>
  );
}
