"""Banner format registry (configuration-backed, versioned). Loaded from shared/banner-formats/formats.json."""
import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

REGISTRY_PATHS = [Path(__file__).resolve().parents[3] / "shared" / "banner-formats" / "formats.json",
                  Path("/app/shared/banner-formats/formats.json")]
MIN_DIM, MAX_DIM = 50, 4096
PLATFORMS = ["google_display", "instagram", "facebook", "linkedin", "x", "pinterest", "youtube"]


class SafeZone(BaseModel):
    top_pct: float = 0
    bottom_pct: float = 0


class FormatDef(BaseModel):
    id: str
    platform: str
    placement: str
    name: str
    width: int
    height: int
    file_types: list[str]
    max_file_kb: int | None = None
    safe_zone: SafeZone | None = None
    eligibility_notes: str = ""
    verification: str = "unverified"
    validated_on: str | None = None   # ISO date a human checked the spec against platform docs; null = never
    format_version: int = 1


class Registry(BaseModel):
    registry_version: int
    note: str = ""
    formats: list[FormatDef] = Field(default_factory=list)


@lru_cache
def registry() -> Registry:
    for p in REGISTRY_PATHS:
        if p.exists():
            return Registry.model_validate(json.loads(p.read_text()))
    raise FileNotFoundError("banner format registry not found")


def get(fid: str) -> FormatDef | None:
    return next((f for f in registry().formats if f.id == fid), None)


def for_size(w: int, h: int) -> list[FormatDef]:
    return [f for f in registry().formats if (f.width, f.height) == (w, h)]


def valid_custom(w: int, h: int) -> bool:
    return MIN_DIM <= w <= MAX_DIM and MIN_DIM <= h <= MAX_DIM
