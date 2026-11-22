from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Approach(str, Enum):
    problem_solution = "problem_solution"
    benefit_focused = "benefit_focused"
    premium = "premium"
    lifestyle = "lifestyle"
    promotional = "promotional"
    feature_highlight = "feature_highlight"
    emotional_storytelling = "emotional_storytelling"
    seasonal = "seasonal"
    minimalist = "minimalist"
    product_innovation = "product_innovation"


TONES = ["premium", "luxury", "professional", "friendly", "bold", "playful", "minimalist", "emotional", "corporate", "conversion-focused"]
HEX = r"^#[0-9a-fA-F]{6}$"


class ClaimRef(BaseModel):
    text: str = Field(max_length=300)
    source_field: str = Field(max_length=100)  # e.g. "features", "description", "user_description"


class Typography(BaseModel):
    headline: str = Field(max_length=120)  # e.g. "bold geometric sans, large"
    body: str = Field(max_length=120)
    cta: str = Field(max_length=120)


class Concept(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    approach: Approach
    advertising_angle: str = Field(max_length=400)
    target_audience: str = Field(max_length=300)
    marketing_objective: str = Field(max_length=300)
    headline: str = Field(min_length=1, max_length=80)
    alternative_headlines: list[str] = Field(default_factory=list, max_length=5)
    supporting_copy: str = Field(max_length=220)
    cta: str = Field(min_length=1, max_length=30)
    emotional_appeal: str = Field(max_length=200)
    visual_direction: str = Field(max_length=500)
    background_description: str = Field(max_length=500)
    product_positioning: str = Field(max_length=300)
    color_palette: list[str] = Field(min_length=2, max_length=6)
    typography: Typography
    claims_used: list[ClaimRef] = Field(default_factory=list, max_length=10)
    assumptions: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("color_palette")
    @classmethod
    def _hex(cls, v):
        import re
        if not all(re.match(HEX, c) for c in v):
            raise ValueError("color_palette must be #RRGGBB values")
        return v


class ConceptSet(BaseModel):
    concepts: list[Concept] = Field(min_length=1, max_length=12)


class Brief(BaseModel):
    objective: str = Field(max_length=300)
    key_message: str = Field(max_length=300)
    background_prompt: str = Field(max_length=800)  # scene only; code appends the no-text/no-product constraints
    composition_notes: str = Field(max_length=500)
    mood: str = Field(max_length=200)
    color_palette: list[str] = Field(min_length=2, max_length=6)
    layout_preferences: list[str] = Field(default_factory=list, max_length=6)
    must_avoid: list[str] = Field(default_factory=list, max_length=10)


class GenerateConceptsIn(BaseModel):
    count: int = Field(default=5, ge=5, le=10)
    approaches: list[Approach] | None = None
