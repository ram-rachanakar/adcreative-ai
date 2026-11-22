# M7 — Templates

Reusable blueprints for the banner editor. A template is **not** a flattened banner: it is a set of *semantic slots* (headline, product, logo, CTA, badge, decoration…) laid out explicitly for each exact canvas size. Applying one produces a *candidate document* that the user previews and then commits through the editor's command engine.

Entry points: the **Templates** tab in the editor's left panel (restyle the open banner), **Start from a template** on the campaign's Banners page (new banner), **Save as template** in the editor toolbar.

## Decisions (from the product brief)
| Topic | Behaviour |
|---|---|
| Editing | Quick and Advanced edit both work on the result. |
| Formats | Every banner stays an independent document. Applying a template to one format never touches another; there is no sync. |
| AI | Applying a template never calls an AI provider and never regenerates anything. Selected-element AI keeps working afterwards. |
| Modes | `layout_only`, `layout_style`, `full` (below). |
| Ownership | `system` (built-in, read-only), `organization`, `personal`. |

## Architecture
```
 Templates tab / dialogs (React Query, no gallery in Zustand)          FastAPI  /templates/*  (app/api/templates.py)
   preview -> before/after, mapping, warnings, M6 rule check  ───►     templates/application.py  preview()  — builds a CANDIDATE, stores it on a TemplateApplication
   apply   -> adoptServer(view)  (one undoable step)          ───►     application.apply_existing()  → editor.apply_batch( APPLY_TEMPLATE )   [revision-checked, atomic]
                                                                         templates/mapping.py   — pure engine: document + resolved layout -> candidate
                                                                         templates/schema.py    — strict Pydantic models, layout inheritance
                                                                         templates/library.py   — built-in templates (idempotent seeding)
                                                                         templates/service.py   — visibility, search, thumbnails, save-as, versions, duplicate
```
* The document model is unchanged: the candidate is a normal `CreativeSpec`. Layers gained one optional, additive field, **`slot`** (semantic role), and `meta` gained `source_template_id` / `source_template_version` (provenance only). Older documents load unchanged.
* **No parallel implementation**: APPLY_TEMPLATE / REVERT_TEMPLATE_APPLICATION / COPY_LAYER_TO_ARTBOARD are ordinary commands in `editor/commands.py`. They produce the same before/after patch format as every other command, so the existing server-side undo/redo, operation log, versions and autosave work without change. `meta` is now part of the patch when it changes so undo also restores provenance.
* Previews render with the deterministic renderer (`render_spec`) from the structured scene graph, never from a bitmap. With a persisted creative the M6 deterministic checks run on the candidate (advisory; **no claim about conversion or performance**).

## Data model (migration `0008_templates`)
| Table | Notes |
|---|---|
| `templates` | `owner_type` system/organization/personal, `owner_id`, `visibility`, `category` (industry), `style_tags`, `layout_tags`, `status` draft/published/archived, `current_version`, `thumbnail_asset_id`. System rows have `organization_id NULL` (CHECK enforces `system ⇔ NULL`). |
| `template_versions` | **immutable** (UPDATE trigger + no UPDATE/DELETE grant): `definition` (palette, fonts, sample copy, source), `brand_constraints`, `application_modes`, `schema_version`. |
| `template_layouts` | one row per exact size per version: `format_id`, `width`, `height`, `aspect_ratio`, `layout_definition` JSONB. **Slots live inside the layout definition** (they only make sense within a layout; normalising them would add joins without adding a constraint we can enforce). Immutable. |
| `template_assets` | template-owned reusable assets (same-organization creative assets only). Immutable. |
| `template_applications` | preview/apply record: mode, `mapping_result`, preserved/replaced/created layer ids, `warnings`, the exact **`source_document`** (recoverability) and `candidate_document`, preview image ids, `source_revision`, `applied_revision`, status previewed/applied/rejected/reverted. |
| `template_favorites`, `template_usages` | per-user favourites; usage log (feeds `GET /templates/{id}/usage`). |
RLS: system rows are readable by everyone (`organization_id IS NULL OR org match`) but writable only when the seeder raises `app.system_admin` — the API cannot create or edit them. Applications, favourites and usage are strictly per organization.

