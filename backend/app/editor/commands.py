"""Centralised editing command engine (pure, immutable). The server is authoritative; the frontend mirrors these rules for optimistic updates.

apply_command(doc, command) -> (new_doc, before_patch, after_patch)
  * the input document is never mutated (deep copy), the result is re-validated through CreativeSpec,
  * patches record only what changed: {"layers": {id: layer|None}, "order": [ids], "background": dict|None},
    so `before` is an exact inverse and undo/redo never has to guess.
"""
import copy
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, TypeAdapter

from app.rendering.spec import CreativeSpec, Element, ElId

BG = "background"
EDGES = ("left", "center", "right", "top", "middle", "bottom")


class CommandError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddLayer(_P):
    layer: dict
    index: int | None = None
class UpdateText(_P):
    layer_id: ElId
    text: str = Field(max_length=300)
class UpdateStyle(_P):
    layer_id: str
    changes: dict[str, Any]
class Move(_P):
    layer_id: ElId
    x: float
    y: float
class Resize(_P):
    layer_id: ElId
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    x: float | None = None
    y: float | None = None
class Rotate(_P):
    layer_id: ElId
    rotation: float
class ChangeColor(_P):
    layer_id: str
    field: Literal["color", "fill", "stroke", "text_color", "gradient_to"]
    value: str
class ReplaceImage(_P):
    layer_id: ElId
    asset_id: uuid.UUID
class ChangeBackground(_P):
    background: dict
class Crop(_P):
    layer_id: ElId
    crop: dict | None
class Reorder(_P):
    layer_id: ElId
    to_index: int | None = None
    direction: Literal["front", "back", "forward", "backward"] | None = None
class Duplicate(_P):
    layer_id: ElId
    new_id: ElId | None = None
    dx: float = 12
    dy: float = 12
class Delete(_P):
    layer_id: ElId
class Visibility(_P):
    layer_id: ElId
    visible: bool | None = None
class Lock(_P):
    layer_id: str
    locked: bool | None = None
class Align(_P):
    layer_ids: list[ElId] = Field(min_length=1, max_length=40)
    edge: Literal["left", "center", "right", "top", "middle", "bottom"]
    to: Literal["canvas", "selection"] = "canvas"
class Distribute(_P):
    layer_ids: list[ElId] = Field(min_length=3, max_length=40)
    axis: Literal["horizontal", "vertical"]
class RegenAccepted(_P):
    layer_id: str
    asset_id: uuid.UUID | None = None
    text: str | None = Field(default=None, max_length=300)
    job_id: uuid.UUID | None = None
class ApplyTemplate(_P):
    """M7 templates: replace the document with a previewed, already-validated candidate (same canvas). Logged as ONE operation whose before/after
    cover every changed layer, the order and the background, so undo/redo restore the exact previous/applied state."""
    candidate: dict
    application_id: str | None = None
class RevertTemplate(_P):
    snapshot: dict                      # the exact pre-application document, stored with the application
    application_id: str | None = None
class CopyLayer(_P):
    layer: dict                         # already scaled to this artboard by the caller; gets a fresh id
    index: int | None = None
    source_document_id: str | None = None
class ApplyPatch(_P):
    """Internal: used by undo/redo/restore. Patches come from earlier operations, never from the browser's imagination: still fully validated."""
    patch: dict
    label: Literal["UNDO", "REDO", "RESTORE"] = "UNDO"


COMMANDS: dict[str, type[_P]] = {
    "ADD_LAYER": AddLayer, "UPDATE_TEXT": UpdateText, "UPDATE_STYLE": UpdateStyle, "MOVE_ELEMENT": Move, "RESIZE_ELEMENT": Resize,
    "ROTATE_ELEMENT": Rotate, "CHANGE_COLOR": ChangeColor, "REPLACE_IMAGE": ReplaceImage, "CHANGE_BACKGROUND": ChangeBackground,
    "CROP_IMAGE": Crop, "REORDER_LAYER": Reorder, "DUPLICATE_ELEMENT": Duplicate, "DELETE_ELEMENT": Delete,
    "TOGGLE_VISIBILITY": Visibility, "TOGGLE_LOCK": Lock, "ALIGN_ELEMENT": Align, "DISTRIBUTE_ELEMENTS": Distribute,
    "REGENERATE_ELEMENT_ACCEPTED": RegenAccepted, "APPLY_PATCH": ApplyPatch,
    "APPLY_TEMPLATE": ApplyTemplate, "REVERT_TEMPLATE_APPLICATION": RevertTemplate, "COPY_LAYER_TO_ARTBOARD": CopyLayer,
}
PROTECTED = {"id", "type", "asset_id"}   # never changeable through UPDATE_STYLE (asset_id only via REPLACE_IMAGE: ownership-checked)
_el_adapter = TypeAdapter(Element)


