import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";

/** Seeds a fresh org + campaign with generated 300x250 and 1080x1080 banners (mock providers) before each run. */
export default async function setup() {
  const backend = path.resolve(__dirname, "../../backend");
  const out = execFileSync(path.join(backend, ".venv/bin/python"), ["scripts/seed_editor_demo.py"], { cwd: backend, env: { ...process.env, CELERY_EAGER: "true" }, encoding: "utf8" });
  writeFileSync(path.join(__dirname, ".seed.json"), out.trim().split("\n").pop()!);
}
