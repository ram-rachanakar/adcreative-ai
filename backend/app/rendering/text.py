"""Text measurement + wrapping + font fitting using the same font files the rasteriser uses."""
from dataclasses import dataclass

from app.rendering import fonts


@dataclass
class TextLayout:
    lines: list[str]
    font_size: float
    line_px: float
    ascent: float
    descent: float
    overflow: bool          # could not fit even at min size
    shrunk: bool
    width: float            # widest line
    height: float


def _width(text: str, family: str, weight: int, size: float, spacing: float) -> float:
    f = fonts.load(family, weight, size)
    return f.getlength(text) + spacing * max(0, len(text) - 1)


def wrap(text: str, family: str, weight: int, size: float, spacing: float, max_w: float) -> tuple[list[str], bool]:
    """Greedy word wrap. Returns (lines, has_unbreakable_word_wider_than_box)."""
    lines, cur, too_wide = [], "", False
    for word in text.split():
        trial = word if not cur else f"{cur} {word}"
        if _width(trial, family, weight, size, spacing) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = word
        if _width(word, family, weight, size, spacing) > max_w:
            too_wide = True
    if cur:
        lines.append(cur)
    return lines, too_wide


def fit_text(text: str, family: str, weight: int, size: float, min_size: float, max_lines: int, line_height: float,
             box_w: float, box_h: float, spacing: float = 0, uppercase: bool = False) -> TextLayout:
    """Shrink the font from `size` down to `min_size` until the text fits the box; otherwise report overflow."""
    text = " ".join(text.split())
    if uppercase:
        text = text.upper()
    s = float(size)
    step = max(0.5, size * 0.03)
    while True:
        lines, too_wide = wrap(text, family, weight, s, spacing, box_w)
        f = fonts.load(family, weight, s)
        asc, desc = f.getmetrics()
        line_px = s * line_height
        height = line_px * len(lines)
        fits = len(lines) <= max_lines and height <= box_h + 0.01 and not too_wide
        if fits or s - step < min_size - 1e-9:
            if not fits and s > min_size:  # try exactly min_size once
                s = float(min_size)
                continue
            width = max((_width(l, family, weight, s, spacing) for l in lines), default=0.0)
            return TextLayout(lines, s, line_px, asc, desc, not fits, s < size - 1e-9, width, height)
        s -= step
