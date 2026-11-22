# AdCreative AI
Turns a product URL into a multi-format ad campaign. Hybrid pipeline: AI generates imagery; a deterministic engine renders all text/logos/products.

**Status: Milestones 1–7 built (foundation → editor) — see docs/plan.md for what is verified and what is not.** SaaS readiness (M8) and hardening (M9) are not built.

## Local dev
    cp .env.example .env
    make infra && cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    make migrate seed test
    make backend-dev          # http://localhost:8000/docs
    cd frontend && npm install && npm run dev   # http://localhost:3000

Seed logins (password `devpassword123`): `{owner,admin,designer,marketer,viewer}@{acme,globex}.example.com`.
Host ports: Postgres 5433, Redis 6380 (chosen to avoid clashes). DB roles: `adcreative` (owner, migrations) and `adcreative_app` (runtime, non-superuser so RLS applies).

## Banner editor (M7)
Open any generated banner with **Open editor** on the Banners page. Quick Edit for copy/colours/images, Advanced Edit for layers, drag/resize/rotate, undo/redo, version history,
selected-element AI edits (preview, then accept), preflight + export. Architecture, API, shortcuts and known limitations: [docs/editor.md](docs/editor.md).

    make infra && make migrate                       # Postgres + Redis, schema (0007_editor)
    cd backend && CELERY_EAGER=true .venv/bin/python scripts/seed_editor_demo.py   # demo campaign with 2 banners (mock AI)
    cd frontend && npm test && npm run build:e2e && npm run e2e                  # unit/component + real-browser tests
