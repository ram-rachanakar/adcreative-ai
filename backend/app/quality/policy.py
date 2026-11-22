"""Quality gates: every threshold is configuration, never a magic number inside a check.
Resolution order: built-in defaults < organisation policy (organizations.quality_policy) < per-request override."""
from pydantic import BaseModel, ConfigDict, Field


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legibility: float = 0.30
    layout: float = 0.25
    conversion: float = 0.20
    brand: float = 0.15
    technical: float = 0.10


class QualityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # status gates
    pass_at: int = Field(default=80, ge=0, le=100)        # overall >= pass_at and no warnings-as-fails => "pass"
    fail_below: int = Field(default=60, ge=0, le=100)     # overall < fail_below => "fail" (a blocking check failing is always "fail")
    # legibility
    min_contrast: float = Field(default=4.5, ge=1, le=21)        # WCAG AA, normal text: below => warning
    block_contrast: float = Field(default=3.0, ge=1, le=21)      # below => blocking failure (also the large-text AA ratio)
    contrast_percentile: float = Field(default=10, ge=0, le=50)  # judge text on its worst N% of backdrop pixels, not the average
    min_font_px_warn: float = 10
    min_font_px_fail: float = 7
    # layout
    edge_margin_pct: float = Field(default=2.0, ge=0, le=20)     # of the shorter canvas side
    max_text_area_pct: float = Field(default=45, ge=5, le=100)
    max_overlap_pct: float = Field(default=5, ge=0, le=100)      # text/text overlap tolerated, of the smaller box
    # conversion / brand
    min_cta_height_px: float = 16
    min_cta_height_pct: float = 6.0                              # of the shorter side; the larger of the two applies
    min_logo_px: float = 24
    min_product_area_pct: float = 5.0
    cta_vs_background_contrast: float = 3.0                      # WCAG 1.4.11 non-text contrast
    brand_color_max_distance: float = 90                         # RGB distance of the CTA fill from the nearest brand colour
    weights: Weights = Weights()
    # behaviour
    auto_correct: bool = True
    max_correction_rounds: int = Field(default=2, ge=0, le=5)
    vision_enabled: bool = False                                 # costs one model call per banner: opt-in
    vision_weight: float = Field(default=0.35, ge=0, le=0.8)     # share of the overall score when a REAL vision assessment exists
    count_mock_vision: bool = False                              # a mock provider never contributes to the score


def resolve_policy(org_overrides: dict | None, request_overrides: dict | None = None) -> QualityPolicy:
    data: dict = {}
    for layer in (org_overrides or {}, request_overrides or {}):
        for k, v in layer.items():
            if v is None:
                continue
            if k == "weights" and isinstance(v, dict):
                data["weights"] = {**data.get("weights", {}), **v}
            else:
                data[k] = v
    return QualityPolicy.model_validate(data)
