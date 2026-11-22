"""WCAG contrast helpers + deterministic text-colour selection."""
import io

from PIL import Image


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(round(max(0, min(255, v)))) for v in c)


def luminance(rgb) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def blend(base, over, alpha: float):
    return tuple(base[i] * (1 - alpha) + over[i] * alpha for i in range(3))


def cover_region_average(img_bytes: bytes, canvas_w: int, canvas_h: int, box: tuple[float, float, float, float]):
    """Average colour of the part of a cover-fitted background image that lies under `box` (canvas coords)."""
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    iw, ih = im.size
    s = max(canvas_w / iw, canvas_h / ih)
    ox, oy = (iw * s - canvas_w) / 2, (ih * s - canvas_h) / 2
    x, y, w, h = box
    l, t = max(0, (x + ox) / s), max(0, (y + oy) / s)
    r, b = min(iw, (x + w + ox) / s), min(ih, (y + h + oy) / s)
    if r - l < 1 or b - t < 1:
        l, t, r, b = 0, 0, iw, ih
    crop = im.crop((int(l), int(t), max(int(l) + 1, int(r)), max(int(t) + 1, int(b)))).resize((1, 1), Image.Resampling.BOX)
    return crop.getpixel((0, 0))


LIGHT, DARK = "#ffffff", "#111111"


def pick_text_color(bg_rgb, min_ratio: float = 4.5):
    """Returns (text_hex, scrim) where scrim is None or (hex, opacity) needed to reach min_ratio."""
    best = max((LIGHT, DARK), key=lambda c: contrast(hex_to_rgb(c), bg_rgb))
    if contrast(hex_to_rgb(best), bg_rgb) >= min_ratio:
        return best, None
    # Not enough natural contrast: add a scrim of the opposite tone behind the text and re-pick.
    for text in (LIGHT, DARK):
        scrim = DARK if text == LIGHT else LIGHT
        for op in (0.35, 0.5, 0.65, 0.8):
            if contrast(hex_to_rgb(text), blend(bg_rgb, hex_to_rgb(scrim), op)) >= min_ratio:
                return text, (scrim, op)
    return LIGHT, (DARK, 0.85)


def button_colors(candidates: list[str], bg_rgb) -> tuple[str, str]:
    """Pick a button fill (from brand/concept palette) distinct from the background, and a label colour with >=4.5 contrast."""
    pool = [c for c in candidates if c] or ["#ff5a1f"]
    fill = max(pool, key=lambda c: contrast(hex_to_rgb(c), bg_rgb))
    label = max((LIGHT, DARK), key=lambda c: contrast(hex_to_rgb(c), hex_to_rgb(fill)))
    return fill, label
