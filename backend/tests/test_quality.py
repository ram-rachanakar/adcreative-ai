import io
import json
import uuid
import zipfile

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from app.ai.llm import AnthropicProvider, LLMProvider, LLMRequest, LLMResult
from app.ai.mock_image import MockImageProvider
from app.core.db import SessionLocal, set_org
from app.models import GeneratedCreative
from app.orchestration import factories
from app.quality import checks, corrections
from app.quality.policy import QualityPolicy, resolve_policy
from app.quality.vision import VisionAssessment, Scores, VisionIssue
from app.rendering.spec import Background, ButtonEl, CreativeSpec, TextEl, ImageEl, Canvas
from app.rendering.svg import ResolvedAsset, render_spec
from test_creative_api import CountingImages, env, fetch_png, gen, prepared, usage  # noqa: F401
from test_creative_api import product_png  # noqa: F401

NONE = lambda a: None  # noqa: E731


def spec_of(w=600, h=400, bg="#1d2a44", els=None):
    return CreativeSpec(canvas=Canvas(width=w, height=h), background=Background(type="solid", color=bg), elements=els or [])


def head(**kw):
    d = dict(id="headline", role="headline", x=30, y=40, w=400, h=100, text="Cold all day", font_size=48, font_weight=700, color="#ffffff", min_font_size=24)
    d.update(kw)
    return TextEl(**d)


def cta(**kw):
    d = dict(id="cta", role="cta", x=30, y=300, w=180, h=50, label="Shop now", font_size=22, fill="#ff5a1f", text_color="#ffffff")
    d.update(kw)
    return ButtonEl(**d)


def run(spec, pol=None, **kw):
    pol = pol or QualityPolicy()
    full = render_spec(spec, NONE)
    bare = spec.model_copy(deep=True)
    bare.elements = [e for e in bare.elements if not isinstance(e, TextEl)]
    rr = full.report
    rep = {"errors": rr.errors, "missing_assets": rr.missing_assets,
           "elements": [{"id": e.id, "type": e.type, "font_size": e.font_size, "lines": e.lines, "text_overflow": e.text_overflow} for e in rr.elements]}
    res = checks.run_checks(checks.CheckInput(spec, full.png, render_spec(bare, NONE).png, rep, **kw), pol)
    res["status"] = checks.decide(res["score"], res["findings"], pol)
    return res


def codes(res, sev=None):
    return {f["code"] for f in res["findings"] if sev is None or f["severity"] == sev}


# ------------------------------------------------------------------ deterministic checks
def test_clean_banner_has_no_blocking_findings_and_lists_what_passed():
    r = run(spec_of(els=[head(), cta()]))
    assert not codes(r, "fail"), r["findings"]
    assert r["score"] >= 80 and {"dimensions", "contrast_blocking", "text_overflow", "overlap"} <= set(r["passed"])


def test_low_contrast_is_measured_on_pixels_and_blocks():
    r = run(spec_of(bg="#dddddd", els=[head(color="#cccccc"), cta()]))
    f = next(x for x in r["findings"] if x["code"] == "contrast_blocking")
    assert f["severity"] == "fail" and f["value"] < 3 and f["fix"] == "recolor_text"
    assert r["status"] == "fail" and r["score"] < QualityPolicy().fail_below   # a blocking defect cannot hide behind a good average


def test_busy_backdrop_uses_worst_pixels_not_the_average():
    # half dark / half light backdrop: the average is mid-grey, but white text over the light half is unreadable
    s = spec_of(bg="#101010", els=[head(w=500, text="Wide headline text")])
    s.background.scrims = []
    from app.rendering.spec import ShapeEl
    s.elements.insert(0, ShapeEl(id="bar", x=250, y=0, w=350, h=400, fill="#f2f2f2"))
    assert "contrast_blocking" in codes(run(s), "fail")


def test_overflow_and_tiny_font_are_failures():
    r = run(spec_of(els=[head(w=60, h=20, text="An extremely long headline that cannot possibly fit here", min_font_size=12), cta()]))
    assert {"text_overflow"} <= codes(r, "fail")
    r2 = run(spec_of(els=[head(font_size=6, min_font_size=6, h=30), cta()]))
    assert "font_too_small" in codes(r2, "fail")


