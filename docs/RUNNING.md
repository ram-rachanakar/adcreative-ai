# Running and checking AdCreative AI

How to start the app locally, see what is built, and run the tests. Everything below uses the **mock** AI providers by default (no paid calls). Docker Compose for the full stack (`make up`) has not been verified; use the steps here.

## 0. One-time setup
```
cp .env.example .env                       # defaults work for local development
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ../frontend && npm install
```
Postgres and Redis run in Docker (`make infra`). Postgres listens on **5433** and Redis on **6380**, as in `.env.example`.

## 1. Start infrastructure and apply migrations
```
make infra
make migrate            # alembic upgrade head (includes 0008_templates)
```

## 2. Seed demo data
```
cd backend && CELERY_EAGER=true .venv/bin/python scripts/seed_editor_demo.py
```
It prints one line of JSON: a demo login (`email`, `password`), the `campaign_id`, and the ids of generated 300x250 and 1080x1080 banners. Each run creates a fresh organization. The nine built-in templates are created automatically the first time templates are listed.

## 3. Start the backend
```
cd backend && CELERY_EAGER=true .venv/bin/uvicorn app.main:app --port 8000
```
- `CELERY_EAGER=true` runs generation and export jobs inline. Without it, start a Celery worker (see `docker-compose.yml`) or jobs stay pending.
- API docs, listing every endpoint (including the template routes): http://localhost:8000/docs

## 4. Start the frontend
```
cd frontend
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run build
NEXT_PUBLIC_API_URL=http://localhost:8000 npx next start -p 3000
```
Open http://localhost:3000 and sign in with the seeded account.

**Port 3000 taken?** Use another port and tell the backend to allow that origin:
```
CORS_ORIGINS=http://localhost:3100 CELERY_EAGER=true .venv/bin/uvicorn app.main:app --port 8000   # backend
npx next start -p 3100                                                                             # frontend
```

## 5. What to click through
| Area | Where | What to try |
|---|---|---|
| Banner generation | Campaign → Banners | Generate banners for the chosen formats |
| Editor | Open any banner | Move, resize, edit text, undo/redo, autosave, versions |
| Templates | Editor → left panel → Templates | Preview, then apply in **Layout only**, **Layout + style**, **Full template**; undo afterwards |
| Start from a template | Campaign → Banners → "Start from a template" | New banner from a template, with your copy and product |
| Save as template | Editor top bar → "Save as template" | Saves placeholders only, never customer copy or product images |
| Quality | Campaign → Banners (quality score on each banner) | Deterministic checks and auto-correction |
| Export | Campaign → Banners → Export panel | PNG / JPEG / WebP / SVG, optional **Transparent background** (not for JPEG), ZIP with manifest |
| Preview | Editor toolbar | Server-rendered preview: exactly the pixels that export |

## 6. Run the tests
Do not run the backend suite and the browser suite at the same time; they share one database.
```
cd backend && .venv/bin/python -m pytest -q                      # 271 tests, about 15–20 min
cd frontend && npx tsc --noEmit && npm test                      # type check + 94 unit/component tests
cd frontend && npm run build:e2e && npx playwright test          # 10 real-browser tests
```
If port 3000 is in use (Playwright reuses whatever is on it, even another project's server):
```
E2E_WEB_PORT=3100 npx playwright test
```
Playwright starts the API on port **8001**, seeds its own data, and uses mock providers.

Canvas-to-export parity: `PARITY_DUMP=/some/dir npx playwright test -g parity` saves the canvas image and the server render so they can be diffed.

## 7. Where to read more
- `docs/plan.md`: milestone checklist, what is done and not done
- `docs/editor.md`: editor, preview/export parity, transparent export, limitations
- `docs/templates.md`: templates architecture, modes, permissions, API
- `docs/quality-engine.md`, `docs/creative-engine.md`, `docs/ai-providers.md`, `docs/architecture.md`

## Known gaps
- Docker Compose full stack not verified.
- AI features are only exercised against mock providers; no live-provider check yet.
- Canvas and export are close (mean difference about 0.9/255) but not byte-identical.
- M8 (SaaS readiness) and M9 (hardening) are not started.
