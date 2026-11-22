import io
import json

import httpx
import pytest
from PIL import Image

from app.main import app
from app.orchestration import factories
from app.scraping import service
from app.scraping.fetcher import SafeFetcher
from app.storage.base import LocalStorage, get_storage


def png(color=(200, 30, 30), size=(120, 90)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


LD = {"@type": "Product", "name": "AeroBottle", "description": "Keeps drinks cold.", "brand": {"@type": "Brand", "name": "Aero", "logo": "/logo.png"},
      "image": ["/a.png", "/b.png", "/missing.png", "/evil.html"], "offers": {"price": "30", "priceCurrency": "USD"}}
PAGE = f'<html><head><script type="application/ld+json">{json.dumps(LD)}</script></head><body></body></html>'


def site(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    img = lambda c: httpx.Response(200, headers={"content-type": "image/png"}, content=png(c))  # noqa: E731
    return {
        "/product": lambda: httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE),
        "/a.png": lambda: img((200, 30, 30)), "/b.png": lambda: img((20, 90, 200)), "/logo.png": lambda: img((10, 160, 90)),
        "/evil.html": lambda: httpx.Response(200, headers={"content-type": "text/html"}, text="<h1>x</h1>"),
    }.get(path, lambda: httpx.Response(404))()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "make_fetcher", lambda: SafeFetcher(resolver=lambda h: ["93.184.216.34"], transport=httpx.MockTransport(site)))
    store = LocalStorage(str(tmp_path / "store"))
    app.dependency_overrides[get_storage] = lambda: store
    saved = factories.storage
    factories.storage = lambda: store
    yield store
    factories.storage = saved
    app.dependency_overrides.clear()


def campaign(c, url="https://shop.example.com/product", name="C"):
    ws = c.req("get", "/workspaces").json()[0]["id"]
    pid = c.req("post", "/projects", json={"workspace_id": ws, "name": "P"}).json()["id"]
    return c.req("post", "/campaigns", json={"project_id": pid, "name": name, "product_url": url, "description": "my notes"}).json()["id"]


def test_analyze_end_to_end(make_client):
    c = make_client()
    c.register("a@a.example.com")
    cid = campaign(c)
    r = c.sync("post", f"/campaigns/{cid}/analyze")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["data"]["title"] == "AeroBottle" and p["data"]["price"] == "30" and p["data"]["user_description"] == "my notes"
    kinds = sorted(a["kind"] for a in p["assets"])
    assert kinds == ["image", "image", "logo"]  # missing.png 404 + evil.html wrong MIME skipped
    assert len(p["extraction_meta"]["warnings"]) == 2
    # stored image is byte-for-byte what the site served (product fidelity) and served via signed URL
    url = next(a["url"] for a in p["assets"] if a["kind"] == "logo")
    got = c.http.get(url)
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert got.content == png((10, 160, 90))
    # brand profile auto-created with a palette from the logo
    brands = c.req("get", "/brand-profiles").json()
    assert brands[0]["name"] == "Aero" and brands[0]["detected"]["color_source"] == "logo"
    assert brands[0]["detected"]["colors"][0] == "#0aa05a"
    assert c.req("get", f"/campaigns/{cid}").json()["status"] == "analyzed"


def test_reanalyze_is_idempotent(make_client):
    c = make_client()
    c.register("a@a.example.com")
    cid = campaign(c)
    c.sync("post", f"/campaigns/{cid}/analyze")
    p = c.sync("post", f"/campaigns/{cid}/analyze").json()
    assert len(p["assets"]) == 3 and len(c.req("get", "/brand-profiles").json()) == 1


def test_edit_product_and_validation(make_client):
    c = make_client()
    c.register("a@a.example.com")
    cid = campaign(c)
    c.sync("post", f"/campaigns/{cid}/analyze")
    r = c.req("patch", f"/campaigns/{cid}/product", json={"title": "Better title", "features": ["Cold 24h"]})
    assert r.json()["data"]["title"] == "Better title" and r.json()["user_edited"] is True
    assert r.json()["data"]["price"] == "30"  # untouched fields preserved
    assert c.req("patch", f"/campaigns/{cid}/product", json={"sources": {"x": "y"}}).status_code == 422  # not editable


def test_ssrf_url_rejected_via_api(make_client):
    c = make_client()
    c.register("a@a.example.com")
    for url in ("http://169.254.169.254/latest/meta-data/", "http://localhost:8000/", "file:///etc/passwd"):
        cid = campaign(c, url)
        r = c.sync("post", f"/campaigns/{cid}/analyze")
        assert r.status_code == 400 and "not allowed" in r.json()["detail"], url


def test_fetch_failure_is_422(make_client):
    c = make_client()
    c.register("a@a.example.com")
    cid = campaign(c, "https://shop.example.com/nope")
    assert c.sync("post", f"/campaigns/{cid}/analyze").status_code == 422