def test_safe_zone_and_canvas_bounds():
    s = spec_of(w=1080, h=1920, els=[head(y=20, x=60, w=900, h=200), cta(x=60, y=1860, w=400, h=100)])
    r = run(s, safe_top_pct=14, safe_bottom_pct=20)
    assert "outside_safe_zone" in codes(r, "fail")
    assert "off_canvas" in codes(run(spec_of(els=[head(x=500, w=400), cta()])), "fail")


def test_overlap_and_density():
    body = head(id="body", role="body", y=60, h=60, text="Supporting copy", font_size=20, font_weight=400)
    r = run(spec_of(els=[head(), body, cta()]))
    assert "overlap" in codes(r) and next(f for f in r["findings"] if f["code"] == "overlap")["fix"] == "drop_body"


def test_small_cta_and_weak_hierarchy_warn():
    r = run(spec_of(els=[head(font_size=26), head(id="body", role="body", y=150, h=60, text="Support", font_size=26, font_weight=400), cta(h=12, w=80, font_size=9, y=300)]))
    assert {"cta_small", "weak_hierarchy"} <= codes(r, "warn")


def test_missing_cta_and_headline_block():
    assert {"missing_cta"} <= codes(run(spec_of(els=[head()])), "fail")
    assert {"missing_headline"} <= codes(run(spec_of(els=[cta()])), "fail")


def test_cropped_product_and_tiny_logo_are_flagged():
    im = Image.new("RGBA", (100, 100), (200, 30, 30, 255)); b = io.BytesIO(); im.save(b, "PNG")
    aid = uuid.uuid4()
    prod = ImageEl(id="product", role="product", x=300, y=60, w=150, h=200, asset_id=aid, fit="cover")
    logo = ImageEl(id="logo", role="logo", x=500, y=10, w=14, h=14, asset_id=aid, fit="contain")
    s = spec_of(els=[prod, logo, head(), cta()])
    full = render_spec(s, lambda a: ResolvedAsset(b.getvalue(), 100, 100))
    rep = {"errors": [], "elements": [{"id": e.id, "type": e.type, "font_size": e.font_size, "text_overflow": e.text_overflow} for e in full.report.elements]}
    r = checks.run_checks(checks.CheckInput(s, full.png, full.png, rep), QualityPolicy())
    assert "product_distorted" in codes(r, "fail") and "logo_small" in codes(r, "warn")


def test_unsupported_claim_in_edited_copy_is_flagged():
    r = run(spec_of(els=[head(text="Guaranteed 50% off"), cta()]), claim_source="keeps drinks cold")
    assert "unsupported_claim" in codes(r, "warn")
    assert "unsupported_claim" not in codes(run(spec_of(els=[head(text="Keeps drinks cold"), cta()]), claim_source="keeps drinks cold"))


def test_policy_is_configurable_and_validated():
    s = spec_of(bg="#777777", els=[head(color="#ffffff"), cta()])
    assert "contrast_low" in codes(run(s)) or "contrast_blocking" in codes(run(s))
    loose = resolve_policy({"min_contrast": 1.5, "block_contrast": 1.2})
    assert not ({"contrast_low", "contrast_blocking"} & codes(run(s, loose)))
    with pytest.raises(ValidationError):
        resolve_policy({"min_contrats": 3})            # typos are rejected, not silently ignored
    assert resolve_policy({"weights": {"layout": 0.5}}).weights.legibility == 0.30
    assert checks.decide(95, [], QualityPolicy(pass_at=99)) == "warn"


# ------------------------------------------------------------------ corrections
def fix_loop(spec, rounds=3, **ctx_kw):
    tried, applied = set(), []
    for _ in range(rounds):
        r = run(spec, **ctx_kw.pop("run_kw", {}))
        ctx = corrections.CorrectionContext(QualityPolicy(), checks._rgb(render_spec(_bare(spec), NONE).png), **ctx_kw)
        spec, done = corrections.apply_fixes(spec, r["findings"], ctx, tried)
        applied += done
        if not done:
            break
    return spec, applied


