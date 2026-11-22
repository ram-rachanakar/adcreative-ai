import io
import uuid

import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from app.ai.image import ImageNotConfigured, ImageProviderError, OpenAIImageProvider
from app.ai.mock_image import MockImageProvider
from app.creative.cutout import BorderFloodCutout
from app.rendering import color, fonts
from app.rendering.spec import Background, ButtonEl, Canvas, CreativeSpec, ImageEl, TextEl
from app.rendering.svg import ResolvedAsset, build_svg, render_spec
from app.rendering.text import fit_text


def png(size=(400, 500), bg=(250, 250, 250), body=(20, 80, 200)):
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((120, 60, 280, 440), 30, fill=body)
    d.rectangle((150, 200, 250, 300), fill=(252, 252, 252))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def spec(*els, w=600, h=300, bg=None):
    return CreativeSpec(canvas=Canvas(width=w, height=h), background=bg or Background(type="solid", color="#203060"), elements=list(els))


# ---- spec validation
def test_spec_rejects_bad_input():
    with pytest.raises(ValidationError):
        TextEl(id="a", x=0, y=0, w=10, h=10, text="x", font_size=12, color="red")            # bad colour
    with pytest.raises(ValidationError):
        TextEl(id="a b", x=0, y=0, w=10, h=10, text="x", font_size=12)                      # bad id
    with pytest.raises(ValidationError):
        TextEl(id="a", x=0, y=0, w=10, h=10, text="x", font_size=12, font_family="Comic Sans")
    with pytest.raises(ValidationError):
        Background(type="image")                                                              # needs asset
    with pytest.raises(ValidationError):
        CreativeSpec.model_validate({"canvas": {"width": 100, "height": 100}, "background": {}, "elements": [{"type": "html", "id": "x", "x": 0, "y": 0, "w": 1, "h": 1}]})
    with pytest.raises(ValidationError):
        CreativeSpec.model_validate({"canvas": {"width": 100, "height": 100}, "background": {}, "script": "alert(1)"})  # extra keys forbidden
    t = dict(type="text", x=0, y=0, w=10, h=10, text="x", font_size=12)
    with pytest.raises(ValidationError):
        CreativeSpec.model_validate({"canvas": {"width": 100, "height": 100}, "background": {}, "elements": [{**t, "id": "a"}, {**t, "id": "a"}]})


def test_markup_in_text_is_escaped_not_interpreted():
    s = spec(TextEl(id="h", x=10, y=10, w=500, h=100, text='<script>alert(1)</script> & "q" </text><rect/>', font_size=30, color="#ffffff"))
    svg, _ = build_svg(s, lambda i: None)
    assert "<script>" not in svg and "&lt;script&gt;" in svg and "<rect/>" not in svg.split("</defs>")[1].split("<text")[1]


def test_no_external_references_in_svg():
    aid = uuid.uuid4()
    s = spec(ImageEl(id="p", x=0, y=0, w=100, h=100, asset_id=aid), bg=Background(type="solid"))
    svg, _ = build_svg(s, lambda i: ResolvedAsset(png(), 400, 500))
    assert "http://" not in svg.replace("http://www.w3.org", "") and "file:" not in svg


# ---- text fitting
def test_text_shrinks_then_reports_overflow():
    lay = fit_text("Keeps your drinks ice cold for twenty four hours", "Liberation Sans", 700, 60, 20, 2, 1.1, 300, 150)
    assert not lay.overflow and lay.shrunk and lay.font_size < 60 and len(lay.lines) <= 2
    bad = fit_text("Keeps your drinks ice cold for twenty four hours every single day without fail", "Liberation Sans", 700, 40, 30, 1, 1.1, 120, 40)
    assert bad.overflow  # never silently truncated
    word = fit_text("Supercalifragilisticexpialidocious", "Liberation Sans", 700, 40, 30, 3, 1.1, 100, 200)
    assert word.overflow


def test_missing_font_is_error():
    with pytest.raises(fonts.MissingFont):
        fonts.font_path("Nope", 400)
    fam, warn = fonts.resolve_family("Helvetica Neue")
    assert fam == "Liberation Sans" and warn


# ---- contrast
@pytest.mark.parametrize("bg", [(10, 10, 30), (245, 245, 245), (128, 128, 128), (120, 100, 140), (200, 60, 60), (255, 255, 0), (0, 128, 0)])
def test_text_colour_always_reaches_aa_contrast(bg):
    txt, scrim = color.pick_text_color(bg)
    eff = color.blend(bg, color.hex_to_rgb(scrim[0]), scrim[1]) if scrim else bg
    assert color.contrast(color.hex_to_rgb(txt), eff) >= 4.5


