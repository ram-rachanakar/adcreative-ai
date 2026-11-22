/** Autosave engine: debounced, strictly serial (one request in flight, in order), idempotent (batch ids), optimistic-concurrency aware.
 *  An older in-flight save can never overwrite a newer edit: requests are sent one at a time, each with the revision returned by the previous one. */
import type { Command, DocView } from "./types";

export type SaveStatus = "saved" | "unsaved" | "saving" | "failed" | "offline" | "conflict";
export type QueueItem = { kind: "commands"; batchId: string; commands: Command[] } | { kind: "undo" } | { kind: "redo" };
export interface SaveResult { document: DocView; duplicate?: boolean }
export interface Transport {
  commands(body: { expected_revision: number; commands: Command[]; batch_id: string }): Promise<SaveResult>;
  undo(expected_revision: number): Promise<SaveResult>;
  redo(expected_revision: number): Promise<SaveResult>;
  reload?(): Promise<DocView>;
}
export interface TransportError { status: number; code?: string; message: string; body?: unknown }
export interface AutosaveOptions {
  transport: Transport; debounceMs?: number; maxRetries?: number; baseDelayMs?: number;
  onStatus: (s: SaveStatus, info: { pending: number; error?: string }) => void;
  onSaved: (doc: DocView, queueEmpty: boolean) => void;
  onConflict: (latest: DocView | null, pending: QueueItem[]) => void;
  onRejected: (item: QueueItem, err: TransportError) => void;
  onQueueChange?: (queue: QueueItem[], serverRevision: number) => void;
  isOnline?: () => boolean;
}

const isTransportError = (e: unknown): e is TransportError => typeof e === "object" && e !== null && "status" in e;

export class Autosaver {
  queue: QueueItem[] = [];
  serverRevision: number;
  status: SaveStatus = "saved";
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inflight = false;
  private retries = 0;
  private paused = false;
  private lastError?: string;
  constructor(private o: AutosaveOptions, revision: number) { this.serverRevision = revision; }

  private set(s: SaveStatus, error?: string) { this.status = s; this.lastError = error; this.o.onStatus(s, { pending: this.queue.length, error }); }
  private changed() { this.o.onQueueChange?.(this.queue, this.serverRevision); }

  enqueue(item: QueueItem) {
    this.queue.push(item); this.changed();
    if (!this.paused) { this.set("unsaved"); this.schedule(this.o.debounceMs ?? 800); }
  }
  /** Replace the still-unsent tail item (used to merge a burst like typing into one saved command). Returns false if it was already sent. */
  replaceLast(item: QueueItem): boolean {
    if (this.inflight && this.queue.length <= 1) return false;
    if (!this.queue.length) return false;
    this.queue[this.queue.length - 1] = item; this.changed(); this.schedule(this.o.debounceMs ?? 800); return true;
  }
  private schedule(ms: number) { if (this.timer) clearTimeout(this.timer); this.timer = setTimeout(() => { this.timer = null; void this.flush(); }, ms); }

  /** Save now (Cmd/Ctrl+S, leaving the page, export). */
  async flush(): Promise<void> {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    if (this.inflight || this.paused) return;
    this.inflight = true;
    try {
      while (this.queue.length) {
        if (this.o.isOnline && !this.o.isOnline()) { this.set("offline"); this.scheduleRetry(); return; }
        this.set("saving");
        const item = this.queue[0];
        try {
          const res = item.kind === "commands" ? await this.o.transport.commands({ expected_revision: this.serverRevision, commands: item.commands, batch_id: item.batchId })
            : item.kind === "undo" ? await this.o.transport.undo(this.serverRevision) : await this.o.transport.redo(this.serverRevision);
          this.queue.shift(); this.serverRevision = res.document.revision; this.retries = 0; this.changed();
          this.o.onSaved(res.document, this.queue.length === 0);
        } catch (e) {
          if (!isTransportError(e)) { this.fail("Unexpected error while saving"); return; }
          if (e.status === 409 && e.code === "revision_conflict") {
            this.paused = true; this.set("conflict", e.message);
            this.o.onConflict(((e.body as { document?: DocView } | undefined)?.document) ?? null, [...this.queue]); return;
          }
          if (e.status === 0 || e.status >= 500 || e.status === 429) {            // transient: network down, server hiccup, rate limit
            if (this.retries >= (this.o.maxRetries ?? 5)) { this.fail(e.message || "Could not reach the server"); return; }
            this.retries++; this.set(this.o.isOnline && !this.o.isOnline() ? "offline" : "saving", e.message); this.scheduleRetry(); return;
          }
          this.queue.shift(); this.changed();                                       // permanent rejection of this change (validation/permission/lock)
          this.o.onRejected(item, e); this.set(this.queue.length ? "unsaved" : "saved"); if (this.queue.length) this.schedule(0); return;
        }
      }
      this.set("saved");
    } finally { this.inflight = false; }
  }
  private scheduleRetry() { this.schedule((this.o.baseDelayMs ?? 1000) * 2 ** Math.max(0, this.retries - 1)); }
  private fail(msg: string) { this.set("failed", msg); }
  /** Explicit retry after a persistent failure. */
  retry() { this.retries = 0; this.paused = false; return this.flush(); }
  /** After a conflict has been resolved by the user. */
  resume(revision: number, queue: QueueItem[]) { this.paused = false; this.retries = 0; this.serverRevision = revision; this.queue = queue; this.changed(); if (queue.length) { this.set("unsaved"); this.schedule(0); } else this.set("saved"); }
  get pending() { return this.queue.length; }
  dispose() { if (this.timer) clearTimeout(this.timer); this.paused = true; }
}
