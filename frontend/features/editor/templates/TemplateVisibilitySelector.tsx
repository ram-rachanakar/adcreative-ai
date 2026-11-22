"use client";
import clsx from "clsx";

export type Visibility = "personal" | "organization";
export default function TemplateVisibilitySelector({ value, onChange }: { value: Visibility; onChange: (v: Visibility) => void }) {
  const opts: { v: Visibility; label: string; help: string }[] = [{ v: "personal", label: "Personal", help: "Only you can see and use it." }, { v: "organization", label: "Organization", help: "Everyone in your organization can use it; editors and admins can change it." }];
  return (
    <div role="radiogroup" aria-label="Visibility" className="grid grid-cols-2 gap-2">
      {opts.map(o => <button type="button" key={o.v} role="radio" aria-checked={value === o.v} onClick={() => onChange(o.v)} className={clsx("rounded-lg border p-2 text-left text-xs", value === o.v ? "border-accent bg-accent/15" : "border-line")}><div className="font-medium">{o.label}</div><div className="text-[11px] text-zinc-400">{o.help}</div></button>)}
    </div>
  );
}
