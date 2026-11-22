# Creative engine (M4)
**Hybrid by design.** AI providers only make backgrounds. Product, logo, headline, body, CTA are placed and typeset by our renderer.

Pipeline: brief.background_prompt_final -> `ImageGenerationProvider` (cached per org) -> product cut-out (derived asset) ->
`templates.compose` -> `CreativeSpec` (JSON, schema in `shared/schemas/creative-spec.schema.json`) -> `rendering.svg` -> PNG (resvg).

- **Spec**: only `image | text | shape | button` elements; extra keys forbidden; colours `#RRGGBB`; fonts from the bundled registry
  (Liberation Sans/Serif, DejaVu Sans). No HTML/JS/external URLs can be expressed. Text is XML-escaped.
- **Fidelity**: product images are placed with `contain` (never stretched). Cut-outs only change alpha (tested pixel-for-pixel) and are
  separate derived assets; the original is never modified. If the backdrop is not uniform the cut-out is skipped and reported.
- **Text**: measured with the same font files the rasteriser uses; shrinks within `min_font_size`, otherwise reports `text_overflow`
  (never silently truncated). Text colour/scrim is chosen from the real background pixels to reach WCAG 4.5:1.
- **Cost**: one background per concept, reused across sizes; cache key includes the organization id, so nothing is shared across tenants.
  Text/colour/layout edits (`PUT /creatives/{id}/spec`) re-render deterministically and never call a provider.
- **Providers**: `mock` (procedural, offline) and `openai` (`OPENAI_API_KEY`, `IMAGE_MODEL`, `IMAGE_COST_PER_IMAGE`). The OpenAI adapter has
  only been tested against a mock transport.
## Known limits (M4)
Templates: landscape/square/portrait only (aspect 0.5–2.4); other sizes need the M5 adaptive engine. Cut-out handles uniform backdrops only
(no learned segmentation). Lifestyle scenes and AI product representations are interface methods only. Generation runs synchronously.
Layout polish is basic: do not treat these as agency-grade designs yet.

# Adaptive layouts (M5)
`templates.compose` -> `family_for(w,h)` (very_wide >2.4, skyscraper <0.5, small area<=100k, then portrait/square/landscape) -> layout -> spec ->
`spec_problems` (text fit, min font 8px, bounds, element overlaps). If problems remain it tries: drop supporting copy -> shortest *approved*
alternate headline -> minimal (no logo). Each step is written to `spec.meta.notes`; if nothing fits, the best attempt is kept and flagged
("could not be fully fitted") rather than hidden. Story formats keep text inside the registry safe zone. Contrast scrims fade out (no hard edges).
Registry data is unverified (see `shared/banner-formats/formats.json`); confirm platform specs before launching ads.
