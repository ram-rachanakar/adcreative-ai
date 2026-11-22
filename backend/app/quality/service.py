"""Quality engine orchestration: assess -> (bounded) correct -> re-assess -> optional vision -> persist a scored report."""
import io
import uuid

from PIL import Image
from sqlalchemy.orm import Session

from app.ai.llm import LLMError, LLMProvider
from app.creative import formats
from app.creative.service import _persist_render, make_resolver
from app.marketing import service as marketing
from app.marketing.service import Conflict
from app.models import Campaign, CreativeConcept, GeneratedCreative, Organization, ProductAsset, QualityReport, UsageRecord
from app.models.product import Product
from app.quality import checks, corrections, vision
from app.quality.policy import QualityPolicy, resolve_policy
from app.rendering import color
from app.rendering.spec import CreativeSpec, TextEl
from app.rendering.svg import render_spec
from app.repositories.org_repo import OrgRepo
from app.storage.base import Storage

VISION_CATEGORY = {"weak_hierarchy": "conversion", "cta_not_prominent": "conversion", "unreadable_text": "legibility", "misleading_content": "brand",
                   "off_brand": "brand", "distorted_product": "brand", "ai_artifact": "brand", "unprofessional": "brand"}


def org_policy(db: Session, org_id: uuid.UUID, overrides: dict | None = None) -> QualityPolicy:
    org = db.get(Organization, org_id)
    return resolve_policy(org.quality_policy if org else {}, overrides)


def _rank(res: dict) -> tuple:
    """Lower is better: fewer blocking defects first, then fewer warnings, then higher score."""
    f = res["findings"]
    return (sum(x["severity"] == "fail" for x in f), sum(x["severity"] == "warn" for x in f), -res["score"])


class _Ctx:
    """Everything a run needs about a creative, loaded once."""
    def __init__(self, db: Session, org_id: uuid.UUID, cr: GeneratedCreative, storage: Storage, pol: QualityPolicy):
        self.pol, self.cr = pol, cr
        self.campaign = OrgRepo(db, Campaign, org_id).get(cr.campaign_id)
        concept = OrgRepo(db, CreativeConcept, org_id).get(cr.concept_id)
        self.concept = concept.data if concept else {}
        zones = [z for z in (formats.get(k) for k in cr.format_keys or []) if z and z.safe_zone]
        self.safe_top = max((z.safe_zone.top_pct for z in zones), default=0)
        self.safe_bottom = max((z.safe_zone.bottom_pct for z in zones), default=0)
        self.placements = [f"{z.platform}/{z.placement}" for z in (formats.get(k) for k in cr.format_keys or []) if z]
        bp_id = (self.campaign.brief or {}).get("brand_profile_id")
        brand = {}
        if bp_id:
            from app.models import BrandProfile
            bp = OrgRepo(db, BrandProfile, org_id).get(uuid.UUID(bp_id))
            brand = {**bp.detected, **bp.overrides} if bp else {}
        self.brand_colors = [c for c in (brand.get("colors") or []) if isinstance(c, str) and len(c) == 7]
        self.tone = brand.get("tone")
        self.palette = list(dict.fromkeys(self.brand_colors + [c for c in self.concept.get("color_palette", []) if isinstance(c, str)]))
        try:
            self.claim_src = marketing.build_context(db, org_id, self.campaign)["src"]
        except Conflict:
            self.claim_src = None
        prods = OrgRepo(db, Product, org_id).list(campaign_id=cr.campaign_id)
        self.expect_logo = bool(prods) and any(a.kind == "logo" for a in OrgRepo(db, ProductAsset, org_id).list(product_id=prods[0].id))
        self.resolve = make_resolver(db, org_id, storage)


