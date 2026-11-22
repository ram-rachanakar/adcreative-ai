"""Pre-export warnings computed from the same renderer the export uses (no invented scores; the M6 quality engine remains the scorer)."""
import uuid

from sqlalchemy.orm import Session

from app.creative.service import make_resolver
from app.rendering import fonts
from app.rendering.spec import ButtonEl, CreativeSpec, ImageEl, TextEl
from app.rendering.svg import render_spec
from app.storage.base import Storage


def run(db: Session, org_id: uuid.UUID, doc: dict, storage: Storage) -> list[dict]:
    spec = CreativeSpec.model_validate(doc)
    resolve = make_resolver(db, org_id, storage)
    res = render_spec(spec, resolve)
    rep = {e.id: e for e in res.report.elements}
    out: list[dict] = []

    def w(code, msg, layer=None, severity="warning"):
        out.append({"code": code, "severity": severity, "message": msg, "layer_id": layer})

    for e in spec.elements:
        if not e.visible:
            continue
        r = rep.get(e.id)
        if isinstance(e, (TextEl, ButtonEl)):
            for wt in {e.font_weight}:
                try:
                    fonts.font_path(e.font_family, wt)
                except fonts.MissingFont:
                    w("missing_font", f"Font {e.font_family} {wt} is not available", e.id, "error")
            if r and r.text_overflow:
                w("text_overflow", f"Text in “{e.name or e.id}” does not fit its box", e.id)
            if r and r.shrunk and isinstance(e, TextEl):
                w("text_shrunk", f"Text auto-shrunk to {r.font_size:.0f}px", e.id, "info")
        if r and r.out_of_bounds:
            w("out_of_canvas", f"“{e.name or e.id}” extends beyond the canvas", e.id)
        if isinstance(e, ImageEl):
            a = resolve(e.asset_id)
            if a and r:
                dw = r.bbox[2]
                if a.width * (e.crop.w if e.crop else 1) < dw * 0.75:
                    w("low_resolution", f"Image “{e.name or e.id}” is smaller than its on-canvas size and will look soft", e.id)
        if isinstance(e, ButtonEl) and (e.opacity < 0.6):
            w("cta_visibility", "CTA is semi-transparent and may be hard to see", e.id)
    for mid in res.report.missing_assets:
        w("missing_asset", f"Referenced asset {mid} is missing", None, "error")
    for msg in res.report.errors:
        w("render_error", msg, None, "error")
    if not any(isinstance(e, ButtonEl) and e.visible for e in spec.elements):
        w("cta_missing", "No visible CTA button on this banner", None, "info")
    return out
