"use client";
import { ACTION_HELP, ACTION_LABEL, ACTION_ORDER, groupMapping, SLOT_LABEL } from "@/lib/templates/templateMappingClient";
import type { Action, Preview } from "@/lib/templates/types";
import clsx from "clsx";

const TONE: Record<Action, string> = { preserved: "text-zinc-300", moved: "text-sky-300", restyled: "text-violet-300", replaced: "text-amber-300", added: "text-emerald-300", removed: "text-red-300" };
/** Exactly which elements will be preserved / moved / restyled / replaced / added / removed. */
export default function TemplateMappingSummary({ mapping, emphasise }: { mapping: Preview["mapping"]; emphasise?: boolean }) {
  const g = groupMapping(mapping.items);
  return (
    <div className="space-y-2" data-testid="mapping-summary">
      {ACTION_ORDER.filter(a => g[a].length).map(a => (
        <section key={a} aria-label={ACTION_LABEL[a]} className={clsx("rounded-lg border p-2", emphasise && (a === "replaced" || a === "removed") ? "border-amber-500/60 bg-amber-500/5" : "border-line")}>
          <h5 className={clsx("text-xs font-semibold", TONE[a])}>{ACTION_LABEL[a]} ({g[a].length}) <span className="font-normal text-zinc-500">· {ACTION_HELP[a]}</span></h5>
          <ul className="mt-1 space-y-0.5 text-[11px] text-zinc-300">{g[a].map(i => <li key={i.layer_id}>{i.layer_name} <span className="text-zinc-500">({SLOT_LABEL(i.slot)})</span>{i.changes.length > 0 && a !== "preserved" && <span className="text-zinc-500"> — {i.changes.slice(0, 6).join(", ")}</span>}{i.note && <span className="text-zinc-500"> — {i.note}</span>}</li>)}</ul>
        </section>))}
      {mapping.missing_required.length > 0 && <p className="text-[11px] text-amber-300" role="status">Required slots with no matching layer (nothing was invented): {mapping.missing_required.map(SLOT_LABEL).join(", ")}</p>}
    </div>
  );
}
