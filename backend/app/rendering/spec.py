"""Creative specification: the editable, schema-validated JSON that fully describes a banner.
Only these typed elements exist — there is no way to inject HTML/JS/arbitrary SVG through a spec."""
import uuid
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
ElId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,40}$")]
FontFamily = Literal["Liberation Sans", "Liberation Serif", "DejaVu Sans"]
Role = Literal["headline", "body", "cta", "logo", "product", "decor", "badge"]
# Semantic template slots (M7 templates). Independent of styling and z-order; `ElBase.slot` is optional so older documents load unchanged.
Slot = Literal["headline_primary", "headline_secondary", "body_copy", "product_primary", "product_secondary", "brand_logo",
               "primary_cta", "secondary_cta", "promotion_badge", "decorative_shape"]
SPEC_VERSION = 1


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Canvas(Strict):
    width: int = Field(ge=16, le=8192)
    height: int = Field(ge=16, le=8192)


class Scrim(Strict):
    color: Hex = "#000000"
    opacity: float = Field(default=0.5, ge=0, le=1)
    x: float = 0
    y: float = 0
    w: float | None = None  # None => full canvas
    h: float | None = None
    fade: Literal["none", "right", "left"] = "none"   # fade to transparent across the box so there is no hard edge
    solid_until: float = Field(default=0.7, ge=0, le=1)  # fraction of the box that stays at full opacity before fading


class Background(Strict):
    type: Literal["solid", "gradient", "image"] = "solid"
    color: Hex = "#ffffff"
    gradient_to: Hex | None = None
    angle: float = 180  # CSS-like: 180 = top -> bottom
    asset_id: uuid.UUID | None = None
    scrims: list[Scrim] = Field(default_factory=list, max_length=4)
    fit: Literal["cover", "contain", "fill"] = "cover"    # image backgrounds
    locked: bool = False

    @model_validator(mode="after")
    def _check(self):
        if self.type == "image" and not self.asset_id:
            raise ValueError("image background needs asset_id")
        if self.type == "gradient" and not self.gradient_to:
            raise ValueError("gradient background needs gradient_to")
        return self


class ElBase(Strict):
    id: ElId
    role: Role = "decor"
    name: str = Field(default="", max_length=80)
    x: float
    y: float
    w: float = Field(gt=0, le=16384)
    h: float = Field(gt=0, le=16384)
    opacity: float = Field(default=1, ge=0, le=1)
    locked: bool = False
    visible: bool = True                                   # M7: hidden layers are skipped by renderer and quality checks
    rotation: float = Field(default=0, ge=-360, le=360)    # M7: degrees, about the element centre
    slot: Slot | None = None                               # M7 templates: semantic role; None => inferred from `role`/type when a template is applied


class Crop(Strict):
    """Source-image crop as fractions (0..1) of the original pixels. The original asset is never modified."""
    x: float = Field(default=0, ge=0, le=1)
    y: float = Field(default=0, ge=0, le=1)
    w: float = Field(default=1, gt=0, le=1)
    h: float = Field(default=1, gt=0, le=1)

    @model_validator(mode="after")
    def _inside(self):
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValueError("crop must lie inside the image")
        return self


class ImageEl(ElBase):
    type: Literal["image"] = "image"
    asset_id: uuid.UUID
    fit: Literal["contain", "cover", "fill"] = "contain"   # generation only uses contain/cover; "fill" (stretch) is an explicit editor choice
    shadow: Literal["none", "contact", "drop"] = "none"
    crop: Crop | None = None
    corner_radius: float = Field(default=0, ge=0, le=2000)


class TextEl(ElBase):
    type: Literal["text"] = "text"
    text: str = Field(max_length=300)
    font_family: FontFamily = "Liberation Sans"
    font_weight: Literal[400, 700] = 400
    font_size: float = Field(ge=6, le=600)
    min_font_size: float = Field(default=10, ge=6, le=600)
    max_lines: int = Field(default=3, ge=1, le=8)
    auto_fit: bool = True    # False => font size is exactly font_size (overflow is reported, never silently shrunk)
    line_height: float = Field(default=1.15, ge=0.9, le=2.0)
    color: Hex = "#111111"
    align: Literal["left", "center", "right"] = "left"
    valign: Literal["top", "middle", "bottom"] = "top"
    letter_spacing: float = Field(default=0, ge=-5, le=40)
    uppercase: bool = False


class ShapeEl(ElBase):
    type: Literal["shape"] = "shape"
    shape: Literal["rect", "ellipse"] = "rect"
    fill: Hex = "#cccccc"
    radius: float = Field(default=0, ge=0, le=2000)
    stroke: Hex | None = None
    stroke_width: float = Field(default=0, ge=0, le=100)


class ButtonEl(ElBase):
    type: Literal["button"] = "button"
    label: str = Field(max_length=30)
    fill: Hex = "#ff5a1f"
    text_color: Hex = "#ffffff"
    font_family: FontFamily = "Liberation Sans"
    font_weight: Literal[400, 700] = 700
    font_size: float = Field(ge=6, le=300)
    min_font_size: float = Field(default=9, ge=6, le=300)
    radius: float = Field(default=8, ge=0, le=2000)
    uppercase: bool = False
    padding_x: float | None = Field(default=None, ge=0, le=500)   # None => 8% of width (legacy behaviour)


Element = Annotated[Union[ImageEl, TextEl, ShapeEl, ButtonEl], Field(discriminator="type")]


class SpecMeta(Strict):
    concept_id: str | None = None
    template: str | None = None
    family: str | None = None
    notes: list[str] = Field(default_factory=list, max_length=20)
    source_template_id: str | None = Field(default=None, max_length=40)       # M7 templates: provenance only; the design stays independently editable
    source_template_version: int | None = None


class CreativeSpec(Strict):
    version: Literal[1] = 1
    canvas: Canvas
    background: Background
    elements: list[Element] = Field(default_factory=list, max_length=40)  # painter's order: first = bottom layer
    meta: SpecMeta = Field(default_factory=SpecMeta)

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [e.id for e in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("element ids must be unique")
        return self

    def asset_ids(self) -> set[uuid.UUID]:
        ids = {e.asset_id for e in self.elements if isinstance(e, ImageEl)}
        if self.background.asset_id:
            ids.add(self.background.asset_id)
        return ids
