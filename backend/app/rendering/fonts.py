"""Bundled fonts (metric-compatible, redistributable). Rendering never depends on system fonts, so output is deterministic."""
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).parent / "fonts"
REGISTRY: dict[tuple[str, int], str] = {
    ("Liberation Sans", 400): "LiberationSans-Regular.ttf", ("Liberation Sans", 700): "LiberationSans-Bold.ttf",
    ("Liberation Serif", 400): "LiberationSerif-Regular.ttf", ("Liberation Serif", 700): "LiberationSerif-Bold.ttf",
    ("DejaVu Sans", 400): "DejaVuSans.ttf", ("DejaVu Sans", 700): "DejaVuSans-Bold.ttf",
}


class MissingFont(Exception):
    pass


def font_path(family: str, weight: int) -> Path:
    f = REGISTRY.get((family, weight))
    if not f or not (FONT_DIR / f).exists():
        raise MissingFont(f"Font not available: {family} {weight}")
    return FONT_DIR / f


def all_font_files() -> list[str]:
    return sorted(str(p) for p in FONT_DIR.glob("*.ttf"))


@lru_cache(maxsize=256)
def load(family: str, weight: int, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path(family, weight)), size=size)


def resolve_family(requested: str | None) -> tuple[str, str | None]:
    """Map a brand font name to an available family. Returns (family, warning-if-substituted)."""
    names = {k[0] for k in REGISTRY}
    if requested in names:
        return requested, None
    if not requested:
        return "Liberation Sans", None
    low = requested.lower()
    fallback = "Liberation Serif" if "serif" in low and "sans" not in low else "Liberation Sans"
    return fallback, f"Font '{requested}' is not bundled; using {fallback}"
