import uuid

import pytest
from sqlalchemy import text

from app.ai.image import ImageProviderError
from app.ai.mock_image import MockImageProvider
from app.core.db import SessionLocal, set_org
from app.creative import service as csvc
from app.orchestration import factories, jobs
from app.workers import tasks
from test_creative_api import CountingImages, env, fetch_png, gen, prepared, usage  # noqa: F401
from test_creative_api import product_png  # noqa: F401

TEN = ["google_display_medium_rectangle_300x250", "google_display_leaderboard_728x90", "google_display_half_page_300x600", "google_display_wide_skyscraper_160x600",
       "google_display_mobile_banner_320x50", "instagram_feed_portrait_1080x1350", "instagram_story_1080x1920", "facebook_link_landscape_1200x630",
       "linkedin_single_image_square_1200x1200", "pinterest_standard_pin_1000x1500", "x_image_16x9_1600x900", "google_display_billboard_970x250"]


def job_of(c, jid):
    return c.req("get", f"/jobs/{jid}").json()


def test_twelve_formats_in_one_job_exact_sizes_and_lifecycle(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: CountingImages()
    r = c.req("post", f"/campaigns/{cid}/creatives/generate", json={"format_ids": TEN})
    assert r.status_code == 202
    j = job_of(c, r.json()["id"])
    assert j["status"] == "completed" and j["progress"] == 100 and j["planned_units"] == 12 and j["error"] is None
    assert len(j["results"]) == 12 and all(x["status"] == "ok" for x in j["results"])
    cs = [x for x in c.req("get", f"/campaigns/{cid}/creatives").json() if x["job_id"] == j["id"]]
    assert len({(x["width"], x["height"]) for x in cs}) == 12
    for x in cs:
        assert fetch_png(c, x["url"]).size == (x["width"], x["height"]) and x["status"] == "rendered" and x["format_keys"]
    assert usage(c, cid)["image_generation"]["calls"] == 1            # one background for 12 banners


def test_partial_failure_is_reported_and_others_survive(make_client, env, monkeypatch):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    real = csvc.compose

    def flaky(inp, w, h):
        if (w, h) == (728, 90):
            raise RuntimeError("layout exploded")
        return real(inp, w, h)
    monkeypatch.setattr(csvc, "compose", flaky)
    g = gen(c, cid, format_ids=["google_display_leaderboard_728x90", "google_display_medium_rectangle_300x250"])
    assert g.job["status"] == "partially_completed" and "1 of 2" in g.job["error"]
    bad = next(r for r in g.job["results"] if r["status"] == "failed")
    assert (bad["width"], bad["height"]) == (728, 90) and "layout exploded" in bad["error"]
    assert [(x["width"], x["height"]) for x in g.json()] == [(300, 250)]       # no half-built row for the failed size


def test_backoff_then_success(make_client, env, monkeypatch):
    sleeps = []
    monkeypatch.setattr(jobs, "sleep_fn", sleeps.append)

    class Flaky(MockImageProvider):
        n = 0
        def generate_background(self, *a, **k):
            Flaky.n += 1
            if Flaky.n < 3:
                raise ImageProviderError("rate limited")
            return super().generate_background(*a, **k)
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: Flaky()
    g = gen(c, cid, sizes=[{"width": 1080, "height": 1080}])
    assert g.job["status"] == "completed" and sleeps == [1, 2] and len(g.json()) == 1


def test_one_concept_failing_does_not_sink_the_other(make_client, env):
    class OneBad(MockImageProvider):
        def generate_background(self, prompt, *a, **k):
            if "forest" in prompt:
                raise ImageProviderError("blocked")
            return super().generate_background(prompt, *a, **k)
    c = make_client()
    cid = prepared(c, env, n_select=2)
    sel = [x for x in c.req("get", f"/campaigns/{cid}/concepts").json() if x["selected"]]
    c.req("patch", f"/concepts/{sel[1]['id']}", json={"background_description": "Misty forest trail"})
    c.sync("post", f"/campaigns/{cid}/briefs/generate")
    factories.image_provider = lambda: OneBad()
    g = gen(c, cid, sizes=[{"width": 1080, "height": 1080}])
    assert g.job["status"] == "partially_completed" and len(g.json()) == 1
    assert any("Background generation failed" in (r["error"] or "") for r in g.job["results"])


def test_cancel_pending_job(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: CountingImages()
    with SessionLocal() as db:
        from app.models import Campaign
        from app.repositories.org_repo import OrgRepo
        set_org(db, uuid.UUID(c.org))
        camp = OrgRepo(db, Campaign, uuid.UUID(c.org)).get(uuid.UUID(cid))
        job, _ = jobs.create_job(db, uuid.UUID(c.org), camp, jobs.resolve_sizes([], [(1080, 1080)]))   # created, never dispatched
        db.commit()
        jid = str(job.id)
    assert c.req("post", f"/campaigns/{cid}/creatives/generate", json={}).status_code == 409           # one active job at a time
    r = c.req("post", f"/jobs/{jid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert c.req("post", f"/jobs/{jid}/cancel").status_code == 409                                        # already terminal
    tasks.run_banner_job.apply(args=[c.org, jid])                                                          # a late worker pickup does nothing
    assert job_of(c, jid)["status"] == "cancelled" and job_of(c, jid)["results"] == []


def test_cancel_mid_run_keeps_finished_work(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=2)
    sel = [x for x in c.req("get", f"/campaigns/{cid}/concepts").json() if x["selected"]]
    c.req("patch", f"/concepts/{sel[1]['id']}", json={"background_description": "Misty forest trail"})
    c.sync("post", f"/campaigns/{cid}/briefs/generate")

    class CancelAfterFirst(MockImageProvider):
        calls = 0

        def generate_background(self, *a, **k):
            r = super().generate_background(*a, **k)
            CancelAfterFirst.calls += 1
            if CancelAfterFirst.calls < 2:       # cancel arrives while the SECOND concept's background is being made
                return r
            from tests.conftest import owner_engine  # noqa
            with owner_engine.begin() as cx:
                cx.execute(text("update generation_jobs set cancel_requested = true"))
            return r
    factories.image_provider = lambda: CancelAfterFirst()
    g = gen(c, cid, sizes=[{"width": 1080, "height": 1080}, {"width": 1200, "height": 628}])
    assert g.job["status"] == "cancelled"
    assert len(g.job["results"]) == 2                           # concept 1's two banners were kept; concept 2 never started
    assert len(g.json()) == len(g.job["results"])


def test_idempotency_key_returns_same_job(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    img = CountingImages()
    factories.image_provider = lambda: img
    h = {"idempotency-key": "abc-123"}
    r1 = c.req("post", f"/campaigns/{cid}/creatives/generate", json={"sizes": [{"width": 1080, "height": 1080}]}, headers=h)
    r2 = c.req("post", f"/campaigns/{cid}/creatives/generate", json={"sizes": [{"width": 1080, "height": 1080}]}, headers=h)
    assert r1.status_code == 202 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert img.calls == 1 and len(c.req("get", f"/campaigns/{cid}/jobs").json()) == 1


def test_worker_crash_retries_then_dead_letters(make_client, env, monkeypatch):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    calls = {"n": 0}
    real = jobs.run_job

    def crash(*a, **k):
        calls["n"] += 1
        raise RuntimeError("worker blew up")
    monkeypatch.setattr(jobs, "run_job", crash)
    factories.image_provider = lambda: CountingImages()
    g = gen(c, cid, sizes=[{"width": 1080, "height": 1080}])
    j = g.job
    assert calls["n"] == 3 and j["status"] == "failed" and j["dead_lettered"] is True and "worker blew up" in j["error"]


def test_resume_skips_completed_units_and_cost_not_double_counted(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    img = CountingImages()
    factories.image_provider = lambda: img
    g = gen(c, cid, sizes=[{"width": 1080, "height": 1080}, {"width": 1200, "height": 628}])
    done = {r["unit"] for r in g.job["results"] if r["status"] == "ok"}
    before = len(c.req("get", f"/campaigns/{cid}/creatives").json())
    with SessionLocal() as db:
        from app.models import Campaign
        from app.repositories.org_repo import OrgRepo
        set_org(db, uuid.UUID(c.org))
        camp = OrgRepo(db, Campaign, uuid.UUID(c.org)).get(uuid.UUID(cid))
        stats = csvc.generate_creatives(db, uuid.UUID(c.org), camp, img, factories.make_storage(), [csvc.SizeReq(1080, 1080), csvc.SizeReq(1200, 628)], done=done)
        db.commit()
    assert img.calls == 1 and stats["cost"] == 0.0 and len(c.req("get", f"/campaigns/{cid}/creatives").json()) == before


def test_regenerate_single_creative_replaces_it(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    img = CountingImages()
    factories.image_provider = lambda: img
    cr = gen(c, cid, sizes=[{"width": 1080, "height": 1080}, {"width": 1200, "height": 628}]).json()
    target = next(x for x in cr if x["width"] == 1080)
    other = next(x for x in cr if x["width"] == 1200)
    r = c.req("post", f"/creatives/{target['id']}/regenerate")
    assert r.status_code == 202
    now = {x["id"]: x for x in c.req("get", f"/campaigns/{cid}/creatives").json()}
    assert target["id"] not in now and other["id"] in now and len(now) == 2 and img.calls == 1      # cache reused; others untouched
    r = c.req("post", f"/creatives/{next(i for i, x in now.items() if x['width'] == 1080)}/regenerate?new_background=true")
    assert r.status_code == 202 and img.calls == 2


def test_safe_zone_applied_to_story_in_pipeline(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    cr = gen(c, cid, format_ids=["instagram_story_1080x1920"]).json()[0]
    spec = c.req("get", f"/creatives/{cr['id']}").json()["spec"]
    assert all(e["y"] >= 1920 * 0.14 - 1 and e["y"] + e["h"] <= 1920 * 0.80 + 1 for e in spec["elements"])


def test_job_isolation_and_roles(make_client, env):
    a, b, v = make_client(), make_client(), make_client()
    cida = prepared(a, env, n_select=1)
    prepared(b, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    g = gen(a, cida, sizes=[{"width": 1080, "height": 1080}])
    jid = g.job["id"]
    assert b.req("get", f"/jobs/{jid}").status_code == 404
    assert b.req("post", f"/jobs/{jid}/cancel").status_code == 404
    assert b.req("get", f"/campaigns/{cida}/jobs").status_code == 404
    assert b.req("post", f"/creatives/{g.json()[0]['id']}/regenerate").status_code == 404
    assert gen(b, cida).status_code == 404
    v.register("viewer@t.example.com", "V")
    a.req("post", "/members", json={"email": "viewer@t.example.com", "role": "viewer"})
    v.org = a.org
    assert v.req("get", f"/jobs/{jid}").status_code == 200
    assert gen(v, cida).status_code == 403
    assert v.req("post", f"/creatives/{g.json()[0]['id']}/regenerate").status_code == 403


def test_rls_covers_job_tables(make_client, env):
    a, b = make_client(), make_client()
    cida = prepared(a, env, n_select=1)
    prepared(b, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    gen(a, cida, sizes=[{"width": 1080, "height": 1080}])
    with SessionLocal() as db:
        for t in ("generation_jobs",):
            set_org(db, uuid.UUID(b.org))
            assert db.execute(text(f"select count(*) from {t}")).scalar() == 0
            set_org(db, uuid.UUID(a.org))
            assert db.execute(text(f"select count(*) from {t}")).scalar() > 0
