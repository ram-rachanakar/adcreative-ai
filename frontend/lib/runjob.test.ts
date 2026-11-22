import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, runJob } from "./api";

const job = (o: object) => ({ id: "j1", status: "pending", progress: 0, stage_detail: "", error: null, error_status: null, ...o });
const respond = (...bodies: { status?: number; body: object }[]) => {
  const f = vi.fn();
  bodies.forEach(b => f.mockResolvedValueOnce({ ok: (b.status ?? 200) < 400, status: b.status ?? 200, json: async () => b.body }));
  vi.stubGlobal("fetch", f);
  return f;
};
afterEach(() => vi.unstubAllGlobals());

describe("runJob", () => {
  it("polls until the job completes", async () => {
    const f = respond({ status: 202, body: job({}) }, { body: job({ status: "analyzing" }) }, { body: job({ status: "completed" }) });
    expect((await runJob("/x", {}, { intervalMs: 1 })).status).toBe("completed");
    expect(f).toHaveBeenCalledTimes(3);
  });
  it("throws the job's original http status on failure", async () => {
    respond({ status: 202, body: job({}) }, { body: job({ status: "failed", error: "Could not fetch", error_status: 422 }) });
    await expect(runJob("/x", {}, { intervalMs: 1 })).rejects.toMatchObject({ status: 422, message: "Could not fetch" });
  });
  it("surfaces synchronous pre-flight errors", async () => {
    respond({ status: 409, body: { detail: "Analyze the product first" } });
    await expect(runJob("/x")).rejects.toBeInstanceOf(ApiError);
  });
  it("gives up after the timeout", async () => {
    respond({ status: 202, body: job({}) }, { body: job({ status: "analyzing" }) }, { body: job({ status: "analyzing" }) });
    await expect(runJob("/x", {}, { intervalMs: 5, timeoutMs: 1 })).rejects.toMatchObject({ status: 504 });
  });
});
