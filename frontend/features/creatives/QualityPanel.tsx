"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { CheckCircle2, Loader2, Undo2, XCircle, AlertTriangle, Info } from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { QualityFinding, QualityReport, QualityResponse, QualityStatus } from "@/types";

export const STATUS_STYLE: Record<QualityStatus, string> = { pass: "text-green-400", warn: "text-yellow-400", fail: "text-red-400" };
const ICON = { fail: XCircle, warn: AlertTriangle, info: Info } as const;

export function QualityBadge({ score, status, stale }: { score: number | null; status: QualityStatus | null; stale?: boolean }) {
  if (score === null || !status) return <span className="text-zinc-500">quality: not checked</span>;
  return <span className={clsx("font-medium", STATUS_STYLE[status])} title={stale ? "The banner was edited after this check" : undefined}>quality {score}/100 · {status}{stale ? " (outdated)" : ""}</span>;
}

export default function QualityPanel({ id, onChanged }: { id: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const [vision, setVision] = useState(false);
  const [fix, setFix] = useState(false);
  const q = useQuery({ queryKey: ["quality", id], queryFn: () => api<QualityResponse>(`/creatives/${id}/quality`) });
  const done = () => { qc.invalidateQueries({ queryKey: ["quality", id] }); qc.invalidateQueries({ queryKey: ["creative", id] }); onChanged(); };
  const run = useMutation({ mutationFn: () => api(`/creatives/${id}/quality`, { method: "POST", json: { vision, auto_correct: fix } }), onSuccess: done });
  const revert = useMutation({ mutationFn: () => api(`/creatives/${id}/quality/revert`, { method: "POST" }), onSuccess: done });
  const r: QualityReport | null = q.data?.assessed ? q.data : null;
  return (
    <div className="space-y-3 rounded-lg border border-line p-3 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn" disabled={run.isPending} onClick={() => run.mutate()}>{run.isPending && <Loader2 className="animate-spin" size={14} />}{r ? "Re-check" : "Check quality"}</button>
        <label className="flex items-center gap-1"><input type="checkbox" checked={fix} onChange={e => setFix(e.target.checked)} />Auto-correct (layout only)</label>
        <label className="flex items-center gap-1" title="One AI call per banner; costs money with a real provider"><input type="checkbox" checked={vision} onChange={e => setVision(e.target.checked)} />AI visual review</label>
        {r?.can_revert && <button className="flex items-center gap-1 text-accent hover:underline" disabled={revert.isPending} onClick={() => revert.mutate()}><Undo2 size={12} />Undo auto-correction</button>}
      </div>
      {(run.error || revert.error) && <p className="err">{((run.error || revert.error) as Error).message}</p>}
      {q.data && !r && <p className="text-zinc-500">Not checked yet. Nothing is scored until a check has run.</p>}
      {r && (<>
        <p><QualityBadge score={r.score} status={r.status} stale={r.stale} /> <span className="text-zinc-500">· rules score {r.deterministic_score}{r.vision?.counted_in_score ? ` · AI review ${r.vision.score}` : ""}</span></p>
        <div className="grid grid-cols-5 gap-2">{Object.entries(r.categories).map(([k, v]) => <div key={k}><div className="text-zinc-500">{k}</div><div className={clsx("h-1.5 rounded", v >= 90 ? "bg-green-500" : v >= 70 ? "bg-yellow-500" : "bg-red-500")} style={{ width: `${v}%` }} /><div>{v}</div></div>)}</div>
        <ul className="space-y-1">{r.findings.map((f: QualityFinding, i) => { const I = ICON[f.severity]; return <li key={i} className={clsx("flex gap-1", f.severity === "fail" ? "text-red-300" : f.severity === "warn" ? "text-yellow-300" : "text-zinc-400")}><I size={13} className="mt-0.5 shrink-0" />{f.message}{f.source === "vision" && " (AI review)"}</li>; })}</ul>
        <p className="flex items-center gap-1 text-zinc-500"><CheckCircle2 size={13} className="text-green-500" />{r.passed.length} check{r.passed.length === 1 ? "" : "s"} passed</p>
        {r.corrections.length > 0 && <div><div className="font-medium text-zinc-300">Automatic corrections</div><ul className="list-disc pl-5 text-zinc-400">{r.corrections.map((c, i) => <li key={i}>{c.accepted ? "Applied" : "Rejected (made it worse)"}: {c.actions.map(a => a.detail).join("; ")} ({c.score_before}→{c.score_after})</li>)}</ul></div>}
        {r.recommendations.length > 0 && <div><div className="font-medium text-zinc-300">Needs a human</div><ul className="list-disc pl-5 text-zinc-400">{r.recommendations.map((x, i) => <li key={i}>{x.message} — {x.suggestion}</li>)}</ul></div>}
        {r.vision && <p className="text-zinc-500">{r.vision.assessed ? `AI review (${r.vision.provider}): ${r.vision.summary ?? ""}` : r.vision.error ? `AI review failed: ${r.vision.error}` : "AI review did not run: no real vision model is configured (the mock provider cannot look at images)."}</p>}
      </>)}
    </div>
  );
}