## Slots, layouts and inheritance
Semantic roles: `headline_primary, headline_secondary, body_copy, product_primary, product_secondary, brand_logo, primary_cta, secondary_cta, promotion_badge, decorative_shape` (+ the document `background`). A slot has `kind` (text/image/button/shape — checked against the role), `required`, `transform` (fractions of *that layout's* canvas, so it is bound to one exact size), `z`, `constraints`, `default_style` (allow-listed keys only), `content_policy` (`from_document` | `placeholder` | `static`) and `asset_policy`.
A layout may `extends` another layout of the same version (e.g. 336×280 extends 300×250 with scaled type) — inheritance is resolved into a complete layout before use; cycles / missing parents are errors. If no layout exists for the banner's exact size the template is **unsupported there** (`format_unsupported`, listing the sizes it does support); it is never stretched. Built-in templates cover 24 registry sizes (the hero template deliberately omits strips).

## Mapping (`templates/mapping.py`)
1. Each layer gets a semantic slot: explicit `slot`, else inferred (headlines by font size → primary/secondary, body, CTA by area, logo, product by area, badge). Layers created by an earlier template have ids `tpl_*`.
2. Layers match layout slots by **role**, then **type compatibility**. Several explicit candidates → deterministic pick (first) + an `ambiguities` entry the user can override (`slot_overrides`).
3. Never invented: missing required slots are reported (`missing_required_slot`), optional ones stay empty; campaign copy and product/logo assets are never fabricated or swapped. Only template *decoration* (shapes) is added.
4. **Unmapped layers are preserved**, not deleted. Locked layers are left untouched (warning). Previous `tpl_*` decoration is removed in `layout_style`/`full` (reported as "Removed") so decoration doesn't stack.
5. Brand precedence: mandatory brand profile rules (fonts, colours) > user-approved overrides > template styling. Violations produce a warning and the compliant alternative (`brand_font_enforced`, `brand_color_substituted`, `contrast_adjusted`); logo minimum size / clear space are checked (`logo_too_small`, `logo_clear_space`).
6. Validation on the candidate: text overflow / minimum font (using the renderer's own fitter), overlaps, platform safe zone, out-of-canvas, missing assets/fonts (preflight), distortion (`fit: fill`).

| Mode | Changes | Preserves |
|---|---|---|
| `layout_only` | x/y/w/h/rotation, layer order | everything else (copy, fonts, colours, assets, background, CTA styling) |
| `layout_style` | + typography, colours, radii, shadows, decoration | product, logo, copy, CTA text, background |
| `full` | + template background, template-owned wording/assets | product & logo & copy unless the template supplies its own **and** the user ticked "allow replacing <role>"; the whole apply needs an explicit confirmation |

## Workflows
**Restyle:** `POST /templates/{id}/preview {document_id, mode}` → candidate stored, nothing else touched → UI shows before/after, Preserved/Moved/Restyled/Replaced/Added/Removed, warnings → `POST /templates/{id}/apply {application_id, expected_revision, confirm}`: row lock, revision recheck (`409 revision_conflict` if the banner changed), `restore_point` version, APPLY_TEMPLATE as one operation, `template` version checkpoint, usage + audit. Undo/redo use the normal stacks; edits made after the application stay independent. `POST /template-applications/{id}/revert` restores the stored pre-application document (needs `force` if later edits exist). Rejecting/cancelling discards the candidate.
**Start new:** `preview {campaign_id, format_id}` seeds a document from the campaign (concept headline/supporting copy/CTA, product cut-out, logo — no AI call), maps the template in `full` mode, and `apply` creates the creative + editable document (`created: true`).
**Save as template:** `POST /creative-documents/{id}/save-as-template` clones the design into slots: copy → `{{placeholders}}`, product/logo → `from_document`, layer ids and source campaign/document ids dropped, background image only if the user opts in **and** it is an org-owned generated background (otherwise solid colour), decorative images only if opted in, **product/customer uploads never**. Extra sizes must have the same aspect ratio (scaled, via `extends`); other ratios are refused. Creates v1 (or a new version of an owned template), generates a thumbnail, publishes with the chosen visibility.

## Permissions
| | viewer | designer | admin/owner |
|---|---|---|---|
| browse / favourite | ✓ | ✓ | ✓ |
| preview / apply / create / duplicate / save-as | ✗ | ✓ | ✓ |
| edit / publish / archive / add version | – | own templates (personal; organization ones they authored) | all organization templates |
| built-in templates | read, duplicate only (nobody can edit them through the API) |
Personal templates are visible only to their owner (not even admins); drafts only to their creator and managers. Foreign/private/unknown ids all return 404. The organisation always comes from the verified membership, never the body. Template JSON is strictly validated (no HTML/URLs/scripts, allow-listed style keys and fonts, hex colours only, assets must be same-organization creative assets). Archiving never alters banners built from a template; versions are immutable and banners remember `source_template_id/version`.

## API
`GET /templates` (q, tab, industry, style, layout, aspect, width/height, owner, source, compatible_with, document_id, favorites, status) · `GET /templates/{id}` · `GET …/versions` · `GET …/layouts/{WxH}/thumbnail` · `POST /templates` · `PATCH /templates/{id}` · `POST …/versions` · `POST …/publish|archive|duplicate|favorite` · `DELETE …/favorite` · `GET …/usage` · `POST …/preview` · `POST …/apply` · `POST /template-applications/{id}/reject|revert` · `GET /creative-documents/{id}/template-applications` · `POST /creative-documents/{id}/save-as-template` · `POST /creative-documents/{id}/copy-layer`. Errors: `{"error": {code, message, request_id}}` with proper statuses (403/404/409/422).
Commands: `APPLY_TEMPLATE`, `REVERT_TEMPLATE_APPLICATION`, `COPY_LAYER_TO_ARTBOARD` are document commands. *Duplicate template* and *Save as template* do not change a document, so they are library actions (endpoints above), recorded in the audit log, not entries in the document operation log.

## Running & testing
```
make infra && (cd backend && .venv/bin/alembic upgrade head)
cd backend && .venv/bin/python -m pytest -q tests/test_templates.py      # 28 tests: modes, mapping, undo/redo, tenancy, immutability, save-as…
cd frontend && npm test && npx tsc --noEmit                              # + templates.test.ts (10) and templates.dom.test.tsx (20)
cd frontend && npm run build:e2e && npx playwright test e2e/templates.spec.ts   # real-browser scenarios A–F
```

## Known limitations
* Real AI providers are not involved in templates; selected-element AI is unchanged (mock-only verification, as in M7).
* Built-in templates use solid/gradient backgrounds and neutral product/logo placeholders in thumbnails; there are no photographic template backgrounds, and only 9 hand-built designs (24 sizes each, one family per aspect class) — they are starting points, not art-directed per platform.
* Slots are not normalised into their own table (they live in the layout JSON). Search/filter/pagination run in Python over the (small) visible set; fine for hundreds of templates, would need SQL for thousands.
* "Template managers" are admins/owners plus the author of an organization template; there is no platform-level administrator, so built-in templates can only be changed by shipping new code.
* Save-as-template only keeps *visible* layers with a recognisable role (others are listed as "left out"). Extra sizes are limited to the same aspect ratio. Copy-to-size's smart re-layout is still not implemented.
* Undo after a template depends on the existing editor history; reverting with `force` discards later edits (they stay in version history).
* The M6 rule check on previews does not run the vision pass; brand-font and brand-colour enforcement depends on what the brand profile contains (`colors`, `fonts`).
* Template previews on the library grid are rendered lazily per organization on first view (cached by version+format).
