"""Deterministic brand analysis from images: dominant palette via Pillow quantization."""
import io

from PIL import Image


def dominant_colors(data: bytes, n: int = 5) -> list[str]:
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    img.thumbnail((128, 128))
    px = [p[:3] for p in img.getdata() if p[3] > 200]
    px = [p for p in px if not (min(p) > 235 or max(p) < 20)] or px  # drop near white/black backdrops
    if not px:
        return []
    flat = Image.new("RGB", (len(px), 1))
    flat.putdata(px)
    q = flat.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    return ["#%02x%02x%02x" % tuple(pal[i * 3:i * 3 + 3]) for _, i in counts][:n]
