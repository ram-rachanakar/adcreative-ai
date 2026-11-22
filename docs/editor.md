# M7 — Banner editor

Canva-style, advertising-focused editor for the banners generated in M3–M6. Two modes on one document: **Quick Edit** (copy, CTA, images, brand colours — found by role, no layers) and **Advanced Edit** (layer panel, drag/resize/rotate, alignment, z-order, crop/fit, shape editing, lock/hide, duplicate/delete).
Route: `/campaigns/{campaignId}/editor/{creativeId}` (link: "Open editor" on every banner card).

## Architecture
```
 Browser                                                     Backend
 ───────                                                     ───────
 Konva canvas (interaction + drawing only)                   FastAPI  /creative-documents/*  (org-scoped, RLS)
   ▲ scene.ts: placement maths mirroring svg.py                 │ commands.py  — authoritative command engine
 Zustand store ── commands.ts (TS mirror) ─ history ─┐         │ service.py   — revisions, versions, undo/redo, write-through
   │                                                  │         │ regeneration.py — selected-layer AI jobs
 Autosaver (debounce, serial queue, retry, conflict) ─┴─────►   │ preflight.py — export warnings
                                                                ▼
                                         CreativeDocument.document (JSONB = CreativeSpec)  ──write-through──►  GeneratedCreative.spec
                                                                │                                                 (gallery, M6 quality, M5 export read this)
                                                                ▼
                                         rendering/svg.py + resvg + bundled fonts  ◄── the ONLY thing that produces exported pixels
```
* **One document model.** The editable document *is* the existing `CreativeSpec` (`rendering/spec.py`), extended additively (per-layer `visible`, `rotation`; image `fit: fill`, `crop`, `shadow: drop`; text `auto_fit`; CTA `padding_x`; background `fit`/`locked`). v1 specs load unchanged. There is no second model and no flattened bitmap in the edit path.
* **Layers are flat** (as M3–M6 code, templates and the M6 quality engine already require). The brief's `transform/style/content` grouping maps 1:1 onto the flat fields; `z_index` is derived from array order in API responses. *Deviation from the brief, deliberate.*
* **One document per banner format** (`creative_documents.creative_id` is unique). Nothing in the code path touches another format's document; there is no sync. **Copy to size** (`POST …/duplicate`) creates a *new* creative + document at another size from the current design (basic proportional fit, not a smart re-layout) and leaves the source untouched; copy/paste of a layer inside a document also works.
* **Saving writes through** to `GeneratedCreative.spec` (+`spec_version`), so gallery, quality staleness and export keep working. Rendering is lazy: `ensure_fresh_render` renders the document when export/preview needs it and `rendered_revision != revision`; export never ships a stale image.
* **Konva** (`konva` 9.3.16, `react-konva` 18.2.10) was chosen over hand-rolled canvas/SVG handles for hit-testing, transformer handles and rotation snapping. It never owns state. Next needs `canvas` stubbed in `next.config.mjs` (Konva's Node entry) and the component is loaded with `dynamic(ssr:false)`.
* **Preview/export parity.** Export is always the backend renderer. The canvas uses the same TTFs (served from the allowlisted `GET /editor/fonts/{file}`), a TS port of `rendering/text.py` (golden-tested against Python in `shared/editor-parity/text-layout.json`), and `scene.ts` which mirrors each branch of `svg.py`. Measured on the seeded 300×250 banner (browser test, captured 1:1): mean abs RGB difference **≈0.85–1.0/255** (varies slightly per seeded run), **≤0.5%** of pixels differ by >48 levels. Layout is identical; what remains is anti-aliasing at glyph edges (Skia vs resvg coverage maths) and image-edge snapping. Two changes closed most of the earlier 2.66/255 gap: the canvas draws text with `textRendering = "geometricPrecision"` (sub-pixel glyph positions; Chrome on Linux otherwise snaps glyphs to whole pixels), and the server rounds text baselines to whole pixels, as browsers do. They are *not* byte-identical and cannot be while two different rasterizers are used; the browser test guards the numbers (mean < 1.5, >48-level pixels < 1%). **Preview** (toolbar) renders through the backend, so it shows exactly what exports.

## Data model (migration `0007_editor`)
| Table | Notes |
|---|---|
| `creative_documents` | unique per creative; `document` JSONB, `revision`, `rendered_revision`, `format_id`, `schema_version`(=2), `status` |
| `creative_versions` | **immutable** snapshots (`initial`/`manual`/`auto`/`ai_accept`/`restore_point`); runtime role has no UPDATE/DELETE + UPDATE trigger |
| `edit_operations` | **append-only** log: `before`/`after` patches of only the affected layers, order, background; undo/redo ops reference what they revert |
| `regeneration_jobs` | prompt, `source_revision`, `source_layer`, candidates, provider/model/usage, status |
| `render_jobs` | revision, format, output asset, warnings |
All have `organization_id`, RLS (`app.current_org`), indexes on (org), (document_id, revision/created_at), (org, campaign, format). *`Artboard` is not a separate table*: width/height/format live on the document (1:1), safe-area comes from the format registry. *No `asset_references` table*: references are computed (live docs ∪ all version snapshots ∪ pending candidates).

## Commands (`app/editor/commands.py`, mirrored in `lib/editor/commands.ts`)
`ADD_LAYER, UPDATE_TEXT, UPDATE_STYLE, MOVE_ELEMENT, RESIZE_ELEMENT, ROTATE_ELEMENT, CHANGE_COLOR, REPLACE_IMAGE, CHANGE_BACKGROUND, CROP_IMAGE, REORDER_LAYER, DUPLICATE_ELEMENT, DELETE_ELEMENT, TOGGLE_VISIBILITY, TOGGLE_LOCK, ALIGN_ELEMENT, DISTRIBUTE_ELEMENTS, REGENERATE_ELEMENT_ACCEPTED` (+ internal `APPLY_PATCH` for undo/redo/restore).
Each: validate payload (strict pydantic, `extra=forbid`) → locked/permission check → immutable apply → whole document re-validated through `CreativeSpec` (colours are `#rrggbb`, fonts allowlisted, opacity 0–1, ids unique, fit enum, no HTML/CSS/URLs anywhere) → asset ownership check for *new* asset ids → `before`/`after` patch → `revision += 1` → logged. A batch is all-or-nothing and is one undo unit.
`UPDATE_STYLE` cannot change `id/type/asset_id/x/y/w/h/rotation/locked/visible`; those have dedicated commands.

## Undo/redo, autosave, concurrency
* **Client:** history = list of `{before, after}` patches (≤200, oldest dropped; server versions are the long-term safety net). Undo applies `before` locally and queues an `undo` item; typing/nudging bursts coalesce into one step. A new edit clears redo.
* **Server:** `/undo` and `/redo` reconstruct the stacks from the operation log (units = ops sharing a batch id), so history survives reload.
* **Autosave** (`NEXT_PUBLIC_EDITOR_AUTOSAVE_MS`, default 800): one request in flight, strictly in order, each carrying the revision returned by the previous; every batch has an idempotency `batch_id` (a retried lost response returns `duplicate:true`, never double-applies). Transient errors back off (1,2,4… s, max 5) → `failed` + explicit **Retry**; offline shows `offline`, resumes on `online`. Unsent edits are mirrored to `localStorage` and offered back after a crash/reload (replayed on the loaded revision; stale commands are skipped).
* **Conflict:** every write has `expected_revision`; mismatch → `409 revision_conflict` + latest document. The client pauses, keeps local edits, and offers *Reapply my changes on latest* (replays unsent commands; ones that no longer apply are counted, never forced) or *Discard mine*. An older in-flight save can never overwrite newer work (serial queue + revision check + row lock).
* **Checkpoints:** `initial` on first open, `manual` (History → Save version), `ai_accept`, `restore_point` before a restore, and `auto` at most every `EDITOR_VERSION_INTERVAL_S` (600) while editing. Restore = a *new* revision equal to the snapshot (undoable).

## Selected-element AI (`regeneration.py`)
1. Select a layer → **AI edit** (button, context menu, or Quick Edit). 2. `POST /regenerate` validates (layer exists, unlocked, supported, current revision, 3–500 chars) → job → Celery. 3. Candidates are stored as `CreativeAsset(kind="candidate")` and **previewed on the canvas without touching the document**. 4. **Accept** applies one `REGENERATE_ELEMENT_ACCEPTED` (+`ai_accept` version, undoable); **Reject** discards.
* background → `ImageGenerationProvider.generate_background` (+ code-appended “no text/logos/product” suffix); decorative image/shape → `generate_visual_asset` (an accepted shape becomes an image in the same box/order); text/CTA → LLM alternatives, claim-guarded (`marketing.claims`), prompt-injection fenced; unsupported claims are dropped. **Product** → *deterministic derivatives of the original upload* (cut-out / original), never an image model, unless the user ticks the explicit opt-in. Logos are not AI-regenerable.
* Provider calls retry twice with backoff; usage/cost recorded as `UsageRecord`; failures become `failed` jobs with the document untouched; reject on a running job cancels it.
* **Revision safety:** the job stores `source_revision` + the layer as requested. If the document moved on: other layers changed → accept still works and keeps the newer edits; the target layer was materially changed → `409 target_changed` until the user explicitly confirms (`acknowledge_changes`); the target was deleted → `409 target_missing`.
* **Cleanup:** `regeneration.cleanup_candidates` soft-deletes unreferenced candidates (older than 24 h); anything referenced by a live document, any version snapshot or a pending job is kept.

## API (all org-scoped; unknown/foreign ids → 404; writes need role ≥ designer; CSRF on unsafe methods; errors `{"error":{code,message,request_id}}` + `x-request-id`)
| | |
|---|---|
| `POST /creatives/{id}/document` | open/create the editable document (designer+); `GET` = read-only lookup (viewers) |
| `GET /creative-documents/{id}` · `PATCH` (name/status only) · `GET /campaigns/{id}/creative-documents` | |
| `POST /creative-documents/{id}/operations` | `{expected_revision, commands[], batch_id}` → `{document, operations, duplicate}` / `409` |
| `GET …/history` · `POST …/undo` · `POST …/redo` | |
| `POST …/versions` · `GET …/versions` · `POST …/restore/{version_id}` | |
| `POST …/duplicate` | copy this design to a new size → new creative + independent document (source untouched) |
| `GET …/preflight` | text overflow, out-of-canvas, missing asset/font, low-res image, CTA visibility/missing |
| `POST …/render` → `GET /render-jobs/{id}` | png/jpeg/webp, exact canvas size, re-decoded and size-validated |
| `POST …/regenerate` · `GET /regeneration-jobs/{id}` · `POST …/accept` · `POST …/reject` | |
| `GET /campaigns/{id}/editor-assets` · `GET /editor/fonts/{file}` | placeable assets + brand colours; allowlisted font files |
Uploads reuse the existing `POST /campaigns/{id}/product/assets` (MIME sniffing, size/pixel limits, dedupe, org-scoped).

## Keyboard shortcuts
`Ctrl/⌘+Z` undo · `Ctrl/⌘+Shift+Z` or `Ctrl+Y` redo · `Ctrl/⌘+D` duplicate · `Delete`/`Backspace` delete (locked layers refused; undo restores) · arrows 1 px, `Shift`+arrow 10 px · `Esc` clear selection/preview · `Ctrl/⌘+S` save now · `Ctrl/⌘+C/V` copy/paste layer · `Space`+drag pan · `Ctrl/⌘`+scroll zoom. Inside inputs/textareas only `Ctrl/⌘+S` is intercepted.

## Running & testing
```
make infra && (cd backend && .venv/bin/alembic upgrade head)
cd backend && .venv/bin/python -m pytest -q                         # all backend tests (needs Postgres on :5433, Redis)
cd frontend && npm test && npx tsc --noEmit                         # unit + jsdom component tests
cd frontend && npm run build:e2e && npm run e2e                     # real-browser tests (Playwright chromium; starts API on :8001 + web on :3000, seeds with mock AI)
cd backend && CELERY_EAGER=true .venv/bin/python scripts/seed_editor_demo.py   # demo campaign with 300x250 + 1080x1080 banners
```
The browser suite covers the full 17-step workflow, offline/conflict, shortcuts and canvas↔renderer pixel parity. If port 8000 is taken, the e2e config uses 8001 for the API.

## Known limitations (honest list)
* **Real AI providers were not exercised**: regeneration runs against the mock providers; OpenAI adapters are covered only by mock-transport tests (unchanged since M4). Cancel of an already-running provider call takes effect between candidates, not mid-request.
* Canvas ↔ export are visually consistent, not pixel-identical (numbers above; only the Preview button shows exact export pixels). Canvas letter-spacing relies on `CanvasRenderingContext2D.letterSpacing`; browsers without it will draw spaced text without the spacing (verified only in Chromium).
* Templates now exist — see `docs/templates.md`. Not implemented: smart re-layout when copying to another size (only proportional scaling), freehand/arbitrary shapes (rect/ellipse only), multi-select resize with mixed layer types keeps per-layer boxes, grouping, text rotation handles snap but alignment/distribution use unrotated boxes.
* Hidden layers are skipped by the renderer **and** the M6 quality checks; quality is not re-run on every edit (the export dialog shows the latest report and flags it *out of date*).
* The text box edits are plain text (no per-character styling). Inline canvas editing is a textarea overlay.
* shadcn/ui is not part of this repo's frontend (M1–M6 use Tailwind primitives); the editor follows the existing conventions instead of introducing it.
* S3/MinIO remains unexercised here (as in M2/M5).

## Transparent export
Export has a "Transparent background" option (`transparent: true` on `POST /campaigns/{id}/exports`). It re-renders the banner with the background layer and its scrims omitted, so only elements remain; PNG and WebP keep an alpha channel and SVG omits the background rect. JPEG is rejected (422) because it has no alpha. Every file is re-opened and checked for an alpha channel, and the manifest records `transparent`. Warnings are added when text may be hard to read without its backdrop, or when a full-bleed image layer leaves no transparent pixel. Normal exports are unchanged.
