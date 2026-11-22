"use client";
import { AlertTriangle, Info, XOctagon } from "lucide-react";
import { sortWarnings } from "@/lib/templates/templateMappingClient";
import type { Preview } from "@/lib/templates/types";

export default function TemplateCompatibilityWarnings({ warnings, quality }: { warnings: Preview["warnings"]; quality: Preview["quality"] }) {
  const ws = sortWarnings(warnings);
  const q = quality.after;
  return (
    <div className="space-y-1.5" data-testid="compat-warnings">
      {ws.length === 0 && <p className="text-xs text-emerald-300" role="status">No compatibility problems found.</p>}
      <ul aria-label="Warnings" className="space-y-1">{ws.map((w, i) => (
        <li key={i} className="flex gap-1.5 text-[11px]" data-severity={w.severity}>
          {w.severity === "error" ? <XOctagon size={13} className="mt-0.5 shrink-0 text-red-400" /> : w.severity === "warning" ? <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-300" /> : <Info size={13} className="mt-0.5 shrink-0 text-sky-300" />}
          <span className="text-zinc-300">{w.message}</span></li>))}</ul>
      {q && !q.error && q.score !== undefined && <p className="text-[11px] text-zinc-400">Rule check (M6): {quality.before?.score ?? "–"} → {q.score} ({q.status}). This measures rule compliance, not advertising performance.</p>}
    </div>
  );
}
