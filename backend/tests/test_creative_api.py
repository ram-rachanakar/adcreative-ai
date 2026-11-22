import io
import uuid

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.ai.image import ImageNotConfigured, ImageProviderError, ImageGenerationProvider
from app.ai.mock_image import MockImageProvider
from app.orchestration import factories
from app.core.db import SessionLocal, set_org
from app.main import app
from app.models import Product
from app.scraping.service import store_asset
from app.storage.base import LocalStorage, get_storage
from test_marketing_api import Spy, use  # noqa: F401  (LLM spy + dependency override helper)


def product_png():
    im = Image.new("RGB", (400, 500), (250, 250, 250))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((120, 60, 280, 440), 30, fill=(20, 80, 200))
    b = io.BytesIO(); im.save(b, "PNG")
    return b.getvalue()


class CountingImages(MockImageProvider):
    def __init__(self):
        self.calls = 0

    def generate_background(self, *a, **k):
        self.calls += 1
        return super().generate_background(*a, **k)


@pytest.fixture(autouse=True)
def env(tmp_path):
    store = LocalStorage(str(tmp_path / "store"))
    app.dependency_overrides[get_storage] = lambda: store
    saved = (factories.storage, factories.image_provider)
    factories.storage = lambda: store
    yield store
    factories.storage, factories.image_provider = saved
    app.dependency_overrides.clear()


def prepared(c, store, n_select=2, with_logo=True, product=True):
    c.register(f"{uuid.uuid4().hex[:8]}@t.example.com")
    ws = c.req("get", "/workspaces").json()[0]["id"]
    pid = c.req("post", "/projects", json={"workspace_id": ws, "name": "P"}).json()["id"]
    cid = c.req("post", "/campaigns", json={"project_id": pid, "name": "C", "brief": {"audience": "hikers", "tone": "bold"}}).json()["id"]
    with SessionLocal() as db:
        set_org(db, uuid.UUID(c.org))
        p = Product(organization_id=uuid.UUID(c.org), campaign_id=uuid.UUID(cid), data={
            "title": "AeroBottle", "description": "Keeps drinks cold. Holds 750 ml.", "features": ["Keeps drinks cold", "Dishwasher safe", "Leak-proof lid"],
            "specifications": {}, "variants": []})
        db.add(p); db.flush()
        if product:
            store_asset(db, store, uuid.UUID(c.org), p, product_png(), "image", None, "upload")
        if with_logo:
            lg = Image.new("RGBA", (200, 80), (0, 0, 0, 0)); ImageDraw.Draw(lg).rectangle((0, 0, 199, 79), fill=(10, 160, 90, 255))
            b = io.BytesIO(); lg.save(b, "PNG"); store_asset(db, store, uuid.UUID(c.org), p, b.getvalue(), "logo", None, "upload")
        db.commit()
    use(Spy())
    concepts = c.sync("post", f"/campaigns/{cid}/concepts/generate", json={}).json()
    c.req("post", f"/campaigns/{cid}/concepts/select", json={"concept_ids": [x["id"] for x in concepts[:n_select]]})
    c.sync("post", f"/campaigns/{cid}/briefs/generate")
    return cid


class Gen:
    """Eager Celery runs the job inside the POST, so by the time we get 202 the job is finished. Mimics the old sync helper."""
    def __init__(self, r, creatives=None, job=None):
        self.status_code, self._r, self._creatives, self.job = (200 if r.status_code == 202 else r.status_code), r, creatives, job
        self.text = r.text

    def json(self):
        return self._creatives if self._creatives is not None else self._r.json()


def gen(c, cid, **body):
    r = c.req("post", f"/campaigns/{cid}/creatives/generate", json=body)
    if r.status_code != 202:
        return Gen(r)
    job = r.json()
    items = [x for x in c.req("get", f"/campaigns/{cid}/creatives").json() if x["job_id"] == job["id"]]
    return Gen(r, items, c.req("get", f"/jobs/{job['id']}").json())


def fetch_png(c, url):
    r = c.http.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def usage(c, cid):
    return {r["kind"]: r for r in c.req("get", f"/campaigns/{cid}/usage").json()["by_kind"]}


def test_end_to_end_real_images_exact_sizes(make_client, env):
    c = make_client()
    cid = prepared(c, env)
    img = CountingImages()
    factories.image_provider = lambda: img
    r = gen(c, cid)
    assert r.status_code == 200, r.text
    out = r.json()
    # 2 concepts x 3 sizes. The mock LLM gives both concepts the same background prompt, so the org-scoped cache serves one image
    assert len(out) == 6 and img.calls == 1
    for cr in out:
        im = fetch_png(c, cr["url"])
        assert im.size == (cr["width"], cr["height"]) and cr["status"] == "rendered" and cr["render_report"]["ok"]
        assert np.asarray(im).std() > 10  # not a blank canvas
    assert {(o["width"], o["height"]) for o in out} == {(1080, 1080), (1200, 628), (1080, 1350)}
    u = usage(c, cid)
    assert u["image_generation"]["calls"] == 1 and u["image_generation"]["priced"] is False
    assert c.req("get", f"/campaigns/{cid}").json()["status"] == "creatives_ready"


