"""Product cut-out. Product pixels are NEVER altered — only the alpha channel is derived.
Strategy (deterministic, no model download): existing alpha > border flood-fill on a uniform backdrop > no cut-out.
When the backdrop is not uniform we return method='none' and the original image is used as a rectangle (reported, not hidden).
A learned segmenter (e.g. rembg/SAM) can be added behind `CutoutProvider` for complex backdrops."""
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter


@dataclass
class CutoutResult:
    png: bytes | None        # RGBA PNG with derived alpha, or None when no cut-out was possible
    method: str              # existing_alpha | border_flood | none
    foreground_fraction: float
    note: str = ""
    crop_box: tuple[int, int, int, int] | None = None  # (l, t, r, b) in ORIGINAL pixel coords; output PNG is cropped to it


class CutoutProvider(ABC):
    @abstractmethod
    def cutout(self, data: bytes) -> CutoutResult: ...


def _shift_or(reach: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    up = np.zeros_like(reach); up[:-1] = reach[1:]
    dn = np.zeros_like(reach); dn[1:] = reach[:-1]
    lf = np.zeros_like(reach); lf[:, :-1] = reach[:, 1:]
    rt = np.zeros_like(reach); rt[:, 1:] = reach[:, :-1]
    return (reach | up | dn | lf | rt) & allowed


class BorderFloodCutout(CutoutProvider):
    def __init__(self, tolerance: float = 26.0, work_size: int = 640, border_uniform_ratio: float = 0.9):
        self.tol, self.work, self.ratio = tolerance, work_size, border_uniform_ratio

    def cutout(self, data: bytes) -> CutoutResult:
        im = Image.open(io.BytesIO(data))
        im.load()
        rgba = im.convert("RGBA")
        a = np.asarray(rgba.getchannel("A"))
        if (a < 250).mean() > 0.01:
            png, box = _tight(rgba)
            return CutoutResult(png, "existing_alpha", float((a > 8).mean()), "Image already had transparency", box)

        small = rgba.convert("RGB")
        small.thumbnail((self.work, self.work), Image.Resampling.BILINEAR)
        arr = np.asarray(small).astype(np.int16)
        h, w, _ = arr.shape
        ring = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]])
        bg = np.median(ring, axis=0)
        if (np.linalg.norm(ring - bg, axis=1) <= self.tol).mean() < self.ratio:
            return CutoutResult(None, "none", 1.0, "Backdrop is not uniform; kept original rectangle")
        like = np.linalg.norm(arr - bg, axis=2) <= self.tol
        seed = np.zeros_like(like)
        seed[0, :], seed[-1, :], seed[:, 0], seed[:, -1] = like[0, :], like[-1, :], like[:, 0], like[:, -1]
        reach = seed
        for _ in range(w + h):  # converges when the flood stops growing
            nxt = _shift_or(reach, like)
            if nxt.sum() == reach.sum():
                break
            reach = nxt
        fg = ~reach
        frac = float(fg.mean())
        if frac < 0.03 or frac > 0.92:
            return CutoutResult(None, "none", frac, "Could not separate product from backdrop; kept original rectangle")
        mask = Image.fromarray((fg * 255).astype(np.uint8), "L").resize(rgba.size, Image.Resampling.BILINEAR)
        mask = mask.filter(ImageFilter.GaussianBlur(1.2)).point(lambda v: 0 if v < 64 else (255 if v > 192 else int((v - 64) * 255 / 128)))
        out = rgba.copy()
        out.putalpha(mask)
        png, box = _tight(out)
        return CutoutResult(png, "border_flood", frac, f"Backdrop removed (rgb≈{tuple(int(v) for v in bg)})", box)


def _tight(im: Image.Image, pad: int = 2) -> tuple[bytes, tuple[int, int, int, int]]:
    """Crop transparent margins so placement and the contact shadow follow the product itself (pixels unchanged)."""
    box = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox() or (0, 0, im.width, im.height)
    l, t, r, b = max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad)
    return _png(im.crop((l, t, r, b))), (l, t, r, b)


def _png(im: Image.Image) -> bytes:
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()
