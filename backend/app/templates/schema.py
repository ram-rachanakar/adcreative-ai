"""Strict, declarative template definitions. Nothing here can carry HTML, scripts, URLs or arbitrary SVG: a layout is numbers, enums,
allow-listed style keys and asset *ids* (which are tenant-checked when a template is saved/applied)."""
import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.rendering.spec import Background, FontFamily, Hex, Scrim

SCHEMA_VERSION = 1
SlotRole = Literal["headline_primary", "headline_secondary", "body_copy", "product_primary", "product_secondary", "brand_logo",
                   "primary_cta", "secondary_cta", "promotion_badge", "decorative_shape"]
SLOT_ROLES: tuple[str, ...] = SlotRole.__args__  # type: ignore[attr-defined]
SlotKind = Literal["text", "image", "button", "shape"]
ALLOWED_KINDS: dict[str, set[str]] = {   # layer types a slot role accepts (type-compatibility rule of the mapper)
    "headline_primary": {"text"}, "headline_secondary": {"text"}, "body_copy": {"text"}, "product_primary": {"image"}, "product_secondary": {"image"},
    "brand_logo": {"image"}, "primary_cta": {"button"}, "secondary_cta": {"button"}, "promotion_badge": {"text", "button", "shape"}, "decorative_shape": {"shape", "image"},
}
MODES = ("layout_only", "layout_style", "full")
Mode = Literal["layout_only", "layout_style", "full"]
INDUSTRIES = ["beauty", "fashion", "electronics", "food", "home", "fitness", "services", "general"]
STYLES = ["minimal", "luxury", "modern", "bold", "editorial", "corporate", "promotional"]
LAYOUTS = ["centered", "split", "full-bleed", "product-focused", "text-focused"]
STYLE_KEYS: dict[str, set[str]] = {
    "text": {"font_family", "font_weight", "font_size", "min_font_size", "max_lines", "auto_fit", "line_height", "color", "align", "valign", "letter_spacing", "uppercase", "opacity"},
    "button": {"fill", "text_color", "font_family", "font_weight", "font_size", "min_font_size", "radius", "uppercase", "padding_x", "opacity"},
    "shape": {"shape", "fill", "radius", "stroke", "stroke_width", "opacity"},
    "image": {"fit", "shadow", "corner_radius", "opacity"},
}
_PLACEHOLDER = re.compile(r"^\{\{[a-z_]{1,40}\}\}$")
SlotId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,40}$")]


class S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Transform(S):
    """Fractions of the layout's canvas (0..1), so a layout is resolution-independent but still bound to one exact size."""
    x: float = Field(ge=-0.5, le=1.5)
    y: float = Field(ge=-0.5, le=1.5)
    w: float = Field(gt=0, le=2)
    h: float = Field(gt=0, le=2)
    rotation: float = Field(default=0, ge=-360, le=360)


class Constraints(S):
    min_font_size: float | None = Field(default=None, ge=6, le=600)
    max_lines: int | None = Field(default=None, ge=1, le=8)
    min_w_px: float | None = Field(default=None, ge=0, le=8192)
    min_h_px: float | None = Field(default=None, ge=0, le=8192)
    image_fit: Literal["contain", "cover"] | None = None


class ContentPolicy(S):
    mode: Literal["from_document", "placeholder", "static"] = "from_document"
    placeholder: str | None = None           # e.g. {{headline_primary}} — saved templates carry placeholders, never customer copy
    text: str | None = Field(default=None, max_length=300)   # static copy (only for template-owned wording, applied in Full mode with confirmation)

    @model_validator(mode="after")
    def _ok(self):
        if self.placeholder is not None and not _PLACEHOLDER.match(self.placeholder):
            raise ValueError("placeholder must look like {{name}}")
        if self.mode == "static" and self.text is None:
            raise ValueError("static content needs text")
        return self


class AssetPolicy(S):
    mode: Literal["from_document", "static"] = "from_document"
    asset_id: uuid.UUID | None = None        # static: template-owned asset (same-organization only)

    @model_validator(mode="after")
    def _ok(self):
        if self.mode == "static" and not self.asset_id:
            raise ValueError("static asset policy needs asset_id")
        return self