def _evaluate(ctx: _Ctx, spec: CreativeSpec) -> tuple[dict, bytes, "object"]:
    full = render_spec(spec, ctx.resolve)
    bare = spec.model_copy(deep=True)
    bare.elements = [e for e in bare.elements if not isinstance(e, TextEl)]
    backdrop_png = render_spec(bare, ctx.resolve).png
    rr = full.report
    report = {"errors": rr.errors, "missing_assets": rr.missing_assets,
              "elements": [{"id": e.id, "type": e.type, "font_size": e.font_size, "lines": e.lines, "text_overflow": e.text_overflow} for e in rr.elements]}
    res = checks.run_checks(checks.CheckInput(spec, full.png, backdrop_png, report, ctx.safe_top, ctx.safe_bottom, ctx.palette, ctx.claim_src, ctx.expect_logo), ctx.pol)
    res["status"] = checks.decide(res["score"], res["findings"], ctx.pol)
    return res, backdrop_png, full


def _backdrop_array(png: bytes):
    return checks._rgb(png)


def assess_creative(db: Session, org_id: uuid.UUID, cr: GeneratedCreative, storage: Storage, pol: QualityPolicy, *, llm: LLMProvider | None = None,
                    use_vision: bool | None = None, auto_correct: bool | None = None) -> QualityReport:
    use_vision = pol.vision_enabled if use_vision is None else use_vision
    auto_correct = pol.auto_correct if auto_correct is None else auto_correct
    ctx = _Ctx(db, org_id, cr, storage, pol)
    original = CreativeSpec.model_validate(cr.spec)
    spec = original
    best, backdrop_png, best_full = _evaluate(ctx, spec)
    initial = {"score": best["score"], "status": best["status"], "blocking": best["blocking"]}
    rounds: list[dict] = []
    tried: set = set()

    def correct(spec, best, backdrop_png, fixes_from, label):
        cctx = corrections.CorrectionContext(pol, _backdrop_array(backdrop_png), ctx.concept.get("alternative_headlines", []), ctx.palette, ctx.safe_top, ctx.safe_bottom)
        cand, done = corrections.apply_fixes(spec, fixes_from, cctx, tried)
        if not done:
            return spec, best, backdrop_png, None, False
        res, bpng, full = _evaluate(ctx, cand)
        accepted = _rank(res) <= _rank(best)
        rounds.append({"round": label, "actions": done, "score_before": best["score"], "score_after": res["score"], "accepted": accepted})
        if accepted:
            return cand, res, bpng, full, True
        return spec, best, backdrop_png, None, False

    if auto_correct:
        for n in range(1, pol.max_correction_rounds + 1):
            if not any(f["severity"] != "info" and f.get("fix") for f in best["findings"]):
                break
            spec, best, backdrop_png, full, ok = correct(spec, best, backdrop_png, best["findings"], n)
            if full is not None:
                best_full = full
            if not ok:
                break

    # ---- optional vision pass (advisory; one call; real providers only count toward the score)
    vis, vis_findings, vis_meta = None, [], None
    if use_vision and llm is not None:
        try:
            res_llm = vision.assess(llm, best_full.png, width=cr.width, height=cr.height, placements=ctx.placements, brand_colors=ctx.brand_colors, tone=ctx.tone,
                                    headline=next((e.text for e in spec.elements if isinstance(e, TextEl) and e.role == "headline"), ""),
                                    cta=next((getattr(e, "label", "") for e in spec.elements if e.type == "button"), ""))
            vis = res_llm.parsed
            vis_meta = {"provider": res_llm.provider, "model": res_llm.model, "prompt_version": vision.PROMPT_VERSION}
            marketing.record_usage(db, org_id, cr.campaign_id, "llm_vision", res_llm, creative_id=str(cr.id))
        except LLMError as e:
            vis_meta = {"error": str(e)[:200]}
    if vis is not None and vis.assessed:
        btn = next((e.id for e in spec.elements if e.type == "button"), None)
        head = next((e.id for e in spec.elements if isinstance(e, TextEl) and e.role == "headline"), None)
        for i in vis.issues:
            vis_findings.append({"code": i.code, "category": VISION_CATEGORY.get(i.code, "brand"), "severity": i.severity, "message": i.description, "source": "vision",
                                 "element_id": btn if i.code == "cta_not_prominent" else (head if i.code == "unreadable_text" else None),
                                 "value": None, "threshold": None, "fix": vision.AUTO_FIX.get(i.code)})
        if auto_correct and any(f["fix"] for f in vis_findings):
            spec, best, backdrop_png, full, ok = correct(spec, best, backdrop_png, [f for f in vis_findings if f["fix"]], "vision")
            if full is not None:
                best_full = full

    # ---- combine
    real_vision = bool(vis is not None and vis.assessed and ((vis_meta or {}).get("provider") != "mock" or pol.count_mock_vision))
    vscore = vision.vision_score(vis) if real_vision else None
    score = best["score"] if vscore is None else int(round(best["score"] * (1 - pol.vision_weight) + vscore * pol.vision_weight))
    if best["blocking"]:
        score = min(score, pol.fail_below - 1)
    all_findings = best["findings"] + vis_findings
    status = checks.decide(score, all_findings, pol)
    recs = []
    for f in all_findings:
        if f["severity"] == "info":
            continue
        if f["code"] in vision.REGEN:
            recs.append({"code": f["code"], "message": f["message"], "suggestion": "Regenerate the background (new AI image) — this cannot be fixed by layout changes", "action": "regenerate_background"})
        elif f["severity"] == "fail" or not f.get("fix"):
            recs.append({"code": f["code"], "message": f["message"], "suggestion": "Adjust manually in the editor", "action": "edit"})
    report = {"score": score, "status": status, "deterministic_score": best["score"], "categories": best["categories"], "findings": all_findings,
              "passed": best["passed"], "blocking": best["blocking"] + [f["code"] for f in vis_findings if f["severity"] == "fail"],
              "initial": initial, "corrections": rounds, "recommendations": recs,
              "vision": None if not use_vision else {"requested": True, **(vis_meta or {"skipped": "no LLM provider available"}),
                                                     "assessed": bool(vis and vis.assessed), "counted_in_score": vscore is not None, "assessed_before_vision_corrections": True,
                                                     "score": vscore, "scores": vis.scores.model_dump() if vis else None,
                                                     "summary": vis.summary if vis else None},
              "policy": pol.model_dump(), "spec_version_assessed": cr.spec_version}
    changed = spec != original
    if changed:
        cr.spec = spec.model_dump(mode="json")
        notes = list(cr.spec["meta"].get("notes", []))
        cr.spec["meta"]["notes"] = (notes + [f"Quality auto-correction: {len(sum((r['actions'] for r in rounds if r['accepted']), []))} change(s) applied"])[-20:]
        cr.spec_version += 1
        _persist_render(db, storage, org_id, cr, render_spec(CreativeSpec.model_validate(cr.spec), ctx.resolve))
    report["spec_version_assessed"] = cr.spec_version
    row = OrgRepo(db, QualityReport, org_id).add(creative_id=cr.id, campaign_id=cr.campaign_id, spec_version=cr.spec_version, score=score, status=status,
                                                 report=report, original_spec=original.model_dump(mode="json") if changed else None)
    cr.quality_score, cr.quality_status, cr.quality_spec_version = score, status, cr.spec_version
    db.flush()
    return row


def revert_corrections(db: Session, org_id: uuid.UUID, cr: GeneratedCreative, storage: Storage) -> GeneratedCreative:
    """Restore the spec as it was before the latest auto-correction (the original is kept on the report row)."""
    from app.creative.service import rerender
    rows = [r for r in OrgRepo(db, QualityReport, org_id).list(creative_id=cr.id) if r.original_spec and r.spec_version == cr.spec_version]
    if not rows:
        raise ValueError("No auto-correction to revert for the current version")
    rerender(db, org_id, cr, CreativeSpec.model_validate(rows[0].original_spec), storage)
    return cr