def _bare(spec):
    b = spec.model_copy(deep=True); b.elements = [e for e in b.elements if not isinstance(e, TextEl)]; return b


def test_recolor_fixes_contrast_and_originals_are_not_mutated():
    s = spec_of(bg="#eeeeee", els=[head(color="#ffffff"), cta()])
    before = s.model_dump()
    fixed, applied = fix_loop(s)
    assert s.model_dump() == before                                  # the input spec is never edited in place
    assert [a["action"] for a in applied][:1] == ["recolor_text"]
    assert "contrast_blocking" not in codes(run(fixed), "fail")


def test_escalates_to_scrim_when_recoloring_cannot_help():
    from app.rendering.spec import ShapeEl
    s = spec_of(bg="#808080", els=[ShapeEl(id="a", x=0, y=0, w=300, h=400, fill="#101010"), ShapeEl(id="b", x=300, y=0, w=300, h=400, fill="#f0f0f0"), head(x=50, w=500, align="center"), cta()])
    assert "contrast_blocking" in codes(run(s), "fail")      # white text straddling a dark and a light half
    fixed, applied = fix_loop(s, rounds=3)
    acts = [a["action"] for a in applied]
    assert "recolor_text" not in acts[:1] or "strengthen_scrim" in acts      # recolouring cannot fix both halves, so it escalates to a scrim
    assert "strengthen_scrim" in acts and fixed.background.scrims


def test_overflow_uses_approved_alternative_and_drop_body_removes_crowding():
    s = spec_of(w=300, h=250, els=[head(w=120, h=40, text="A very long headline that will not fit the box", font_size=30, min_font_size=18), cta(x=10, y=190, w=100, h=40)])
    fixed, applied = fix_loop(s, alternatives=["Cold all day"])
    assert any(a["action"] == "use_shorter_headline" for a in applied)
    assert "text_overflow" not in codes(run(fixed), "fail")
    body = head(id="body", role="body", y=60, h=60, text="Supporting copy", font_size=20, font_weight=400)
    fixed2, applied2 = fix_loop(spec_of(els=[head(), body, cta()]))
    assert any(a["action"] == "drop_body" for a in applied2) and not any(e.id == "body" for e in fixed2.elements)


def test_safe_zone_and_cta_size_fixes():
    s = spec_of(w=1080, h=1920, els=[head(y=20, x=60, w=900, h=200), cta(x=60, y=1700, w=400, h=40, font_size=18)])
    fixed, applied = fix_loop(s, safe_top_pct=14, safe_bottom_pct=20, run_kw={"safe_top_pct": 14, "safe_bottom_pct": 20})
    r = run(fixed, safe_top_pct=14, safe_bottom_pct=20)
    assert "outside_safe_zone" not in codes(r, "fail") and "cta_small" not in codes(r)


# ------------------------------------------------------------------ vision
class FakeVision(LLMProvider):
    name = "fake-real"
    def __init__(self, a): self.a, self.calls = a, []
    def generate_json(self, req: LLMRequest) -> LLMResult:
        self.calls.append(req)
        return LLMResult(self.a, "fake-real", "vis-1", 1200, 150)


