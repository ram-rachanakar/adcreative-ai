# M7 Editor — assessment & plan

Baseline before M7 (2026-11-05): backend 217 tests pass, frontend 10 pass.

## 1. Current architecture findings
- **A scene graph already exists.** `rendering/spec.py::CreativeSpec` is a strict, discriminated, painter-ordered element list
  (image/text/shape/button) + background + scrims. `GeneratedCreative` is already *one row per (concept, size)* with its own `spec` JSONB,
  so per-format independence is the existing model. `rendering/svg.py` (SVG → resvg) + `rendering/text.py` (Pillow/FreeType wrap + fit)
  + bundled Liberation/DejaVu fonts is the deterministic renderer used by generation, quality (M6) and export (M5).
- Tenancy: `OrgContext` (membership verified, `X-Org-Id` only a selector), `OrgRepo` (org-filtered), Postgres RLS on `ORG_OWNED_TABLES`,
  CSRF header on unsafe methods, `require_role`.
- Assets: `ProductAsset` + `CreativeAsset`, resolved tenant-scoped by `make_resolver`; `Storage` abstraction (local/S3), signed URLs.
- AI: `ImageGenerationProvider` (`generate_background/visual_asset/...`), mock + OpenAI adapters, `UsageRecord`, org-scoped cache.
- Jobs: Celery (`GenerationJob`, `ExportJob`), `CELERY_EAGER` in tests. Quality: `assess_creative`, `QualityPolicy`.
- Frontend: Next 14.2 / React 18, Zustand, TanStack Query, zod; no canvas library present. Node 18.

## 2. Key decisions (my calls — override if you disagree)
1. **Evolve `CreativeSpec` (v1 → v2), do not add a second model.** v2 adds per-element `visible`, `rotation`, `z_index` (normalised to list order), shape/image
   extras (stroke, `fit: fill`, crop rect, shadow variants), `logo` role, background overlay. v1 documents load unchanged (migrated in code).
   The brief's `transform/style/content` grouping is a *view*: the stored element stays flat (as M3–M6 code, templates and the quality engine already
   depend on) and the frontend types expose `transform` helpers. Revisit if you want the nested wire format.
2. **`CreativeDocument` wraps a `GeneratedCreative` 1:1** (created lazily on first open from its spec). Saves write through to
   `GeneratedCreative.spec` + re-render, so gallery, quality and export keep working with zero duplication. One document per format ⇒ independence by construction.
3. **Konva + react-konva** for interaction only (Next 14/React 18 OK: react-konva 18.x, loaded with `dynamic(ssr:false)`). The canonical document stays Konva-free.
4. **Preview/export parity:** export is always the backend renderer. The editor canvas uses the *same* bundled TTFs (served by the backend) and a TS port of
   `text.py` wrap/fit; a parity test pins TS layout output to backend golden values. Residual risk: browser vs FreeType glyph advance rounding (documented, tested with tolerance).
5. **Undo/redo is client-side command history** (inverse patches) with server operation log (`EditOperation`) for audit; server `undo/redo` endpoints apply the logged inverse so history survives reload.
6. **Optimistic concurrency:** `PATCH` takes `expected_revision`; 409 with latest doc on mismatch; client serialises autosaves (one in flight, coalesced).

## 3. Migration plan (0007_editor)
`creative_documents`, `creative_versions` and `edit_operations` (immutable: UPDATE/DELETE revoked from the runtime role + UPDATE trigger), `regeneration_jobs`, `render_jobs`.
Asset reference tracking is computed on demand (`regeneration.referenced_asset_ids`: live documents + every version snapshot + open candidate jobs) instead of a table that could drift.
All org-owned with RLS added to `ORG_OWNED_TABLES`; indexes on (organization_id, creative_id) unique, (document_id, revision), (document_id, created_at).
`Artboard` is stored inside the document JSON (canvas/background/safe-area) with width/height/format_id denormalised as columns — a separate table would only duplicate the 1:1 relation.

## 4. Task breakdown
P2 backend: spec v2 + validators → models/migration → document service (commands, revision, versions, restore) → API → tests (incl. cross-tenant)
P3 frontend shell: Konva canvas, selection/transform, layer panel, properties, Quick/Advanced
P4 engine: TS command engine + undo/redo + autosave/conflict
P5 AI: regeneration jobs, candidate preview/accept/reject, revision safety
P6 render/quality: render jobs, preflight warnings, parity tests
P7 hardening: e2e, docs, compose verification

## 5. Risks
- Browser/FreeType text metric drift (see 4). - Playwright is not installed; e2e uses Vitest+jsdom and API-level flow tests unless a browser is installable. Will state honestly what was and was not exercised.
- No live AI provider: regeneration verified against the mock only. - S3 adapter still untested. - Docker image pulls may be limited (MinIO).
