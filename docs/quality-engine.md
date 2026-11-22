# Quality engine (M6)

Flow per banner: render -> deterministic checks -> (optional) bounded auto-correction -> (optional) AI visual review -> stored `QualityReport`.

**What the score means.** It is a rules-compliance score (legibility, layout, conversion, brand, technical), not a judgement of creative merit.
Overall = 0.6 x weighted average + 0.4 x weakest category; any blocking defect caps the score below `fail_below`. Status: `fail` (blocking defect or low score) / `warn` / `pass` (no warnings and score >= `pass_at`).
A banner that was never checked has no score (`null`), and an edited banner's score is marked stale.

**Contrast is measured, not assumed:** the spec is rendered a second time without text; each text box's real backdrop pixels are compared with the text colour and the worst N% (default 10) is judged.

**Corrections** only edit the `CreativeSpec` (recolour text, scrim, approved shorter headline, drop supporting copy, move into the safe zone, enlarge CTA/logo, CTA colours, restore product `contain`). Each round is re-assessed and *rejected* if it is not better; the pre-correction spec is stored on the report row (`POST /creatives/{id}/quality/revert`). Manual runs never auto-correct unless asked; generation jobs follow `policy.auto_correct`.

**Vision review** (`policy.vision_enabled` or per request) sends the PNG to the configured LLM with a closed set of issue codes. It is advisory: it can only trigger `enlarge_cta` / `strengthen_scrim`; art problems become a "regenerate background" recommendation. The mock LLM returns `assessed: false` and is excluded from scores. Not yet exercised against a real model.

**Gates:** `GET/PUT /quality/policy` (admin to write); `POST /campaigns/{id}/exports {quality_gate: "block_failed"}` leaves failed banners out and lists them in `manifest.skipped`.

API: `GET|POST /creatives/{id}/quality`, `POST /creatives/{id}/quality/revert`, `POST|GET /campaigns/{id}/quality` (job / summary).