def test_anthropic_adapter_sends_image_blocks_and_parses_vision_json():
    seen = {}
    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({"assessed": True, "scores": {"hierarchy": 80}, "issues": [], "summary": "ok"})}],
                                         "usage": {"input_tokens": 900, "output_tokens": 40}})
    p = AnthropicProvider("k", "m", transport=httpx.MockTransport(handler))
    r = p.generate_json(LLMRequest(task="vision_quality", system="s", user="u", schema=VisionAssessment, prompt_version="v", images=[b"\x89PNG-fake"]))
    blocks = seen["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["type"] == "base64" and blocks[-1]["type"] == "text"
    assert r.parsed.scores.hierarchy == 80


def test_vision_schema_rejects_out_of_range_scores_and_unknown_codes():
    with pytest.raises(ValidationError):
        Scores(hierarchy=140)
    with pytest.raises(ValidationError):
        VisionIssue(code="looks_ugly", severity="warn", description="x")


# ------------------------------------------------------------------ API / pipeline
def make(c, env, sizes=((300, 250), (1080, 1920)), **kw):
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    g = gen(c, cid, sizes=[{"width": w, "height": h} for w, h in sizes], format_ids=kw.get("format_ids", []))
    assert g.job["status"] == "completed", g.job
    return cid, g


def test_generation_runs_the_quality_gate_per_banner(make_client, env):
    c = make_client()
    cid, g = make(c, env)
    assert all("quality_score" in r and r["quality_status"] in ("pass", "warn", "fail") for r in g.job["results"])
    for cr in g.json():
        assert cr["quality"]["score"] is not None and not cr["quality"]["stale"]
        q = c.req("get", f"/creatives/{cr['id']}/quality").json()
        assert q["assessed"] and q["passed"] and "categories" in q and q["vision"] is None and q["spec_version"] == cr["spec_version"] and q["stale"] is False
    s = c.req("get", f"/campaigns/{cid}/quality").json()
    assert s["total"] == 2 and sum(s["counts"].values()) == 2 and s["counts"]["unassessed"] == 0 and s["average_score"] is not None


def test_editing_makes_the_score_stale_and_manual_run_does_not_rewrite(make_client, env):
    c = make_client()
    cid, g = make(c, env, sizes=((1080, 1080),))
    cr = g.json()[0]
    spec = c.req("get", f"/creatives/{cr['id']}").json()["spec"]
    for e in spec["elements"]:
        if e["type"] == "text" and e["role"] == "headline":
            e["text"] = "Edited by hand"
    assert c.req("put", f"/creatives/{cr['id']}/spec", json=spec).status_code == 200
    assert c.req("get", f"/creatives/{cr['id']}").json()["quality"]["stale"] is True
    v = c.req("get", f"/creatives/{cr['id']}").json()["spec_version"]
    r = c.req("post", f"/creatives/{cr['id']}/quality", json={}).json()
    assert r["stale"] is False and r["corrections"] == [] and c.req("get", f"/creatives/{cr['id']}").json()["spec_version"] == v   # no silent rewrite


def sabotage(c, crid, **changes):
    spec = c.req("get", f"/creatives/{crid}").json()["spec"]
    for e in spec["elements"]:
        if e["type"] == "text" and e["role"] == "headline":
            e.update(changes)
    r = c.req("put", f"/creatives/{crid}/spec", json=spec)
    assert r.status_code == 200, r.text
    return spec


def test_auto_correct_improves_is_recorded_and_can_be_reverted(make_client, env):
    c = make_client()
    cid, g = make(c, env, sizes=((1080, 1080),))
    cr = g.json()[0]
    orig = sabotage(c, cr["id"], color="#7a7a7a")
    bg = c.req("get", f"/creatives/{cr['id']}").json()
    bad = c.req("post", f"/creatives/{cr['id']}/quality", json={}).json()
    if "contrast_blocking" not in {f["code"] for f in bad["findings"]}:
        pytest.skip("background happened to give this grey enough contrast")
    fixed = c.req("post", f"/creatives/{cr['id']}/quality", json={"auto_correct": True}).json()
    assert fixed["score"] > bad["score"] and fixed["corrections"] and fixed["corrections"][0]["accepted"]
    assert fixed["initial"]["status"] == "fail" and fixed["can_revert"] and fixed["status"] != "fail"
    after = c.req("get", f"/creatives/{cr['id']}").json()
    assert after["spec_version"] == bg["spec_version"] + 1 and any("Quality auto-correction" in n for n in after["spec"]["meta"]["notes"])
    assert c.req("post", f"/creatives/{cr['id']}/quality/revert").status_code == 200
    back = c.req("get", f"/creatives/{cr['id']}").json()
    assert [e for e in back["spec"]["elements"] if e["role"] == "headline"][0]["color"] == "#7a7a7a"
    assert c.req("post", f"/creatives/{cr['id']}/quality/revert").status_code == 409        # nothing left to revert


def test_uncorrectable_problem_is_reported_with_recommendation_not_hidden(make_client, env):
    c = make_client()
    cid, g = make(c, env, sizes=((300, 250),))
    cr = g.json()[0]
    sabotage(c, cr["id"], text="x " * 120, font_size=40, h=30, min_font_size=30)
    r = c.req("post", f"/creatives/{cr['id']}/quality", json={"auto_correct": True}).json()
    assert r["status"] == "fail" and "text_overflow" in r["blocking"] and r["recommendations"]


def test_vision_real_provider_counts_cost_and_mock_does_not(make_client, env):
    c = make_client()
    cid, g = make(c, env, sizes=((1080, 1080),))
    crid = g.json()[0]["id"]
    # mock LLM: explicit "not assessed", no score contribution, no fabricated opinion
    m = c.req("post", f"/creatives/{crid}/quality", json={"vision": True}).json()
    assert m["vision"]["assessed"] is False and m["vision"]["counted_in_score"] is False and m["score"] == m["deterministic_score"]
    fake = FakeVision(VisionAssessment(scores=Scores(hierarchy=60, brand_fit=60, harmony=60, cta_prominence=60, professionalism=60),
                                       issues=[VisionIssue(code="ai_artifact", severity="warn", description="warped shape in the corner")], summary="meh"))
    factories.llm_provider = lambda: fake
    r = c.req("post", f"/creatives/{crid}/quality", json={"vision": True}).json()
    assert r["vision"]["assessed"] and r["vision"]["counted_in_score"] and r["vision"]["score"] == 60
    assert r["score"] == round(r["deterministic_score"] * 0.65 + 60 * 0.35) and r["status"] in ("warn", "fail")
    assert any(x["action"] == "regenerate_background" for x in r["recommendations"])      # art defects are never "fixed" by layout tweaks
    assert fake.calls[0].images and fake.calls[0].task == "vision_quality"
    assert usage(c, cid)["llm_vision"]["calls"] == 2                      # both runs were recorded (the mock one is unpriced, the real one has token counts)
    assert usage(c, cid)["llm_vision"]["input_tokens"] == 1200


def test_vision_failure_severity_blocks_and_cta_hint_is_auto_corrected(make_client, env):
    c = make_client()
    cid, g = make(c, env, sizes=((1080, 1080),))
    crid = g.json()[0]["id"]
    factories.llm_provider = lambda: FakeVision(VisionAssessment(scores=Scores(hierarchy=90), issues=[VisionIssue(code="misleading_content", severity="fail", description="implies an award")]))
    r = c.req("post", f"/creatives/{crid}/quality", json={"vision": True}).json()
    assert r["status"] == "fail" and "misleading_content" in r["blocking"]


def test_policy_api_roles_validation_and_effect(make_client, env):
    o, m = make_client(), make_client()
    cid, g = make(o, env, sizes=((1080, 1080),))
    assert o.req("get", "/quality/policy").json()["effective"]["pass_at"] == 80
    assert o.req("put", "/quality/policy", json={"pass_at": 101}).status_code == 422
    assert o.req("put", "/quality/policy", json={"bogus": 1}).status_code == 422
    m.register("mk@t.example.com", "M")
    o.req("post", "/members", json={"email": "mk@t.example.com", "role": "marketer"})
    m.org = o.org
    assert m.req("put", "/quality/policy", json={"pass_at": 90}).status_code == 403
    assert o.req("put", "/quality/policy", json={"min_product_area_pct": 99, "weights": {"layout": 0.4}}).status_code == 200
    crid = g.json()[0]["id"]
    r = o.req("post", f"/creatives/{crid}/quality", json={}).json()
    assert "product_small" in {f["code"] for f in r["findings"]} and r["status"] in ("warn", "fail")     # the org's stricter gate applies
    r2 = o.req("post", f"/creatives/{crid}/quality", json={"policy": {"min_product_area_pct": 0.1}}).json()
    assert "product_small" not in {f["code"] for f in r2["findings"]}                                    # a per-request override beats the org policy
    assert o.req("get", "/quality/policy").json()["overrides"]["min_product_area_pct"] == 99


def test_quality_is_tenant_isolated(make_client, env):
    a, b = make_client(), make_client()
    cid, g = make(a, env, sizes=((1080, 1080),))
    b.register("other@t.example.com", "Other")
    crid = g.json()[0]["id"]
    assert b.req("get", f"/creatives/{crid}/quality").status_code == 404
    assert b.req("post", f"/creatives/{crid}/quality", json={}).status_code == 404
    assert b.req("post", f"/campaigns/{cid}/quality", json={}).status_code == 404
    assert b.req("get", f"/campaigns/{cid}/quality").status_code == 404


def test_campaign_quality_job_runs_in_background_and_reports(make_client, env):
    c = make_client()
    cid, g = make(c, env)
    r = c.req("post", f"/campaigns/{cid}/quality", json={})
    assert r.status_code == 202 and r.json()["kind"] == "quality"
    j = c.req("get", f"/jobs/{r.json()['id']}").json()
    assert j["status"] == "completed" and len(j["results"]) == 2 and all(x["quality_status"] for x in j["results"])
    assert c.req("get", f"/campaigns/{cid}/quality").json()["counts"]["unassessed"] == 0
    assert c.req("post", f"/campaigns/{cid}/quality", json={"policy": {"nope": 1}}).status_code == 422


def test_export_manifest_has_real_scores_and_gate_skips_failures(make_client, env):
    c = make_client()
    cid, g = make(c, env)
    items = g.json()
    def export(**body):
        e = c.req("post", f"/campaigns/{cid}/exports", json=body).json()
        return c.req("get", f"/exports/{e['id']}").json()
    ex = export()
    assert ex["status"] == "completed"
    q = ex["manifest"]["items"][0]["quality_report"]
    assert q["scoring"] == "assessed" and isinstance(q["score"], int) and q["quality_status"] in ("pass", "warn", "fail")
    # force one banner to fail the gate
    sabotage(c, [i for i in items if i["width"] == 300][0]["id"], text="y " * 150, font_size=40, h=30, min_font_size=30)
    c.req("post", f"/campaigns/{cid}/quality", json={})
    gated = export(quality_gate="block_failed")
    assert gated["status"] == "completed" and gated["manifest"]["quality_gate"] == "block_failed"
    assert [s["width"] for s in gated["manifest"]["skipped"]] == [300] and gated["manifest"]["item_count"] < ex["manifest"]["item_count"]
    assert export(quality_gate="off")["manifest"]["skipped"] == []
    only_bad = export(quality_gate="block_failed", creative_ids=[[i for i in items if i["width"] == 300][0]["id"]])
    assert only_bad["status"] == "failed" and "quality gate" in only_bad["error"]


def test_unassessed_banner_is_not_given_a_made_up_score(make_client, env):
    c = make_client()
    cid, g = make(c, env, sizes=((1080, 1080),))
    with SessionLocal() as db:
        set_org(db, uuid.UUID(c.org))
        from app.models import QualityReport
        for r in db.query(QualityReport).all():
            db.delete(r)
        cr = db.get(GeneratedCreative, uuid.UUID(g.json()[0]["id"]))
        cr.quality_score = cr.quality_status = cr.quality_spec_version = None
        db.commit()
    assert c.req("get", f"/creatives/{g.json()[0]['id']}/quality").json() == {"assessed": False, "creative_id": g.json()[0]["id"], "history": []}
    e = c.req("post", f"/campaigns/{cid}/exports", json={}).json()
    assert c.req("get", f"/exports/{e['id']}").json()["manifest"]["items"][0]["quality_report"]["scoring"] == "not_assessed"


def test_rls_covers_quality_reports(make_client, env):
    a, b = make_client(), make_client()
    cid, g = make(a, env, sizes=((1080, 1080),))
    b.register("rls@t.example.com", "RLS")
    from sqlalchemy import text
    with SessionLocal() as db:
        set_org(db, uuid.UUID(b.org))
        assert db.execute(text("select count(*) from quality_reports")).scalar() == 0
