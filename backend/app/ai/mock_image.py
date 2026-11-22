"""Offline procedural background generator (real PNG output, deterministic per prompt+seed). Not an AI model:
soft gradient + blurred shapes tinted by the supplied palette. Contains no text."""
import hashlib
import io
import random

from PIL import Image, ImageDraw, ImageFilter

from app.ai.image import ImageGenerationProvider, ImageResult
from app.rendering.color import hex_to_rgb


class MockImageProvider(ImageGenerationProvider):
    name, model = "mock", "procedural-1"

    def _make(self, prompt, width, height, seed, palette, kind) -> ImageResult:
        w, h = min(width, 1536), min(height, 1536)
        rnd = random.Random(int(hashlib.sha256(f"{prompt}|{seed}|{kind}".encode()).hexdigest()[:12], 16))
        cols = [hex_to_rgb(c) for c in (palette or [])] or [(40, 60, 110), (200, 210, 230)]
        a, b = cols[0], cols[1 % len(cols)]
        small = Image.new("RGB", (64, 64))
        px = small.load()
        for y in range(64):
            for x in range(64):
                t = (x * 0.35 + y * 0.65) / 63
                px[x, y] = tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))
        img = small.resize((w, h), Image.Resampling.BICUBIC)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for _ in range(7):
            c = rnd.choice(cols)
            r = rnd.randint(min(w, h) // 8, min(w, h) // 3)
            x, y = rnd.randint(0, w), rnd.randint(0, h)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(*c, rnd.randint(40, 90)))
        layer = layer.filter(ImageFilter.GaussianBlur(min(w, h) // 14))
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return ImageResult(buf.getvalue(), "image/png", self.name, self.model, w, h, {"seed": seed, "kind": kind}, 5, 0.0, False)

    def generate_background(self, prompt, width, height, *, seed=None, palette=None):
        return self._make(prompt, width, height, seed, palette, "background")

    def generate_lifestyle_scene(self, prompt, width, height, *, seed=None, palette=None):
        return self._make(prompt, width, height, seed, palette, "lifestyle")

    def generate_visual_asset(self, prompt, width, height, *, seed=None, palette=None):
        return self._make(prompt, width, height, seed, palette, "asset")

    def generate_variations(self, image, prompt, n=1):
        return [self._make(prompt, 1024, 1024, i, None, "variation") for i in range(n)]

    def get_generation_status(self, job_id):
        return {"job_id": job_id, "status": "completed"}
