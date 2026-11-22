import { defineConfig } from "@playwright/test";

const API = "http://localhost:8001", PORT = process.env.E2E_WEB_PORT ?? "3000", WEB = `http://localhost:${PORT}`;   // set E2E_WEB_PORT if 3000 is taken by another dev server
/** Browser e2e. Prereqs: Postgres+Redis up (`make infra`), migrations applied, and `NEXT_PUBLIC_API_URL=http://localhost:8001 npm run build`.
 *  Servers are started here (or reused if already running); data is seeded by backend/scripts/seed_editor_demo.py with the MOCK AI providers (no paid calls). */
export default defineConfig({
  testDir: "./e2e", timeout: 120_000, workers: 1, retries: 0, reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: { baseURL: WEB, viewport: { width: 1500, height: 900 }, trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: [
    { command: `cd ../backend && CELERY_EAGER=true CORS_ORIGINS=${WEB} PUBLIC_BASE_URL=${API} .venv/bin/uvicorn app.main:app --port 8001`, url: `${API}/health`, reuseExistingServer: true, timeout: 60_000 },
    { command: `NEXT_PUBLIC_API_URL=${API} npx next start -p ${PORT}`, url: `${WEB}/login`, reuseExistingServer: true, timeout: 60_000 },
  ],
});
