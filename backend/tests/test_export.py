import io
import json
import zipfile
from xml.etree import ElementTree

import pytest
from PIL import Image

from app.ai.mock_image import MockImageProvider
from app.export import service as E
from app.orchestration import factories
from test_creative_api import env, gen, prepared  # noqa: F401


def png_bytes(w=600, h=400, noisy=False):
    im = Image.effect_noise((w, h), 90).convert("RGB") if noisy else Image.new("RGB", (w, h), (20, 60, 120))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def test_encoders_keep_exact_dimensions_and_validate():
    src = png_bytes(300, 250)
    for fmt in ("png", "jpeg", "webp"):
        enc = E.encode(src, fmt, 90)
        assert E.validate_file(enc.data, fmt, 300, 250) == []
        assert Image.open(io.BytesIO(enc.data)).size == (300, 250)
    assert E.validate_file(E.encode(src, "jpeg", 90).data, "png", 300, 250)          # wrong container is caught
    assert E.validate_file(E.encode(src, "png", 90).data, "png", 301, 250)           # wrong size is caught
    assert E.validate_file(b"garbage", "png", 1, 1)


def test_size_limit_lowers_quality_not_dimensions():
    noisy = png_bytes(728, 90, noisy=True)
    plain = E.encode(noisy, "jpeg", 95)
    capped = E.encode(noisy, "jpeg", 95, max_kb=20)
    assert len(capped.data) < len(plain.data) and capped.quality < 95 and any("Quality reduced" in w for w in capped.warnings)
    assert Image.open(io.BytesIO(capped.data)).size == (728, 90)
    png_over = E.encode(png_bytes(728, 90, noisy=True), "png", 90, max_kb=5)
    assert any("above the 5 KB limit" in w for w in png_over.warnings)               # PNG: warned, never silently converted
    impossible = E.encode(noisy, "jpeg", 95, max_kb=1)
    assert any("still above" in w for w in impossible.warnings)                      # reported, not hidden


def test_svg_export_is_valid_and_has_exact_size():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="250"></svg>'
    assert E.validate_file(svg, "svg", 300, 250) == []
    assert E.validate_file(svg, "svg", 301, 250)
    assert E.validate_file(b"<html/>", "svg", 1, 1)


def export(c, cid, **body):
    r = c.req("post", f"/campaigns/{cid}/exports", json=body)
    assert r.status_code == 202, r.text
    return c.req("get", f"/exports/{r.json()['id']}").json()


def open_zip(c, e):
    assert e["status"] == "completed", e
    r = c.http.get(e["url"])
    assert r.status_code == 200
    return zipfile.ZipFile(io.BytesIO(r.content))


FORMATS = ["google_display_medium_rectangle_300x250", "google_display_leaderboard_728x90", "instagram_feed_square_1080x1080",
           "facebook_feed_square_1080x1080", "x_image_square_1080x1080", "youtube_video_thumbnail_1280x720"]


def test_zip_every_file_decodes_to_manifest_dimensions(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=2)
    factories.image_provider = lambda: MockImageProvider()
    gen(c, cid, format_ids=FORMATS, sizes=[{"width": 777, "height": 333}])
    for fmt in ("png", "jpeg", "webp", "svg"):
        e = export(c, cid, format=fmt)
        z = open_zip(c, e)
        m = json.loads(z.read("manifest.json"))
        # 2 concepts x (300x250, 728x90, 1080x1080 serving 3 placements, 1280x720, custom 777x333) = 2 x 7 placements
        assert m["item_count"] == 14 and len(m["items"]) == 14 and e["file_count"] == 14 and m["export_format"] == fmt
        for it in m["items"]:
            data = z.read(it["file"])
            assert len(data) == it["file_size_bytes"]
            assert E.validate_file(data, fmt, it["width"], it["height"]) == [], it["file"]
            for k in ("campaign_id", "creative_id", "concept", "platform", "placement", "width", "height", "format", "file_size_bytes", "quality_report", "created_at"):
                assert it[k] is not None or k == "created_at", k
            assert ".." not in it["file"] and not it["file"].startswith("/")
        folders = {it["file"].split("/")[0] for it in m["items"]}
        assert folders == {"google_display", "instagram", "facebook", "x", "youtube", "custom"}
        assert sum(1 for n in z.namelist() if n.startswith("thumbnails/")) == len({it["creative_id"] for it in m["items"]})
        assert z.testzip() is None


