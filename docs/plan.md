# Implementation plan & checklist

Legend: [ ] todo, [~] in progress, [x] done (verified by test/run)

## M1 Foundation
- [x] Repo skeleton, .env.example, Makefile
- [~] docker-compose written; postgres+redis verified; minio image not pullable here; backend/worker/frontend containers not yet built/run
- [x] FastAPI app, config, DB session, Alembic
- [x] Models: org, user, membership, workspace, project, campaign (+ rest stubbed in later milestones)
- [x] Auth: register/login, argon2/bcrypt hash, secure cookie sessions, CSRF
- [x] Tenancy: OrgContext, role checks, org-scoped repositories, RLS
- [x] Seed data (2 orgs, users per role)
- [x] Tests: auth, tenant isolation (2 orgs)
- [x] Next.js app: login/register, dashboard shell
## M2 Product intelligence
- [x] SSRF-safe fetcher (IP pinning, redirect revalidation, size/MIME/port limits) — 36 tests
- [x] JSON-LD > Open Graph > page-text extractor (no fabrication, sources recorded)
- [x] Image download + Pillow validation, content-hash dedupe, storage abstraction w/ signed URLs (local verified; S3 adapter untested)
- [x] Palette detection, brand profiles w/ user overrides, product edit API, uploads, RLS on new tables
- [x] Review UI (/campaigns/[id]); [ ] Playwright render fallback (not built), [ ] DNS-based tests use injected resolver only, [x] analyze now runs as a Celery job (see Async stages)
## M3 Marketing intelligence
- [x] LLMProvider interface; Anthropic adapter (model/key from env, retry+backoff, schema-validated JSON, one repair retry) — verified ONLY against a mock transport, no live call made
- [x] Deterministic MockLLM (offline; builds from supplied facts only)
- [x] Versioned prompts (concepts.v1, brief.v1), untrusted-data fencing, prompt-injection redaction
- [x] >=5 concepts across the 10 approaches; deterministic claim guard + one corrective pass; flagged concepts block selection until edited or acknowledged
- [x] Briefs (no-text/no-product constraints appended by code), usage records w/ token + cost (unpriced unless rates configured)
- [x] Concepts UI; [ ] concept comparison view, [ ] language/tone pickers beyond create form, [x] async execution (see Async stages)
## M4 Creative generation
- [x] ImageGenerationProvider iface; procedural mock; OpenAI adapter (mock-transport tested only)
- [x] Org-scoped background cache, provenance (provider/model/prompt version/params/cost/duration), usage records
- [x] Product preservation: tight border-flood cut-out (pixels untouched), contain-fit, contact shadow
- [x] CreativeSpec schema (+JSON schema export), SVG->PNG renderer, bundled fonts, text fitting, contrast-driven colours
- [x] Landscape/square/portrait templates; generate/list/get/PUT-spec APIs; banners UI with free text re-render
- [ ] Lifestyle scenes, AI product mode, learned segmentation, shadow/lighting beyond contact shadow, async execution (M5)
## M5 Multi-format
- [x] Format registry (shared/banner-formats/formats.json): all 29 listed sizes, platform/placement, safe zones, file limits, eligibility notes, versioning; every entry `unverified` / `validated_on: null` (NOT checked against live platform docs)
- [x] Platform/format/select-all/custom-size selection, saved presets (org-scoped)
- [x] Adaptive layout engine: landscape, square, portrait, small display, very wide, skyscraper; layout validator (fit, min font, bounds, overlaps); fallback chain; safe zones; fading scrims
- [x] GenerationJob via Celery+Redis: statuses, progress, cancel (pending + mid-run), exponential backoff, partial failure, idempotency key, resume, dead-letter flag, per-job cost; verified against a REAL broker + separate worker process
- [x] Export: PNG/JPEG/WebP/SVG, per-platform limit handling (quality only, never size), every file re-decoded and size-checked, ZIP + manifest + thumbnails, single download, regenerate individual banner
- [x] UI: format picker, live job panel, filterable gallery, favourites, export panel
- [x] Async stages: `POST /campaigns/{id}/analyze`, `/concepts/generate`, `/briefs/generate` now return 202 + a job (`kind` analyze|concepts|briefs, reusing `generation_jobs`, task `adcreative.run_text_job`); cheap checks (not analyzed, nothing selected, provider not configured, no URL) stay synchronous 4xx; failures carry `error_status` (the HTTP code the old sync endpoint returned); one active job per campaign; frontend `runJob()` polls. Verified with eager Celery tests only — NOT yet against a real broker/worker
- [ ] Image optimisation limited to re-encoding; no CDN/etag caching; browser UI not exercised in a real browser
## M6 Quality engine
- [x] Deterministic checks (measured on rendered pixels): dimensions, render errors, text fit, min font, contrast (worst-10% of backdrop), CTA label/surroundings contrast, safe zones, canvas bounds, edge margins, overlaps, text density, CTA size, hierarchy, logo/product size, cropped product, unsupported claims in edited copy
- [x] Configurable policy (thresholds, weights, gates): defaults < org (`PUT /quality/policy`, admin) < per-request; unknown keys rejected
- [x] Bounded correction loop (spec-only, max rounds configurable, rejects changes that make things worse, original kept for one-click revert)
- [x] Vision review via the LLM provider (image blocks): advisory, opt-in, usage-recorded; mock provider reports "not assessed" and never contributes a score
- [x] Runs per banner inside generation; campaign-wide re-check job; export manifest carries real scores; optional export gate that skips failed banners
- [x] UI: badge, filter, summary, per-banner report panel, undo auto-correction, export gate checkbox
- [ ] Vision adapter never called against a real model; art defects (artefacts, clutter, off-brand) are only reported with a "regenerate background" recommendation, not auto-fixed; scores measure rule compliance, not creative merit; brand-font check absent
## M7 Editor — see docs/editor.md (architecture, API, shortcuts, limitations) and docs/m7-editor-plan.md
- [x] Spec v2 (additive): visible, rotation, crop, fit fill, drop shadow, auto_fit, CTA padding, background fit/lock; renderer + M6 checks honour them
- [x] CreativeDocument (1:1 with a banner/format), immutable versions, append-only operation log, regeneration + render jobs; migration 0007, RLS, immutability grants/trigger
- [x] Command engine (18 commands) server-side + TS mirror; undo/redo (client + server log), revision-checked autosave (serial, idempotent, retry, offline, conflict + recovery)
- [x] Konva canvas, Quick/Advanced modes, layer panel (DnD, visibility, lock), properties per layer type, asset/brand/background panels, snap/guides/safe area, shortcuts, context menu, inline text edit
- [x] Selected-element AI (background/image/shape/text; product = deterministic original), preview-then-accept, revision safety, cost/usage, cleanup of unreferenced candidates
- [x] Preflight warnings, render jobs (png/jpeg/webp), export always renders latest revision, version history + restore
- [x] Tests: backend 14+9 editor tests, 64 frontend unit/component tests, 4 real-browser Playwright tests (full 17-step flow, offline+conflict, shortcuts, canvas/renderer parity)
- [x] Copy design to another size (new independent banner, proportional fit)
- [x] Templates system (docs/templates.md): immutable template versions, per-format layouts with semantic slots, 3 application modes with non-destructive preview, APPLY_TEMPLATE through the command engine, save-as-template, personal/organization/system ownership, migration 0008
- [ ] Not done: smart re-layout on copy, live-provider AI verification (mock only), byte-identical canvas/export (not achievable with two rasterizers; now ≈0.9/255 mean diff, was 2.66)
## M8 SaaS readiness        [ ] metering, quotas, plans, audit logs
## M9 Hardening             [ ] security review, load tests, docs

Assumptions (my calls): JWT-free server-side sessions in Postgres w/ httpOnly cookie; argon2 for hashing; resvg/Playwright
for rasterising SVG; rembg (optional dependency) for background removal; Next.js 14 (Node 18 compatible).
Honest limits: real AI generation is untested until provider keys are supplied; mock providers are used in CI.
