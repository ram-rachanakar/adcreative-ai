import io
import uuid

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.creative import formats as F
from app.creative.templates import ComposeInput, compose, family_for, spec_problems
from app.main import app
from app.orchestration.jobs import BadRequest, resolve_sizes
from app.rendering.svg import ResolvedAsset, render_spec

# the exact sizes the product spec lists, per platform
SPEC = {
    "google_display": ["300x250", "336x280", "728x90", "300x600", "160x600", "120x600", "970x250", "970x90", "320x50", "320x100", "250x250", "200x200"],
    "instagram": ["1080x1350", "1080x1080", "1080x566", "1080x1920"], "facebook": ["1080x1080", "1200x630", "1080x1920", "1200x1200"],
    "linkedin": ["1200x627", "1200x1200", "720x900"], "x": ["1600x900", "1080x1080"], "pinterest": ["1000x1500", "1000x1000"], "youtube": ["300x250", "1280x720"],
}


def test_registry_contains_every_listed_size_and_is_honest_about_verification():
    reg = F.registry()
    for plat, sizes in SPEC.items():
        have = sorted(f"{f.width}x{f.height}" for f in reg.formats if f.platform == plat)
        assert have == sorted(sizes), plat
    assert len({f.id for f in reg.formats}) == len(reg.formats)
    assert all(f.validated_on is None and f.verification == "unverified" for f in reg.formats)   # never claim a check that did not happen
    yt = F.get("youtube_video_thumbnail_1280x720")
    assert "NOT a static ad placement" in yt.eligibility_notes
    assert F.get("google_display_medium_rectangle_300x250").max_file_kb == 150
    assert F.get("instagram_story_1080x1920").safe_zone.top_pct == 14


def test_resolve_sizes_merges_shared_dimensions_and_validates():
    out = resolve_sizes(["instagram_feed_square_1080x1080", "facebook_feed_square_1080x1080", "x_image_square_1080x1080"], [])
    assert len(out) == 1 and len(out[0].format_keys) == 3
    assert [s.format_keys for s in resolve_sizes([], [(1080, 1080)])][0] != []                  # custom size inherits matching placements
    assert resolve_sizes([], [(777, 333)])[0].format_keys == []
    with pytest.raises(BadRequest):
        resolve_sizes(["nope"], [])
    with pytest.raises(BadRequest):
        resolve_sizes([], [(10, 10)])


def mk_input(headline="Keeps drinks cold for hours", supporting="Double-wall insulation keeps your drink at the right temperature all day.", alts=("Stay cold",), logo=True, cta="Learn more"):
    bg = io.BytesIO()
    Image.new("RGB", (512, 512), (30, 50, 90)).save(bg, "PNG")
    return ComposeInput(headline=headline, supporting=supporting, cta=cta, palette=["#ff5a1f"], bg_asset_id=uuid.uuid4(), bg_bytes=bg.getvalue(),
                        product_asset_id=uuid.uuid4(), product_size=(200, 400), alternatives=list(alts),
                        logo_asset_id=uuid.uuid4() if logo else None, logo_size=(200, 80) if logo else None)


@pytest.mark.parametrize("fmt", F.registry().formats, ids=lambda f: f.id)
def test_every_registry_format_gets_a_valid_layout(fmt):
    s = compose(mk_input(), fmt.width, fmt.height)
    assert (s.canvas.width, s.canvas.height) == (fmt.width, fmt.height)
    assert spec_problems(s) == [], s.meta.notes
    assert s.meta.family == family_for(fmt.width, fmt.height)


def test_families_selected_by_geometry():
    exp = {(728, 90): "very_wide", (320, 50): "very_wide", (970, 250): "very_wide", (160, 600): "skyscraper", (120, 600): "skyscraper", (300, 250): "small",
           (200, 200): "small", (1080, 1080): "square", (1200, 628): "landscape", (1080, 1350): "portrait", (1080, 1920): "portrait", (300, 600): "portrait"}
    for (w, h), fam in exp.items():
        assert family_for(w, h) == fam, (w, h)