def test_platform_limits_and_type_warnings_in_manifest(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    gen(c, cid, format_ids=["google_display_medium_rectangle_300x250", "youtube_video_thumbnail_1280x720"])
    z = open_zip(c, export(c, cid, format="webp", quality=95))
    items = {it["platform"]: it for it in json.loads(z.read("manifest.json"))["items"]}
    g = items["google_display"]
    assert g["max_file_kb"] == 150 and g["within_size_limit"] is True and g["format_verification"] == "unverified"
    assert any("not an accepted file type for google_display" in w for w in g["warnings"])
    assert items["youtube"]["max_file_kb"] is None
    assert items["google_display"]["quality_report"]["scoring"] == "assessed" and isinstance(items["google_display"]["quality_report"]["score"], int)   # real, measured scores


def test_subset_selection_and_foreign_ids(make_client, env):
    a, b = make_client(), make_client()
    cida, cidb = prepared(a, env, n_select=1), prepared(b, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    ca = gen(a, cida, sizes=[{"width": 1080, "height": 1080}, {"width": 1200, "height": 628}]).json()
    cb = gen(b, cidb, sizes=[{"width": 1080, "height": 1080}]).json()
    sq = next(x for x in ca if x["width"] == 1080)
    wide = next(x for x in ca if x["width"] == 1200)
    assert export(a, cida, creative_ids=[wide["id"]])["file_count"] == 1           # 1200x628 matches no registry placement
    assert export(a, cida, creative_ids=[sq["id"]])["file_count"] == 3             # 1080x1080 serves instagram + facebook + x
    assert a.req("post", f"/campaigns/{cida}/exports", json={"creative_ids": [cb[0]["id"]]}).status_code == 404     # other org's creative
    assert b.req("post", f"/campaigns/{cida}/exports", json={}).status_code == 404
    ex = a.req("post", f"/campaigns/{cida}/exports", json={}).json()
    assert b.req("get", f"/exports/{ex['id']}").status_code == 404


def test_nothing_to_export_fails_cleanly(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    e = export(c, cid)
    assert e["status"] == "failed" and "Nothing to export" in e["error"] and e["url"] is None


def test_hostile_concept_name_cannot_escape_zip_paths(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    sel = next(x for x in c.req("get", f"/campaigns/{cid}/concepts").json() if x["selected"])
    c.req("patch", f"/concepts/{sel['id']}", json={"name": "../../etc/passwd\\..\\x"})
    c.sync("post", f"/campaigns/{cid}/briefs/generate")
    factories.image_provider = lambda: MockImageProvider()
    gen(c, cid, sizes=[{"width": 1080, "height": 1080}])
    z = open_zip(c, export(c, cid))
    assert all(".." not in n and not n.startswith("/") and "\\" not in n for n in z.namelist())


def test_single_download(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    cr = gen(c, cid, format_ids=["google_display_medium_rectangle_300x250"]).json()[0]
    for fmt, mime in (("png", "image/png"), ("jpeg", "image/jpeg"), ("webp", "image/webp"), ("svg", "image/svg+xml")):
        r = c.req("get", f"/creatives/{cr['id']}/download?format={fmt}")
        assert r.status_code == 200 and r.headers["content-type"] == mime and "attachment" in r.headers["content-disposition"]
        assert E.validate_file(r.content, fmt, 300, 250) == []
    assert c.req("get", f"/creatives/{cr['id']}/download?format=bmp").status_code == 422
    other = make_client()
    other.register("o@o.example.com", "O")
    assert other.req("get", f"/creatives/{cr['id']}/download").status_code == 404


def test_svg_export_has_no_external_references(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    cr = gen(c, cid, format_ids=["google_display_medium_rectangle_300x250"]).json()[0]
    svg = c.req("get", f"/creatives/{cr['id']}/download?format=svg").content.decode()
    ElementTree.fromstring(svg)
    assert "http://" not in svg.replace("http://www.w3.org", "") and "<script" not in svg


def test_transparent_export_has_alpha_and_exact_size(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    gen(c, cid, format_ids=["google_display_medium_rectangle_300x250"])
    opaque = open_zip(c, export(c, cid, format="png"))
    for fmt in ("png", "webp"):
        z = open_zip(c, export(c, cid, format=fmt, transparent=True))
        m = json.loads(z.read("manifest.json"))
        assert m["transparent"] is True and all(it["transparent"] for it in m["items"])
        it = m["items"][0]
        im = Image.open(io.BytesIO(z.read(it["file"])))
        assert im.size == (it["width"], it["height"]) and "A" in im.getbands()
        assert im.convert("RGBA").getchannel("A").getextrema()[0] == 0          # the background is really gone
        assert any("background was removed" in w for w in it["warnings"])
    # the default export is untouched: still fully opaque
    m0 = json.loads(opaque.read("manifest.json"))
    assert m0["transparent"] is False
    flat = Image.open(io.BytesIO(opaque.read(m0["items"][0]["file"]))).convert("RGBA")
    assert flat.getchannel("A").getextrema()[0] == 255


def test_transparent_jpeg_rejected(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    r = c.req("post", f"/campaigns/{cid}/exports", json={"format": "jpeg", "transparent": True})
    assert r.status_code == 422
