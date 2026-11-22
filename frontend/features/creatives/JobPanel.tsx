"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Loader2, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { isActive, jobSummary } from "@/lib/creatives";
import type { Job } from "@/types";

export default function JobPanel({ jobId, onFinished }: { jobId: string; onFinished: () => void }) {
  const qc = useQueryClient();
  const job = useQuery({ queryKey: ["job", jobId], queryFn: () => api<Job>(`/jobs/${jobId}`), refetchInterval: q => (q.state.data && !isActive(q.state.data.status) ? false : 1200) });
  const cancel = useMutation({ mutationFn: () => api<Job>(`/jobs/${jobId}/cancel`, { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["job", jobId] }) });
  const j = job.data;
  const running = !!j && isActive(j.status);
  useEffect(() => { if (j && !isActive(j.status)) onFinished(); }, [j?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!j) return null;
  const failed = j.results.filter(r => r.status === "failed");
  return (
    <div className="card space-y-3" role="status" aria-live="polite">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">{running && <Loader2 className="animate-spin" size={16} />}
          <span className="font-medium capitalize">{j.status.replace(/_/g, " ")}</span><span className="text-zinc-400">{j.stage_detail && running ? `· ${j.stage_detail}` : ""}</span></div>
        {running && <button className="flex items-center gap-1 text-xs text-zinc-400 hover:text-red-400" disabled={cancel.isPending || j.cancel_requested} onClick={() => cancel.mutate()}>
          <XCircle size={14} />{j.cancel_requested ? "Cancelling…" : "Cancel"}</button>}
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-line"><div className="h-full bg-accent transition-all" style={{ width: `${j.progress}%` }} /></div>
      <p className="text-xs text-zinc-400">{jobSummary(j)} · generation cost {j.cost_usd > 0 ? `$${j.cost_usd.toFixed(4)}` : "not priced"}{j.attempts > 0 && ` · ${j.attempts} worker retr${j.attempts > 1 ? "ies" : "y"}`}</p>
      {failed.length > 0 && <ul className="list-disc space-y-1 pl-5 text-xs text-red-300">{failed.map(r => <li key={r.unit}>{r.width}×{r.height}: {r.error}</li>)}</ul>}
      {j.dead_lettered && <p className="err">The worker gave up after repeated errors. Check the worker logs.</p>}
    </div>
  );
}