class Slot(S):
    id: SlotId
    role: SlotRole
    kind: SlotKind
    required: bool = False
    transform: Transform
    z: int = Field(default=0, ge=0, le=100)
    constraints: Constraints = Field(default_factory=Constraints)
    default_style: dict[str, Any] = Field(default_factory=dict)
    content_policy: ContentPolicy = Field(default_factory=ContentPolicy)
    asset_policy: AssetPolicy = Field(default_factory=AssetPolicy)

    @model_validator(mode="after")
    def _ok(self):
        if self.kind not in ALLOWED_KINDS[self.role]:
            raise ValueError(f"slot role {self.role} cannot be a {self.kind}")
        bad = set(self.default_style) - STYLE_KEYS[self.kind]
        if bad:
            raise ValueError(f"style keys not allowed for {self.kind}: {sorted(bad)}")
        for k, v in self.default_style.items():
            if isinstance(v, str) and k in ("color", "fill", "text_color", "stroke") and not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                raise ValueError(f"{k} must be #rrggbb")
            if isinstance(v, str) and k == "font_family" and v not in FontFamily.__args__:  # type: ignore[attr-defined]
                raise ValueError("font family not allowed")
            if isinstance(v, (dict, list)):
                raise ValueError("style values must be scalars")
        return self


class LayoutBackground(S):
    type: Literal["solid", "gradient", "image"] = "solid"
    color: Hex = "#ffffff"
    gradient_to: Hex | None = None
    angle: float = 180
    asset_id: uuid.UUID | None = None
    fit: Literal["cover", "contain", "fill"] = "cover"
    scrims: list[Scrim] = Field(default_factory=list, max_length=4)

    def to_spec(self) -> dict:
        Background.model_validate(self.model_dump(mode="json"))   # raises if inconsistent (image without asset, gradient without end colour)
        return self.model_dump(mode="json")


class LayoutDef(S):
    """One exact-size layout. `extends` names another layout (format id) of the same version; slots are merged by id, then validated as a whole."""
    extends: str | None = Field(default=None, max_length=80)
    slot_overrides: dict[SlotId, dict[str, Any]] = Field(default_factory=dict)
    remove_slots: list[SlotId] = Field(default_factory=list)
    background: LayoutBackground | None = None
    slots: list[Slot] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _ok(self):
        ids = [s.id for s in self.slots]
        if len(ids) != len(set(ids)):
            raise ValueError("slot ids must be unique")
        if not self.extends and not self.slots:
            raise ValueError("a layout needs slots or must extend another layout")
        return self


class BrandConstraints(S):
    """Template-level wishes about brand handling. Mandatory *brand profile* rules always win over template styling."""
    min_logo_width_px: float = Field(default=0, ge=0, le=2000)
    logo_clear_space_pct: float = Field(default=0, ge=0, le=0.5)
    respect_brand_colors: bool = True
    respect_brand_fonts: bool = True


class Definition(S):
    palette: list[Hex] = Field(default_factory=list, max_length=8)
    fonts: list[FontFamily] = Field(default_factory=list, max_length=3)
    notes: str = Field(default="", max_length=400)
    sample: dict[str, str] = Field(default_factory=dict)       # placeholder copy for thumbnails only; never applied to documents
    source: Literal["built_in", "manual", "saved_from_design", "duplicated"] = "manual"
    source_template_id: str | None = Field(default=None, max_length=40)


def validate_layout(raw: dict) -> LayoutDef:
    return LayoutDef.model_validate(raw)


def resolve_layout(layouts: dict[str, dict], format_id: str, _seen: tuple[str, ...] = ()) -> dict:
    """Resolve `extends` into a complete layout (no inheritance left). Cycles and missing parents are errors, never silently ignored."""
    if format_id in _seen:
        raise ValueError("layout inheritance cycle: " + " -> ".join(_seen + (format_id,)))
    if format_id not in layouts:
        raise ValueError(f"layout {format_id!r} not found")
    cur = LayoutDef.model_validate(layouts[format_id])
    if not cur.extends:
        return cur.model_dump(mode="json")
    base = resolve_layout(layouts, cur.extends, _seen + (format_id,))
    slots = {s["id"]: s for s in base["slots"] if s["id"] not in cur.remove_slots}
    for sid, patch in cur.slot_overrides.items():
        if sid not in slots:
            raise ValueError(f"override targets unknown slot {sid!r}")
        slots[sid] = _merge(slots[sid], patch)
    for s in cur.slots:
        slots[s.id] = s.model_dump(mode="json")
    out = {"extends": None, "slot_overrides": {}, "remove_slots": [], "background": (cur.background.model_dump(mode="json") if cur.background else base["background"]),
           "slots": list(slots.values())}
    return LayoutDef.model_validate(out).model_dump(mode="json")


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out
