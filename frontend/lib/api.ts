const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown, public code?: string) { super(message); }
}

let csrf = "";
let orgId = "";
export const setSession = (c: string, o: string) => { csrf = c; orgId = o; };

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (init.json !== undefined) headers["content-type"] = "application/json";
  if (csrf) headers["x-csrf-token"] = csrf;
  if (orgId) headers["x-org-id"] = orgId;
  const res = await fetch(`${BASE}${path}`, {
    ...init, headers, credentials: "include", body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = (data as { detail?: unknown }).detail;
    const e = (data as { error?: { code?: string; message?: string } }).error;   // structured editor errors: {error:{code,message,request_id}}
    throw new ApiError(res.status, typeof d === "string" ? d : e?.message ?? "Request failed", data, e?.code);
  }
  return data as T;
}

export type JobStatus = { id: string; status: string; progress: number; stage_detail: string; error: string | null; error_status: number | null };
const JOB_DONE = new Set(["completed", "partially_completed", "failed", "cancelled"]);

/** For endpoints that queue a Celery job (analyze, concepts, briefs): POST, then poll /jobs/{id} until it finishes.
 *  Resolves with the finished job; a failed job throws an ApiError carrying the status the old synchronous endpoint returned. */
export async function runJob(path: string, init: RequestInit & { json?: unknown } = {}, opts: { intervalMs?: number; timeoutMs?: number } = {}): Promise<JobStatus> {
  const { intervalMs = 1000, timeoutMs = 10 * 60_000 } = opts;
  let job = await api<JobStatus>(path, { method: "POST", ...init });
  const t0 = Date.now();
  while (!JOB_DONE.has(job.status)) {
    if (Date.now() - t0 > timeoutMs) throw new ApiError(504, "Timed out waiting for the job; it may still finish in the background.");
    await new Promise(r => setTimeout(r, intervalMs));
    job = await api<JobStatus>(`/jobs/${job.id}`);
  }
  if (job.status !== "completed") throw new ApiError(job.error_status ?? 500, job.error ?? `Job ${job.status}`);
  return job;
}