def test_render_every_format_exact_dimensions():
    from app.rendering.spec import ImageEl
    pic = io.BytesIO()
    Image.new("RGBA", (200, 400), (0, 120, 200, 255)).save(pic, "PNG")
    inp = mk_input()
    res = lambda i: ResolvedAsset(inp.bg_bytes if i == inp.bg_asset_id else pic.getvalue(), 512 if i == inp.bg_asset_id else 200, 512 if i == inp.bg_asset_id else 400)  # noqa: E731
    for f in F.registry().formats:
        s = compose(inp, f.width, f.height)
        r = render_spec(s, res)
        assert Image.open(io.BytesIO(r.png)).size == (f.width, f.height) and r.report.ok, f.id
        p = next(e for e in r.report.elements if e.id == "product")
        assert abs(p.bbox[2] / p.bbox[3] - 200 / 400) < 1e-6          # product never distorted at any size


def test_story_safe_zone_respected():
    s = compose(ComposeInput(**{**mk_input().__dict__, "safe_top_pct": 14, "safe_bottom_pct": 20}), 1080, 1920)
    for e in s.elements:
        assert e.y >= 1920 * 0.14 - 1 and e.y + e.h <= 1920 * 0.80 + 1, e.id


def test_fallback_chain_records_every_simplification():
    long_head = "An extraordinarily long headline about insulated stainless steel drinkware that never ends"
    s = compose(mk_input(headline=long_head, alts=("Stay cold",)), 320, 100)
    assert "Simplified: shortest approved headline used" in s.meta.notes
    assert next(e for e in s.elements if e.id == "headline").text == "Stay cold"
    assert not any(e.id == "body" for e in s.elements)
    s2 = compose(mk_input(), 970, 250)           # roomy: nothing simplified
    assert any(e.id == "body" for e in s2.elements) and not any("Simplified" in n for n in s2.meta.notes)


def test_unfittable_copy_is_reported_never_hidden():
    s = compose(mk_input(headline="Supercalifragilisticexpialidocious" * 3, alts=(), cta="Shop the entire collection now"), 120, 600)
    assert any("could not be fully fitted" in n for n in s.meta.notes)
    assert spec_problems(s)


def test_no_logo_still_lays_out():
    for f in F.registry().formats:
        assert spec_problems(compose(mk_input(logo=False), f.width, f.height)) == [], f.id


def test_formats_api_and_presets_isolated(make_client):
    a, b = make_client(), make_client()
    a.register("a@a.example.com", "A")
    b.register("b@b.example.com", "B")
    r = TestClient(app).get("/formats").json()
    assert len(r["formats"]) == 29 and r["custom_limits"] == {"min": 50, "max": 4096}
    assert all(f["platform"] == "x" for f in TestClient(app).get("/formats?platform=x").json()["formats"])
    p = a.req("post", "/format-presets", json={"name": "Display pack", "format_ids": ["google_display_leaderboard_728x90"], "custom_sizes": [{"width": 640, "height": 480}]})
    assert p.status_code == 201
    assert len(a.req("get", "/format-presets").json()) == 1 and b.req("get", "/format-presets").json() == []
    assert b.req("delete", f"/format-presets/{p.json()['id']}").status_code == 404
    assert a.req("post", "/format-presets", json={"name": "bad", "format_ids": ["nope"]}).status_code == 422
    assert a.req("post", "/format-presets", json={"name": "empty"}).status_code == 422
    assert a.req("delete", f"/format-presets/{p.json()['id']}").status_code == 204


def _gray_input(v=120):
    bg = io.BytesIO()
    Image.new("RGB", (512, 512), (v, v, v)).save(bg, "PNG")
    i = mk_input()
    i.bg_bytes = bg.getvalue()
    return i


def test_scrim_never_has_a_hard_edge():
    # luminance ~0.188 sits in the narrow band where neither white nor #111 text reaches 4.5:1, so a scrim is required
    land = compose(_gray_input(), 1200, 628)
    assert [s.fade for s in land.background.scrims] == ["right"] and land.background.scrims[0].solid_until < 0.9
    wide = compose(_gray_input(), 970, 250)
    assert [s.fade for s in wide.background.scrims] == ["none"] and wide.background.scrims[0].w is None   # even, full-canvas
    inp = _gray_input()
    r = render_spec(land, lambda i: ResolvedAsset(inp.bg_bytes, 512, 512) if i == inp.bg_asset_id else ResolvedAsset(inp.bg_bytes, 512, 512))
    im = Image.open(io.BytesIO(r.png)).convert("RGB")
    row = [im.getpixel((x, 20))[0] for x in range(0, 1200, 20)]
    steps = [abs(b - a) for a, b in zip(row, row[1:])]
    assert max(steps) <= 8, steps    # brightness changes gradually across the canvas: no visible seam