def test_button_label_contrast():
    fill, label = color.button_colors(["#ff5a1f", "#112233"], (20, 20, 40))
    assert color.contrast(color.hex_to_rgb(label), color.hex_to_rgb(fill)) >= 4.5


# ---- rendering
def test_render_exact_dimensions_and_pixels():
    s = spec(ButtonEl(id="c", role="cta", x=20, y=200, w=200, h=60, label="Buy now", fill="#ff5a1f", font_size=24))
    r = render_spec(s, lambda i: None)
    im = Image.open(io.BytesIO(r.png)).convert("RGB")
    assert im.size == (600, 300) and r.report.ok
    assert im.getpixel((300, 150)) == (0x20, 0x30, 0x60)            # background
    assert im.getpixel((30, 230)) == (0xff, 0x5a, 0x1f)             # button fill
    assert any(im.getpixel((x, 230)) != (0xff, 0x5a, 0x1f) for x in range(30, 210))  # label drawn


def test_image_never_distorted_and_reports_oob():
    aid = uuid.uuid4()
    s = spec(ImageEl(id="p", x=0, y=0, w=300, h=100, asset_id=aid), ImageEl(id="q", x=500, y=250, w=200, h=200, asset_id=aid))
    r = render_spec(s, lambda i: ResolvedAsset(png(), 400, 500))
    x, y, w, h = r.report.elements[0].bbox
    assert abs(w / h - 400 / 500) < 1e-6 and w <= 300 and h <= 100
    assert r.report.elements[1].out_of_bounds and not r.report.ok


def test_missing_asset_reported():
    r = render_spec(spec(ImageEl(id="p", x=0, y=0, w=50, h=50, asset_id=uuid.uuid4())), lambda i: None)
    assert r.report.missing_assets and not r.report.ok


# ---- cutout
def test_cutout_removes_backdrop_keeps_pixels_and_enclosed_label():
    src = png()
    r = BorderFloodCutout().cutout(src)
    assert r.method == "border_flood"
    out = Image.open(io.BytesIO(r.png))
    l, t, rr, b = r.crop_box
    assert out.size == (rr - l, b - t) and out.width < 400 and out.height < 500  # tight crop: transparent margins removed
    a = np.asarray(out.getchannel("A"))
    assert a[0, 0] == 0                                                   # rounded corner outside the product is transparent
    assert a[150 - t, 200 - l] == 255 and a[250 - t, 200 - l] == 255     # body + enclosed white label kept
    o, c = np.asarray(Image.open(io.BytesIO(src)).convert("RGB"))[t:b, l:rr], np.asarray(out.convert("RGB"))
    solid = a == 255
    assert (o[solid] == c[solid]).all()  # product pixels untouched


def test_cutout_declines_on_busy_backdrop_and_respects_existing_alpha():
    noisy = Image.effect_noise((300, 300), 80).convert("RGB")
    b = io.BytesIO(); noisy.save(b, "PNG")
    assert BorderFloodCutout().cutout(b.getvalue()).method == "none"
    rgba = Image.new("RGBA", (100, 100), (0, 0, 0, 0)); ImageDraw.Draw(rgba).ellipse((20, 20, 80, 80), fill=(200, 0, 0, 255))
    b = io.BytesIO(); rgba.save(b, "PNG")
    assert BorderFloodCutout().cutout(b.getvalue()).method == "existing_alpha"


# ---- providers
def test_mock_image_provider_deterministic_and_real_image():
    p = MockImageProvider()
    a, b, c = (p.generate_background(t, 512, 512, seed=1, palette=["#112233", "#ddeeff"]) for t in ("x", "x", "y"))
    assert a.data == b.data and a.data != c.data
    assert Image.open(io.BytesIO(a.data)).size == (512, 512)


def test_openai_adapter_request_and_errors():
    import base64
    seen = {}

    def h(r):
        import json
        seen["body"], seen["auth"] = json.loads(r.content), r.headers["authorization"]
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})
    p = OpenAIImageProvider("sk-x", "cfg-model", 0.04, httpx.MockTransport(h))
    r = p.generate_background("a calm scene", 1200, 628)
    assert seen["body"]["model"] == "cfg-model" and seen["body"]["size"] == "1536x1024" and seen["auth"] == "Bearer sk-x"
    assert r.data == png() and r.cost_usd == 0.04 and r.priced
    assert OpenAIImageProvider.pick_size(1080, 1350) == "1024x1536"
    with pytest.raises(ImageNotConfigured):
        OpenAIImageProvider("", "")
    bad = OpenAIImageProvider("sk-x", "m", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="sk-x leaked")))
    with pytest.raises(ImageProviderError) as e:
        bad.generate_background("p", 100, 100)
    assert "sk-x" not in str(e.value)
