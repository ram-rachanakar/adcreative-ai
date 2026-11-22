"""Provider-independent image generation. Providers produce backgrounds/scenes/decor ONLY — never final typography or the product."""
import base64
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings


class ImageProviderError(Exception):
    pass


class ImageNotConfigured(ImageProviderError):
    pass


@dataclass
class ImageResult:
    data: bytes
    mime: str
    provider: str
    model: str
    width: int
    height: int
    params: dict = field(default_factory=dict)
    duration_ms: int = 0
    cost_usd: float = 0.0
    priced: bool = False


class ImageGenerationProvider(ABC):
    name = "base"

    @abstractmethod
    def generate_background(self, prompt: str, width: int, height: int, *, seed: int | None = None, palette: list[str] | None = None) -> ImageResult: ...
    @abstractmethod
    def generate_lifestyle_scene(self, prompt: str, width: int, height: int, *, seed: int | None = None, palette: list[str] | None = None) -> ImageResult: ...
    @abstractmethod
    def generate_visual_asset(self, prompt: str, width: int, height: int, *, seed: int | None = None, palette: list[str] | None = None) -> ImageResult: ...
    @abstractmethod
    def generate_variations(self, image: bytes, prompt: str, n: int = 1) -> list[ImageResult]: ...
    @abstractmethod
    def get_generation_status(self, job_id: str) -> dict: ...


OPENAI_SIZES = {"1024x1024": 1.0, "1536x1024": 1.5, "1024x1536": 2 / 3}


class OpenAIImageProvider(ImageGenerationProvider):
    """OpenAI Images API. Model from config. NOTE: tested only against a mock transport — no live call has been made.
    The provider returns one of its supported sizes; the renderer cover-crops it to each banner size."""
    name = "openai"
    URL = "https://api.openai.com/v1/images/generations"

    def __init__(self, api_key: str, model: str, cost_per_image: float = 0.0, transport: httpx.BaseTransport | None = None):
        if not api_key or not model:
            raise ImageNotConfigured("OPENAI_API_KEY and IMAGE_MODEL must be set to use the openai image provider")
        self.api_key, self.model, self.cost, self.transport = api_key, model, cost_per_image, transport

    @staticmethod
    def pick_size(w: int, h: int) -> str:
        r = w / h
        return min(OPENAI_SIZES, key=lambda s: abs(OPENAI_SIZES[s] - r))

    def _gen(self, prompt: str, w: int, h: int, kind: str) -> ImageResult:
        size = self.pick_size(w, h)
        t0 = time.time()
        try:
            with httpx.Client(transport=self.transport, timeout=180) as c:
                r = c.post(self.URL, json={"model": self.model, "prompt": prompt, "size": size, "n": 1},
                           headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise ImageProviderError(f"Network error calling image provider: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise ImageProviderError(f"Image provider error {r.status_code}")  # body not echoed
        item = (r.json().get("data") or [{}])[0]
        if "b64_json" in item:
            raw = base64.b64decode(item["b64_json"])
        elif "url" in item:
            from app.scraping.fetcher import SafeFetcher
            raw = SafeFetcher().fetch(item["url"], allowed_mime=("image/png", "image/jpeg", "image/webp"), max_bytes=20_000_000).body
        else:
            raise ImageProviderError("Image provider returned no image")
        sw, sh = (int(x) for x in size.split("x"))
        return ImageResult(raw, "image/png", self.name, self.model, sw, sh, {"size": size, "kind": kind}, int((time.time() - t0) * 1000),
                           self.cost, bool(self.cost))

    def generate_background(self, prompt, width, height, *, seed=None, palette=None):
        return self._gen(prompt, width, height, "background")

    def generate_lifestyle_scene(self, prompt, width, height, *, seed=None, palette=None):
        return self._gen(prompt, width, height, "lifestyle")

    def generate_visual_asset(self, prompt, width, height, *, seed=None, palette=None):
        return self._gen(prompt, width, height, "asset")

    def generate_variations(self, image, prompt, n=1):
        raise ImageProviderError("Variations are not implemented for this provider yet")

    def get_generation_status(self, job_id):
        return {"job_id": job_id, "status": "completed"}  # synchronous API: results are returned inline


def get_image_provider() -> ImageGenerationProvider:
    s = get_settings()
    if s.image_provider == "openai":
        return OpenAIImageProvider(s.openai_api_key, s.image_model, s.image_cost_per_image)
    from app.ai.mock_image import MockImageProvider
    return MockImageProvider()