def test_distinct_prompts_get_distinct_backgrounds_reused_across_sizes(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=2)
    cs = c.req("get", f"/campaigns/{cid}/concepts").json()
    sel = [x for x in cs if x["selected"]]
    c.req("patch", f"/concepts/{sel[1]['id']}", json={"background_description": "Misty forest trail at dawn"})
    c.sync("post", f"/campaigns/{cid}/briefs/generate")
    img = CountingImages()
    factories.image_provider = lambda: img
    assert len(gen(c, cid).json()) == 6
    assert img.calls == 2 and usage(c, cid)["image_generation"]["calls"] == 2  # one per concept, NOT one per size


def test_background_cache_reused_and_regeneration_explicit(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    img = CountingImages()
    factories.image_provider = lambda: img
    gen(c, cid, sizes=[{"width": 1080, "height": 1080}])
    gen(c, cid, sizes=[{"width": 1200, "height": 628}])
    assert img.calls == 1
    gen(c, cid, sizes=[{"width": 1080, "height": 1080}], regenerate_background=True)
    assert img.calls == 2
    assert usage(c, cid)["image_generation"]["calls"] == 2


def test_product_preserved_not_distorted_and_original_untouched(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    cr = gen(c, cid, sizes=[{"width": 1200, "height": 628}]).json()[0]
    spec = c.req("get", f"/creatives/{cr['id']}").json()
    prod = next(e for e in spec["spec"]["elements"] if e["id"] == "product")
    assert prod["shadow"] == "contact" and prod["fit"] == "contain"
    assert any("cut-out: border_flood" in n for n in spec["spec"]["meta"]["notes"])
    rep = next(e for e in spec["render_report"]["elements"] if e["id"] == "product")
    assert rep["out_of_bounds"] is False
    # the stored original product image is byte-identical to what was uploaded
    p = c.req("get", f"/campaigns/{cid}/product").json()
    orig = next(a for a in p["assets"] if a["kind"] == "image")
    assert c.http.get(orig["url"]).content == product_png()


def test_text_only_edit_makes_no_ai_calls_and_bumps_version(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    img = CountingImages()
    factories.image_provider = lambda: img
    cr = gen(c, cid, sizes=[{"width": 1080, "height": 1080}]).json()[0]
    before_calls, before_usage = img.calls, usage(c, cid)["image_generation"]["calls"]
    d = c.req("get", f"/creatives/{cr['id']}").json()
    spec = d["spec"]
    next(e for e in spec["elements"] if e["id"] == "headline")["text"] = "A brand new headline"
    next(e for e in spec["elements"] if e["id"] == "cta")["fill"] = "#00aa44"
    r = c.req("put", f"/creatives/{cr['id']}/spec", json=spec)
    assert r.status_code == 200, r.text
    assert r.json()["spec_version"] == 2 and r.json()["url"] != d["url"]
    assert img.calls == before_calls and usage(c, cid)["image_generation"]["calls"] == before_usage
    assert fetch_png(c, r.json()["url"]).size == (1080, 1080)


def test_spec_update_validation_and_tenant_safety(make_client, env):
    a, b = make_client(), make_client()
    cida, cidb = prepared(a, env, n_select=1), prepared(b, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    ca = gen(a, cida, sizes=[{"width": 1080, "height": 1080}]).json()[0]
    cb = gen(b, cidb, sizes=[{"width": 1080, "height": 1080}]).json()[0]
    sa = a.req("get", f"/creatives/{ca['id']}").json()["spec"]
    assert b.req("get", f"/creatives/{ca['id']}").status_code == 404
    assert b.req("put", f"/creatives/{ca['id']}/spec", json=sa).status_code == 404
    # B tries to reference A's background asset inside B's own creative
    sb = b.req("get", f"/creatives/{cb['id']}").json()["spec"]
    sb["background"]["asset_id"] = sa["background"]["asset_id"]
    r = b.req("put", f"/creatives/{cb['id']}/spec", json=sb)
    assert r.status_code == 422 and "not found" in r.json()["detail"]
    # invalid specs
    bad = {**sa, "elements": sa["elements"] + [{"type": "html", "id": "x", "x": 0, "y": 0, "w": 1, "h": 1}]}
    assert a.req("put", f"/creatives/{ca['id']}/spec", json=bad).status_code == 422
    bad = {**sa, "canvas": {"width": 500, "height": 500}}
    assert a.req("put", f"/creatives/{ca['id']}/spec", json=bad).status_code == 422
    sa["elements"][0]["color"] = "javascript:alert(1)"
    assert a.req("put", f"/creatives/{ca['id']}/spec", json=sa).status_code == 422


def test_overflow_is_reported_not_hidden(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    cr = gen(c, cid, sizes=[{"width": 1080, "height": 1080}]).json()[0]
    spec = c.req("get", f"/creatives/{cr['id']}").json()["spec"]
    next(e for e in spec["elements"] if e["id"] == "headline")["text"] = "Supercalifragilisticexpialidocious " * 6
    r = c.req("put", f"/creatives/{cr['id']}/spec", json=spec).json()
    assert r["status"] == "needs_attention" and any(e["text_overflow"] for e in r["render_report"]["elements"])


def test_preconditions_and_unsupported_size(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: CountingImages()
    assert gen(c, cid, sizes=[{"width": 10, "height": 10}]).status_code == 422
    assert gen(c, cid, format_ids=["nope"]).status_code == 422
    assert "image_generation" not in usage(c, cid)  # rejected before spending anything
    c2 = make_client()
    c2.register("x@t.example.com")
    ws = c2.req("get", "/workspaces").json()[0]["id"]
    pid = c2.req("post", "/projects", json={"workspace_id": ws, "name": "P"}).json()["id"]
    cid2 = c2.req("post", "/campaigns", json={"project_id": pid, "name": "C"}).json()["id"]
    assert gen(c2, cid2).status_code == 409


def test_no_product_image_blocks(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1, product=False, with_logo=False)
    factories.image_provider = lambda: MockImageProvider()
    r = gen(c, cid)
    assert r.status_code == 409 and "product image" in r.json()["detail"]


def test_provider_failures_become_failed_jobs_not_silent_fallbacks(make_client, env, monkeypatch):
    class Boom(MockImageProvider):
        def __init__(self, exc): self.exc, self.n = exc, 0
        def generate_background(self, *a, **k):
            self.n += 1
            raise self.exc
    from app.orchestration import jobs as J
    c = make_client()
    cid = prepared(c, env, n_select=1)
    boom = Boom(ImageProviderError("down"))
    factories.image_provider = lambda: boom
    g = gen(c, cid, sizes=[{"width": 1080, "height": 1080}])
    assert g.status_code == 200 and g.job["status"] == "failed" and "Background generation failed" in g.job["error"]
    assert boom.n == 3                                   # retried with backoff, then gave up (no cheaper-model substitution)
    assert g.json() == []                                # nothing half-baked was left behind
    assert "image_generation" not in usage(c, cid)
    factories.image_provider = lambda: Boom(ImageNotConfigured("no key"))
    g = gen(c, cid, sizes=[{"width": 1080, "height": 1080}])
    assert g.job["status"] == "failed" and "no key" in g.job["error"]


def test_cache_never_shared_across_organizations(make_client, env):
    a, b = make_client(), make_client()
    cida, cidb = prepared(a, env, n_select=1), prepared(b, env, n_select=1)
    img = CountingImages()
    factories.image_provider = lambda: img
    gen(a, cida, sizes=[{"width": 1080, "height": 1080}])
    gen(b, cidb, sizes=[{"width": 1080, "height": 1080}])
    assert img.calls == 2  # identical prompts, but each org generates (and owns) its own asset
    from sqlalchemy import text
    from test_marketing_api import SessionLocal as S
    with S() as db:
        keys = set()
        for o in (a.org, b.org):
            set_org(db, uuid.UUID(o))
            keys |= {r[0] for r in db.execute(text("select cache_key from creative_assets where kind='background'"))}
        assert len(keys) == 2


def test_viewer_can_view_not_generate(make_client, env):
    o, v = make_client(), make_client()
    cid = prepared(o, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    cr = gen(o, cid, sizes=[{"width": 1080, "height": 1080}]).json()[0]
    v.register("viewer@t.example.com", "V")
    o.req("post", "/members", json={"email": "viewer@t.example.com", "role": "viewer"})
    v.org = o.org
    assert v.req("get", f"/campaigns/{cid}/creatives").status_code == 200
    assert gen(v, cid).status_code == 403
    spec = v.req("get", f"/creatives/{cr['id']}").json()["spec"]
    assert v.req("put", f"/creatives/{cr['id']}/spec", json=spec).status_code == 403


def test_rls_covers_creative_tables(make_client, env):
    from sqlalchemy import text
    a, b = make_client(), make_client()
    cida = prepared(a, env, n_select=1)
    prepared(b, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    gen(a, cida, sizes=[{"width": 1080, "height": 1080}])
    with SessionLocal() as db:
        for t in ("creative_assets", "generated_creatives"):
            set_org(db, uuid.UUID(b.org))
            assert db.execute(text(f"select count(*) from {t}")).scalar() == 0
            set_org(db, uuid.UUID(a.org))
            assert db.execute(text(f"select count(*) from {t}")).scalar() > 0