def test_cross_org_isolation(make_client):
    a, b = make_client(), make_client()
    a.register("a@a.example.com", "A")
    b.register("b@b.example.com", "B")
    cid = campaign(a)
    p = a.sync("post", f"/campaigns/{cid}/analyze").json()
    assert b.req("get", f"/campaigns/{cid}/product").status_code == 404
    assert b.sync("post", f"/campaigns/{cid}/analyze").status_code == 404
    assert b.req("patch", f"/campaigns/{cid}/product", json={"title": "pwn"}).status_code == 404
    assert b.req("delete", f"/campaigns/{cid}/product/assets/{p['assets'][0]['id']}").status_code == 404
    assert b.req("get", "/brand-profiles").json() == []
    bid = a.req("get", "/brand-profiles").json()[0]["id"]
    assert b.req("patch", f"/brand-profiles/{bid}", json={"name": "pwn"}).status_code == 404


def test_signed_url_tamper_and_expiry(make_client):
    c = make_client()
    c.register("a@a.example.com")
    cid = campaign(c)
    url = c.sync("post", f"/campaigns/{cid}/analyze").json()["assets"][0]["url"]
    assert c.http.get(url).status_code == 200
    assert c.http.get(url.replace("sig=", "sig=0")).status_code == 403
    path = url.split("/files/")[1].split("?")[0]
    assert c.http.get(f"/files/{path}").status_code == 422  # missing signature params
    assert c.http.get(f"/files/{path}?exp=1&sig=abc").status_code == 403
    assert c.http.get("/files/../../etc/passwd?exp=9999999999&sig=x").status_code in (403, 404)


def test_upload_validation(make_client):
    c = make_client()
    c.register("a@a.example.com")
    cid = campaign(c)
    c.sync("post", f"/campaigns/{cid}/analyze")
    up = lambda data, name="x.png": c.req("post", f"/campaigns/{cid}/product/assets?kind=image", files={"file": (name, data, "image/png")})  # noqa: E731
    assert up(png((1, 2, 3), (64, 64))).status_code == 201
    assert up(png((1, 2, 3), (64, 64))).status_code == 409  # duplicate
    assert up(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "x.svg").status_code == 422
    assert up(b"MZ\x90\x00 not an image", "evil.png").status_code == 422
    assert up(png((1, 2, 3), (8, 8))).status_code == 422  # too small
    assert up(b"GIF89a" + b"\x00" * 50, "x.gif").status_code == 422


def test_viewer_cannot_mutate(make_client):
    o, v = make_client(), make_client()
    o.register("o@o.example.com", "O")
    v.register("v@v.example.com", "V")
    o.req("post", "/members", json={"email": "v@v.example.com", "role": "viewer"})
    cid = campaign(o)
    o.sync("post", f"/campaigns/{cid}/analyze")
    v.org = o.org
    assert v.req("get", f"/campaigns/{cid}/product").status_code == 200
    assert v.sync("post", f"/campaigns/{cid}/analyze").status_code == 403
    assert v.req("patch", f"/campaigns/{cid}/product", json={"title": "x"}).status_code == 403
    assert v.req("post", "/brand-profiles", json={"name": "x"}).status_code == 403


def test_brand_overrides_win_and_validate(make_client):
    c = make_client()
    c.register("a@a.example.com")
    r = c.req("post", "/brand-profiles", json={"name": "Mine", "overrides": {"colors": ["#112233"], "fonts": ["Inter"]}, "guidelines": "Be kind"})
    assert r.status_code == 201 and r.json()["effective"]["colors"] == ["#112233"]
    bid = r.json()["id"]
    assert c.req("patch", f"/brand-profiles/{bid}", json={"overrides": {"colors": ["red"]}}).status_code == 422
    assert c.req("patch", f"/brand-profiles/{bid}", json={"overrides": {"evil": 1}}).status_code == 422
    # re-analysis refreshes detection but never clobbers overrides
    cid = campaign(c)
    c.sync("post", f"/campaigns/{cid}/analyze")
    auto = next(b for b in c.req("get", "/brand-profiles").json() if b["name"] == "Aero")
    c.req("patch", f"/brand-profiles/{auto['id']}", json={"overrides": {"colors": ["#ffffff"]}})
    c.sync("post", f"/campaigns/{cid}/analyze")
    after = next(b for b in c.req("get", "/brand-profiles").json() if b["id"] == auto["id"])
    assert after["overrides"]["colors"] == ["#ffffff"] and after["effective"]["colors"] == ["#ffffff"]


def test_rls_covers_new_tables(make_client):
    import uuid
    from sqlalchemy import text
    from app.core.db import SessionLocal, set_org
    a, b = make_client(), make_client()
    a.register("a@a.example.com", "A")
    b.register("b@b.example.com", "B")
    cid = campaign(a)
    a.sync("post", f"/campaigns/{cid}/analyze")
    with SessionLocal() as db:
        for t in ("products", "product_assets", "brand_profiles"):
            set_org(db, uuid.UUID(b.org))
            assert db.execute(text(f"select count(*) from {t}")).scalar() == 0, t
            set_org(db, uuid.UUID(a.org))
            assert db.execute(text(f"select count(*) from {t}")).scalar() > 0, t