def _find(layers: dict, lid: str) -> dict:
    if lid not in layers:
        raise CommandError("layer_not_found", f"Layer {lid} not found")
    return layers[lid]


def _unlocked(layers: dict, lid: str) -> dict:
    el = _find(layers, lid)
    if el.get("locked"):
        raise CommandError("layer_locked", f"Layer {lid} is locked")
    return el


def _new_id(layers: dict, wanted: str | None) -> str:
    if wanted:
        if wanted in layers:
            raise CommandError("duplicate_id", f"Layer id {wanted} already exists")
        return wanted
    while True:
        i = "l" + uuid.uuid4().hex[:11]
        if i not in layers:
            return i


def _bbox(el: dict) -> tuple[float, float, float, float]:
    return el["x"], el["y"], el["x"] + el["w"], el["y"] + el["h"]


def apply_command(doc: dict, command: dict) -> tuple[dict, dict, dict]:
    ctype = command.get("type")
    model = COMMANDS.get(ctype)
    if not model:
        raise CommandError("unknown_command", f"Unknown command {ctype!r}")
    try:
        p = model.model_validate(command.get("payload") or {})
    except ValidationError as e:
        raise CommandError("invalid_payload", "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from e

    new = copy.deepcopy(doc)
    els: list[dict] = new["elements"]
    layers = {e["id"]: e for e in els}
    before_order = [e["id"] for e in els]
    touched: set[str] = set()
    bg_touched = False
    meta_touched = False
    W, H = new["canvas"]["width"], new["canvas"]["height"]

    def touch(*ids): touched.update(ids)

    def to_canvas_check(el):
        x0, y0, x1, y1 = _bbox(el)
        if x1 <= 0 or y1 <= 0 or x0 >= W or y0 >= H:
            raise CommandError("out_of_canvas", "Layer would be entirely outside the canvas")

    if ctype == "ADD_LAYER":
        lay = dict(p.layer)
        lay["id"] = _new_id(layers, lay.get("id"))
        try:
            _el_adapter.validate_python(lay)
        except ValidationError as e:
            raise CommandError("invalid_layer", str(e.errors()[0]["msg"])) from e
        to_canvas_check(lay)
        idx = len(els) if p.index is None else max(0, min(p.index, len(els)))
        els.insert(idx, lay)
        touch(lay["id"])
    elif ctype == "UPDATE_TEXT":
        el = _unlocked(layers, p.layer_id)
        if el["type"] == "text":
            el["text"] = p.text
        elif el["type"] == "button":
            el["label"] = p.text
        else:
            raise CommandError("wrong_type", "Only text and CTA layers have text")
        touch(p.layer_id)
    elif ctype == "UPDATE_STYLE":
        if p.layer_id == BG:
            if new["background"].get("locked"):
                raise CommandError("layer_locked", "Background is locked")
            bad = set(p.changes) & {"type", "asset_id", "locked"}
            if bad:
                raise CommandError("forbidden_field", f"Use dedicated commands for {sorted(bad)}")
            new["background"].update(p.changes)
            bg_touched = True
        else:
            el = _unlocked(layers, p.layer_id)
            bad = set(p.changes) & (PROTECTED | {"x", "y", "w", "h", "rotation", "locked", "visible"})
            if bad:
                raise CommandError("forbidden_field", f"Use dedicated commands for {sorted(bad)}")
            el.update(p.changes)
            touch(p.layer_id)
    elif ctype == "MOVE_ELEMENT":
        el = _unlocked(layers, p.layer_id)
        el["x"], el["y"] = p.x, p.y
        to_canvas_check(el)
        touch(p.layer_id)
    elif ctype == "RESIZE_ELEMENT":
        el = _unlocked(layers, p.layer_id)
        el["w"], el["h"] = p.w, p.h
        if p.x is not None: el["x"] = p.x
        if p.y is not None: el["y"] = p.y
        to_canvas_check(el)
        touch(p.layer_id)
    elif ctype == "ROTATE_ELEMENT":
        el = _unlocked(layers, p.layer_id)
        r = ((p.rotation + 180) % 360) - 180       # normalise to [-180, 180)
        el["rotation"] = r
        touch(p.layer_id)
    elif ctype == "CHANGE_COLOR":
        if p.layer_id == BG:
            if p.field not in ("color", "gradient_to"):
                raise CommandError("wrong_field", "Background colour fields are color/gradient_to")
            new["background"][p.field] = p.value
            bg_touched = True
        else:
            el = _unlocked(layers, p.layer_id)
            if p.field not in el and not (p.field == "stroke" and el["type"] == "shape"):
                raise CommandError("wrong_field", f"{el['type']} layers have no '{p.field}'")
            el[p.field] = p.value
            touch(p.layer_id)
    elif ctype == "REPLACE_IMAGE":
        el = _unlocked(layers, p.layer_id)
        if el["type"] != "image":
            raise CommandError("wrong_type", "Only image layers can be replaced")
        el["asset_id"] = str(p.asset_id)
        el["crop"] = None                       # a crop of the old picture is meaningless for the new one
        touch(p.layer_id)
    elif ctype == "CHANGE_BACKGROUND":
        if new["background"].get("locked"):
            raise CommandError("layer_locked", "Background is locked")
        nb = {"scrims": new["background"].get("scrims", []), **p.background, "locked": new["background"].get("locked", False)}
        new["background"] = nb
        bg_touched = True
    elif ctype == "CROP_IMAGE":
        el = _unlocked(layers, p.layer_id)
        if el["type"] != "image":
            raise CommandError("wrong_type", "Only image layers can be cropped")
        el["crop"] = p.crop
        touch(p.layer_id)
    elif ctype == "REORDER_LAYER":
        el = _unlocked(layers, p.layer_id)
        i = els.index(el)
        if p.to_index is not None:
            j = max(0, min(p.to_index, len(els) - 1))
        elif p.direction:
            j = {"front": len(els) - 1, "back": 0, "forward": min(i + 1, len(els) - 1), "backward": max(i - 1, 0)}[p.direction]
        else:
            raise CommandError("invalid_payload", "to_index or direction required")
        els.insert(j, els.pop(i))
    elif ctype == "DUPLICATE_ELEMENT":
        src = _find(layers, p.layer_id)
        if len(els) >= 40:
            raise CommandError("too_many_layers", "A banner can have at most 40 layers")
        dup = copy.deepcopy(src)
        dup["id"] = _new_id(layers, p.new_id)
        dup["x"] += p.dx
        dup["y"] += p.dy
        dup["locked"] = False
        dup["name"] = (src.get("name") or src["id"])[:70] + " copy"
        els.insert(els.index(src) + 1, dup)
        touch(dup["id"])
    elif ctype == "DELETE_ELEMENT":
        el = _unlocked(layers, p.layer_id)
        els.remove(el)
        touch(p.layer_id)
    elif ctype == "TOGGLE_VISIBILITY":
        el = _find(layers, p.layer_id)
        el["visible"] = (not el.get("visible", True)) if p.visible is None else p.visible
        touch(p.layer_id)
    elif ctype == "TOGGLE_LOCK":
        if p.layer_id == BG:
            new["background"]["locked"] = (not new["background"].get("locked")) if p.locked is None else p.locked
            bg_touched = True
        else:
            el = _find(layers, p.layer_id)
            el["locked"] = (not el.get("locked")) if p.locked is None else p.locked
            touch(p.layer_id)
    elif ctype == "ALIGN_ELEMENT":
        targets = [_unlocked(layers, i) for i in p.layer_ids]
        if p.to == "selection" and len(targets) > 1:
            x0, y0 = min(t["x"] for t in targets), min(t["y"] for t in targets)
            x1, y1 = max(t["x"] + t["w"] for t in targets), max(t["y"] + t["h"] for t in targets)
        else:
            x0, y0, x1, y1 = 0, 0, W, H
        for t in targets:
            if p.edge == "left": t["x"] = x0
            elif p.edge == "right": t["x"] = x1 - t["w"]
            elif p.edge == "center": t["x"] = (x0 + x1) / 2 - t["w"] / 2
            elif p.edge == "top": t["y"] = y0
            elif p.edge == "bottom": t["y"] = y1 - t["h"]
            else: t["y"] = (y0 + y1) / 2 - t["h"] / 2
            touch(t["id"])
    elif ctype == "DISTRIBUTE_ELEMENTS":
        targets = [_unlocked(layers, i) for i in p.layer_ids]
        k, size = ("x", "w") if p.axis == "horizontal" else ("y", "h")
        targets.sort(key=lambda t: t[k])
        span = targets[-1][k] + targets[-1][size] - targets[0][k]
        gap = (span - sum(t[size] for t in targets)) / (len(targets) - 1)
        cur = targets[0][k]
        for t in targets:
            t[k] = cur
            cur += t[size] + gap
            touch(t["id"])
    elif ctype == "REGENERATE_ELEMENT_ACCEPTED":
        if p.layer_id == BG:
            if not p.asset_id:
                raise CommandError("invalid_payload", "asset_id required for a background")
            if new["background"].get("locked"):
                raise CommandError("layer_locked", "Background is locked")
            new["background"].update({"type": "image", "asset_id": str(p.asset_id)})
            bg_touched = True
        else:
            el = _unlocked(layers, p.layer_id)
            if el["type"] == "image" and p.asset_id:
                el["asset_id"] = str(p.asset_id)
                el["crop"] = None
            elif el["type"] == "shape" and p.asset_id:      # a decorative shape replaced by a generated decorative image, same box/order
                keep = {k: el[k] for k in ("id", "role", "name", "x", "y", "w", "h", "opacity", "locked", "visible", "rotation")}
                els[els.index(el)] = {**keep, "type": "image", "asset_id": str(p.asset_id), "fit": "cover", "shadow": "none", "corner_radius": el.get("radius", 0)}
            elif el["type"] in ("text", "button") and p.text is not None:
                el["text" if el["type"] == "text" else "label"] = p.text
            else:
                raise CommandError("invalid_payload", "Replacement does not fit the selected layer type")
            touch(p.layer_id)
    elif ctype in ("APPLY_TEMPLATE", "REVERT_TEMPLATE_APPLICATION"):
        target = p.candidate if ctype == "APPLY_TEMPLATE" else p.snapshot
        try:
            tdoc = CreativeSpec.model_validate(target).model_dump(mode="json")
        except ValidationError as e:
            x = e.errors()[0]
            raise CommandError("invalid_document", f"{'.'.join(map(str, x['loc']))}: {x['msg']}") from e
        if tdoc["canvas"] != doc["canvas"]:
            raise CommandError("invalid_document", "A template can never change the canvas size")
        t_by_id = {t["id"]: t for t in tdoc["elements"]}
        for e in els:      # locked layers are never moved, restyled or removed by a template
            if e.get("locked") and t_by_id.get(e["id"]) != e:
                raise CommandError("layer_locked", f"Layer {e['id']} is locked")
        if doc["background"].get("locked") and tdoc["background"] != doc["background"]:
            raise CommandError("layer_locked", "Background is locked")
        new = tdoc
        old_l = {e["id"]: e for e in els}
        new_l = {e["id"]: e for e in tdoc["elements"]}
        touched.update(i for i in new_l if old_l.get(i) != new_l[i])
        bg_touched = tdoc["background"] != doc["background"]
        meta_touched = tdoc["meta"] != doc["meta"]          # provenance (source template) travels with the operation so undo/redo restore it too
    elif ctype == "COPY_LAYER_TO_ARTBOARD":
        lay = dict(p.layer)
        lay["id"] = _new_id(layers, None)
        lay["locked"] = False
        lay["name"] = ((lay.get("name") or "Layer")[:66]) + " (copied)"
        lay.pop("z_index", None)
        if len(els) >= 40:
            raise CommandError("too_many_layers", "A banner can have at most 40 layers")
        try:
            _el_adapter.validate_python(lay)
        except ValidationError as e:
            raise CommandError("invalid_layer", str(e.errors()[0]["msg"])) from e
        to_canvas_check(lay)
        els.insert(len(els) if p.index is None else max(0, min(p.index, len(els))), lay)
        touch(lay["id"])
    elif ctype == "APPLY_PATCH":
        patch = p.patch
        for lid, val in (patch.get("layers") or {}).items():
            if val is None:
                layers.pop(lid, None)
            else:
                layers[lid] = val
            touch(lid)
        if patch.get("background") is not None:
            new["background"] = patch["background"]
            bg_touched = True
        if patch.get("meta") is not None:
            new["meta"] = patch["meta"]
            meta_touched = True
        order = patch.get("order") or before_order
        new["elements"] = els = [layers[i] for i in order if i in layers]

    # ---- validate the whole document & build patches
    try:
        valid = CreativeSpec.model_validate(new)
    except ValidationError as e:
        x = e.errors()[0]
        raise CommandError("invalid_document", f"{'.'.join(map(str, x['loc']))}: {x['msg']}") from e
    out = valid.model_dump(mode="json")
    old_layers = {e["id"]: e for e in doc["elements"]}
    new_layers = {e["id"]: e for e in out["elements"]}
    ids = touched | {i for i in new_layers if i not in old_layers} | {i for i in old_layers if i not in new_layers}
    new_order = [e["id"] for e in out["elements"]]
    before = {"layers": {i: old_layers.get(i) for i in ids}, "order": before_order}
    after = {"layers": {i: new_layers.get(i) for i in ids}, "order": new_order}
    if bg_touched:
        before["background"], after["background"] = doc["background"], out["background"]
    if meta_touched:
        before["meta"], after["meta"] = doc["meta"], out["meta"]
    if out == doc:
        raise CommandError("no_change", "Command made no change")
    return out, before, after


def referenced_asset_ids(doc: dict) -> set[str]:
    ids = {e["asset_id"] for e in doc.get("elements", []) if e.get("asset_id")}
    if doc.get("background", {}).get("asset_id"):
        ids.add(doc["background"]["asset_id"])
    return ids
