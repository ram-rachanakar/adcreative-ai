# AdCreative AI — Architecture

## Style
Modular monolith (FastAPI) + Celery workers sharing the same codebase; Next.js frontend talks only to the backend.
The frontend never calls AI providers. Business logic depends on provider *interfaces* (`app/ai`), never on vendor SDKs.

## Modules (backend/app)
| Module | Responsibility |
|---|---|
| api | HTTP routers, dependency wiring, request/response schemas |
| core | config (env-based), security (hashing, sessions), db session, tenancy context |
| models / repositories | SQLAlchemy models; org-scoped repositories (every query filtered by organization_id) |
| scraping | SSRF-safe fetcher, JSON-LD/OG/HTML extraction, Playwright fallback |
| brand / marketing | Brand profile logic; concept & copy generation (LLM via provider interface) |
| ai | `LLMProvider`, `ImageGenerationProvider`, `VisionProvider` interfaces + adapters + mock |
| creative / rendering | Creative spec (JSON schema), layout engine, SVG/HTML renderer, compositing |
| quality | Deterministic checks + vision checks, quality gates, corrections |
| orchestration / workers | Campaign pipeline state machine, Celery tasks, cost tracking |
| export | Encoding, ZIP, manifest |
| storage | S3 (MinIO) abstraction + local-filesystem fallback, signed URLs |

## Tenancy
Every org-owned table has `organization_id`. Org context is derived from the authenticated session + verified membership
(`X-Org-Id` header is only a *selector*, validated against memberships). Repositories require an `OrgContext`. PostgreSQL RLS
(`app.current_org` GUC) is added as defense in depth.

## Creative pipeline
URL -> secure fetch -> extract -> product profile -> brand profile -> concepts (LLM) -> briefs -> AI backgrounds (reused
across formats) -> master spec (JSON) -> layout adaptation per format -> deterministic render (SVG -> PNG via Playwright/resvg)
-> quality gate -> correct/re-render -> export. Final typography is always rendered deterministically, never by an image model.

## Infrastructure
docker-compose: frontend, backend, worker, postgres (host port 5433), redis, minio. Multi-arch images (arm64 OK).

## Orchestration (M5)
`POST /campaigns/{id}/creatives/generate` validates everything up front (selection, briefs, product image, provider configured, sizes) then
persists a `GenerationJob` and enqueues `adcreative.run_banner_job` (Celery/Redis) — it returns 202 immediately. The worker re-reads the job,
sets the tenant (`app.current_org`) from the job's own org id, and runs `generate_creatives` as a resumable loop: one background per concept
(cached per org, committed immediately), then compose→render→validate per (concept,size). Each unit commits its own result, so a failure
never loses finished work. Status path: pending → generating_assets → composing/rendering/validating → completed | partially_completed |
failed | cancelled. Retries: provider errors back off 1s/2s inside the job; worker crashes retry the task 2× (5s/10s), then the job is marked
`failed` + `dead_lettered`. One active job per campaign; `Idempotency-Key` returns the existing job. Exports run as `adcreative.run_export_job`.
Run a worker: `celery -A app.workers.celery_app worker -l info`. Tests set `CELERY_EAGER=true`.
