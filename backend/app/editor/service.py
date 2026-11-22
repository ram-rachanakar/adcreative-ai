"""Document persistence, revisioning, versions, server-side undo/redo. Writes through to GeneratedCreative so gallery, quality (M6)
and export (M5) keep reading the one spec — there is no second copy of the design."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.creative import formats
from app.creative.service import _persist_render, make_resolver
from app.editor import commands as C
from app.models import Campaign, CreativeAsset, CreativeDocument, CreativeVersion, EditOperation, GeneratedCreative, ProductAsset
from app.rendering import fonts
from app.rendering.spec import CreativeSpec
from app.rendering.svg import render_spec
from app.repositories.org_repo import OrgRepo
from app.storage.base import Storage

SCHEMA_VERSION = 2


class RevisionConflict(Exception):
    def __init__(self, current: int):
        self.current = current


def _now():
    return datetime.now(timezone.utc)


def get_or_create(db: Session, org_id: uuid.UUID, creative: GeneratedCreative, user_id: uuid.UUID) -> CreativeDocument:
    doc = db.scalar(select(CreativeDocument).where(CreativeDocument.creative_id == creative.id, CreativeDocument.organization_id == org_id))
    if doc:
        return doc
    camp = OrgRepo(db, Campaign, org_id).get(creative.campaign_id)
    keys = creative.format_keys or []
    spec = CreativeSpec.model_validate(creative.spec).model_dump(mode="json")   # also upgrades v1 specs (new fields get defaults)
    doc = CreativeDocument(organization_id=org_id, campaign_id=creative.campaign_id, creative_id=creative.id, format_id=keys[0] if keys else f"{creative.width}x{creative.height}",
                           name=f"{camp.name if camp else 'Banner'} {creative.width}x{creative.height}", width=creative.width, height=creative.height,
                           document=spec, revision=0, rendered_revision=0 if creative.render_asset_id else None, created_by=user_id, updated_by=user_id)
    db.add(doc)
    db.flush()
    make_version(db, doc, "Original", "Generated design", "initial", user_id)
    return doc


def make_version(db: Session, doc: CreativeDocument, name: str, summary: str, kind: str, user_id: uuid.UUID | None) -> CreativeVersion:
    v = CreativeVersion(organization_id=doc.organization_id, document_id=doc.id, revision=doc.revision, snapshot=doc.document, version_name=name[:200],
                        change_summary=summary[:400], kind=kind, created_by=user_id)
    db.add(v)
    doc.last_version_at = _now()
    db.flush()
    return v


def view(db: Session, org_id: uuid.UUID, doc: CreativeDocument, storage: Storage) -> dict:
    d = doc.document
    assets: dict[str, dict] = {}

    def asset_info(aid: str | None):
        if not aid or aid in assets:
            return
        u = uuid.UUID(aid)
        row = OrgRepo(db, ProductAsset, org_id).get(u) or OrgRepo(db, CreativeAsset, org_id).get(u)
        assets[aid] = {"id": aid, "url": storage.signed_url(row.storage_key, 3600), "width": row.width, "height": row.height, "mime": row.mime} if row else {"id": aid, "missing": True}

    layers = []
    for i, e in enumerate(d["elements"]):
        asset_info(e.get("asset_id"))
        layers.append({**e, "z_index": i + 1})
    asset_info(d["background"].get("asset_id"))
    keys = [f for f in (formats.get(k) for k in (db.get(GeneratedCreative, doc.creative_id).format_keys or [])) if f]
    zone = next((f.safe_zone.model_dump() for f in keys if f.safe_zone), None)
    return {"id": str(doc.id), "creative_id": str(doc.creative_id), "campaign_id": str(doc.campaign_id), "format_id": doc.format_id, "name": doc.name,
            "schema_version": doc.schema_version, "revision": doc.revision, "rendered_revision": doc.rendered_revision, "status": doc.status,
            "canvas": d["canvas"], "background": {**d["background"], "layer_id": C.BG}, "layers": layers, "metadata": d["meta"], "assets": assets,
            "artboard": {"width": doc.width, "height": doc.height, "format_id": doc.format_id, "safe_zone": zone},
            "fonts": [{"family": f, "weight": w, "file": file} for (f, w), file in fonts.REGISTRY.items()],
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None}


def _check_assets(db: Session, org_id: uuid.UUID, ids: set[str]) -> None:
    for a in ids:
        u = uuid.UUID(a)
        if not (OrgRepo(db, ProductAsset, org_id).get(u) or OrgRepo(db, CreativeAsset, org_id).get(u)):   # tenant-scoped: foreign ids look missing
            raise C.CommandError("asset_not_found", f"Asset {a} not found")


def lock(db: Session, org_id: uuid.UUID, document_id: uuid.UUID) -> CreativeDocument | None:
    return db.scalar(select(CreativeDocument).where(CreativeDocument.id == document_id, CreativeDocument.organization_id == org_id).with_for_update())


def _batch_of(op: EditOperation) -> str | None:
    return (op.after or {}).get("_batch")


def apply_batch(db: Session, org_id: uuid.UUID, doc: CreativeDocument, creative: GeneratedCreative, user_id: uuid.UUID, commands: list[dict],
                expected_revision: int, *, batch_id: str | None = None, version: tuple[str, str, str] | None = None) -> tuple[list[EditOperation], bool]:
    """Atomically apply commands. Returns (operations, duplicate). Caller holds the row lock (see lock()) and commits.
    All-or-nothing: a failing command raises CommandError and nothing is written."""
    s = get_settings()
    if not commands or len(commands) > s.editor_max_batch:
        raise C.CommandError("invalid_payload", f"Between 1 and {s.editor_max_batch} commands per request")
    if batch_id:
        recent = db.scalars(select(EditOperation).where(EditOperation.document_id == doc.id).order_by(EditOperation.revision_after.desc()).limit(200)).all()
        dup = [o for o in recent if _batch_of(o) == batch_id]
        if dup:                                    # a retried request whose first attempt already committed
            return sorted(dup, key=lambda o: o.revision_after), True
    if doc.revision != expected_revision:
        raise RevisionConflict(doc.revision)
    batch_id = batch_id or uuid.uuid4().hex
    cur = doc.document
    ops: list[EditOperation] = []
    rev = doc.revision
    for cmd in commands:
        try:
            new, before, after = C.apply_command(cur, cmd)
        except C.CommandError as e:
            if e.code == "no_change":
                continue
            raise
        fresh = C.referenced_asset_ids(new) - C.referenced_asset_ids(cur)
        _check_assets(db, org_id, fresh)
        rev += 1
        before["_batch"] = after["_batch"] = batch_id
        payload = cmd.get("payload") or {}
        target = payload.get("layer_id") or (payload.get("layer_ids") or [None])[0]
        ops.append(EditOperation(organization_id=org_id, document_id=doc.id, revision_before=rev - 1, revision_after=rev, operation_type=cmd["type"],
                                 target_layer_id=target, before=before, after=after, created_by=user_id))
        cur = new
    if not ops:
        return [], False
    db.add_all(ops)
    commit_document(db, doc, creative, cur, rev, user_id)
    kind = None
    if version:
        make_version(db, doc, *version[:2], version[2], user_id)
    elif not doc.last_version_at or (_now() - doc.last_version_at).total_seconds() >= s.editor_version_interval_s:
        make_version(db, doc, "Auto checkpoint", f"Automatic checkpoint at revision {doc.revision}", "auto", user_id)
    db.flush()
    return ops, False


def commit_document(db, doc: CreativeDocument, creative: GeneratedCreative, new_doc: dict, revision: int, user_id) -> None:
    doc.document, doc.revision, doc.updated_by = new_doc, revision, user_id
    creative.spec = new_doc                       # write-through: quality/export/gallery read the same spec
    creative.spec_version += 1                    # marks quality reports stale (M6 semantics) and the render as outdated
    doc.updated_at = _now()


# ---------------------------------------------------------------- undo / redo (server side, survives reload)
def _units(ops: list[EditOperation]) -> list[tuple[str, list[EditOperation]]]:
    """Group the log into user-level units: consecutive ops with the same batch id and the same class (normal/undo/redo)."""
    out: list[tuple[str, list[EditOperation]]] = []
    for o in ops:
        cls = "undo" if o.operation_type == "UNDO" else "redo" if o.operation_type == "REDO" else "do"
        key = (cls, _batch_of(o))
        if out and out[-1][0] == key[0] and _batch_of(out[-1][1][-1]) == key[1] and key[1] is not None:
            out[-1][1].append(o)
        else:
            out.append((cls, [o]))
    return out


def stacks(db: Session, doc: CreativeDocument) -> tuple[list[list[EditOperation]], list[list[EditOperation]]]:
    ops = list(db.scalars(select(EditOperation).where(EditOperation.document_id == doc.id).order_by(EditOperation.revision_after)))
    undo: list = []
    redo: list = []
    for cls, unit in _units(ops):
        if cls == "do":
            undo.append(unit)
            redo.clear()                              # a new edit invalidates the redo branch
        elif cls == "undo" and undo:
            redo.append(undo.pop())
        elif cls == "redo" and redo:
            undo.append(redo.pop())
    return undo, redo


def undo_redo(db: Session, org_id: uuid.UUID, doc: CreativeDocument, creative: GeneratedCreative, user_id: uuid.UUID, which: str) -> list[EditOperation]:
    undo, redo = stacks(db, doc)
    unit = (undo[-1] if which == "undo" else redo[-1]) if (undo if which == "undo" else redo) else None
    if not unit:
        raise C.CommandError("nothing_to_" + which, f"Nothing to {which}")
    batch = uuid.uuid4().hex
    cur, rev, new_ops = doc.document, doc.revision, []
    for o in (reversed(unit) if which == "undo" else unit):
        patch = {k: v for k, v in (o.before if which == "undo" else o.after).items() if k != "_batch"}
        _check_assets(db, org_id, C.referenced_asset_ids({"elements": [x for x in (patch.get("layers") or {}).values() if x], "background": patch.get("background") or {}}))
        new, before, after = C.apply_command(cur, {"type": "APPLY_PATCH", "payload": {"patch": patch, "label": which.upper()}})
        rev += 1
        before["_batch"] = after["_batch"] = batch
        new_ops.append(EditOperation(organization_id=org_id, document_id=doc.id, revision_before=rev - 1, revision_after=rev, operation_type=which.upper(),
                                     target_layer_id=o.target_layer_id, before=before, after=after, created_by=user_id, reverts_operation_id=o.id))
        cur = new
    db.add_all(new_ops)
    commit_document(db, doc, creative, cur, rev, user_id)
    db.flush()
    return new_ops


def history(db: Session, doc: CreativeDocument, limit: int = 100) -> dict:
    undo, redo = stacks(db, doc)
    ops = db.scalars(select(EditOperation).where(EditOperation.document_id == doc.id).order_by(EditOperation.revision_after.desc()).limit(limit)).all()
    return {"revision": doc.revision, "can_undo": bool(undo), "can_redo": bool(redo), "undo_depth": len(undo), "redo_depth": len(redo),
            "operations": [{"id": str(o.id), "type": o.operation_type, "target_layer_id": o.target_layer_id, "revision_before": o.revision_before,
                            "revision_after": o.revision_after, "created_by": str(o.created_by) if o.created_by else None,
                            "created_at": o.created_at.isoformat() if o.created_at else None} for o in ops]}


# ---------------------------------------------------------------- versions
def version_out(v: CreativeVersion, with_snapshot: bool = False) -> dict:
    d = {"id": str(v.id), "document_id": str(v.document_id), "revision": v.revision, "version_name": v.version_name, "change_summary": v.change_summary,
         "kind": v.kind, "created_by": str(v.created_by) if v.created_by else None, "created_at": v.created_at.isoformat() if v.created_at else None}
    if with_snapshot:
        d["snapshot"] = v.snapshot
    return d


def restore(db: Session, org_id: uuid.UUID, doc: CreativeDocument, creative: GeneratedCreative, user_id: uuid.UUID, version: CreativeVersion) -> list[EditOperation]:
    """Restore = a NEW revision whose content equals the snapshot (history is never rewritten; the pre-restore state is checkpointed and undoable)."""
    snap = CreativeSpec.model_validate(version.snapshot).model_dump(mode="json")
    if (snap["canvas"]["width"], snap["canvas"]["height"]) != (doc.width, doc.height):
        raise C.CommandError("invalid_document", "Version has a different canvas size")
    _check_assets(db, org_id, C.referenced_asset_ids(snap))
    make_version(db, doc, "Before restore", f"State before restoring revision {version.revision}", "restore_point", user_id)
    cur = doc.document
    patch = {"layers": {**{i: None for i in (e["id"] for e in cur["elements"])}, **{e["id"]: e for e in snap["elements"]}},
             "order": [e["id"] for e in snap["elements"]], "background": snap["background"]}
    new, before, after = C.apply_command(cur, {"type": "APPLY_PATCH", "payload": {"patch": patch, "label": "RESTORE"}}) if snap != cur else (cur, None, None)
    if before is None:
        return []
    batch = uuid.uuid4().hex
    before["_batch"] = after["_batch"] = batch
    op = EditOperation(organization_id=org_id, document_id=doc.id, revision_before=doc.revision, revision_after=doc.revision + 1, operation_type="RESTORE",
                       target_layer_id=None, before=before, after=after, created_by=user_id)
    db.add(op)
    commit_document(db, doc, creative, new, doc.revision + 1, user_id)
    db.flush()
    return [op]


# ---------------------------------------------------------------- rendering
def ensure_fresh_render(db: Session, org_id: uuid.UUID, doc: CreativeDocument, creative: GeneratedCreative, storage: Storage) -> None:
    """Render the *document* if the stored render is older than the latest revision. Deterministic backend renderer only."""
    if doc.rendered_revision == doc.revision and creative.render_asset_id:
        return
    spec = CreativeSpec.model_validate(doc.document)
    _persist_render(db, storage, org_id, creative, render_spec(spec, make_resolver(db, org_id, storage)))
    doc.rendered_revision = doc.revision
    db.flush()


def run_render_job(db: Session, org_id: uuid.UUID, job_id: uuid.UUID, storage: Storage) -> None:
    from app.export.service import encode, validate_file
    from app.models import RenderJob
    j = OrgRepo(db, RenderJob, org_id).get(job_id)
    if not j or j.status not in ("pending", "processing"):
        return
    j.status = "processing"
    db.commit()
    try:
        doc = lock(db, org_id, j.document_id)
        creative = db.get(GeneratedCreative, doc.creative_id)
        ensure_fresh_render(db, org_id, doc, creative, storage)
        from app.creative.service import _store
        png = storage.get(OrgRepo(db, CreativeAsset, org_id).get(creative.render_asset_id).storage_key)
        enc = encode(png, j.output_format, j.quality)
        problems = validate_file(enc.data, j.output_format, doc.width, doc.height)
        if problems:
            raise ValueError("; ".join(problems))
        a = _store(db, storage, org_id, enc.data, enc.mime, "render", f"{doc.campaign_id}/exports/{doc.id}/r{doc.revision}",
                   campaign_id=doc.campaign_id, provider="local", model="resvg", params={"revision": doc.revision, "format": j.output_format})
        j.output_asset_id, j.revision, j.warnings, j.status = a.id, doc.revision, enc.warnings, "completed"
    except Exception as e:  # noqa: BLE001 - recorded on the job, never swallowed silently
        db.rollback()
        j = OrgRepo(db, RenderJob, org_id).get(job_id)
        j.status, j.error_message = "failed", f"{type(e).__name__}: {e}"[:500]
    j.completed_at = _now()
    db.commit()


# ---------------------------------------------------------------- duplicate design to another format
def adapt_spec(spec: dict, width: int, height: int) -> dict:
    """Basic proportional adaptation: uniform scale (min of both axes) and centre the content on the new canvas; the background still covers the whole canvas.
    This is NOT a smart re-layout (the generator's per-format layout logic needs a concept context); the user is expected to refine it."""
    sw, sh = spec["canvas"]["width"], spec["canvas"]["height"]
    s = min(width / sw, height / sh)
    ox, oy = (width - sw * s) / 2, (height - sh * s) / 2
    out = {**spec, "canvas": {"width": width, "height": height}, "elements": []}
    for e in spec["elements"]:
        n = {**e, "x": round(ox + e["x"] * s, 2), "y": round(oy + e["y"] * s, 2), "w": max(1, round(e["w"] * s, 2)), "h": max(1, round(e["h"] * s, 2))}
        for k, lo, hi in (("font_size", 6, 600), ("min_font_size", 6, 600), ("radius", 0, 2000), ("corner_radius", 0, 2000), ("stroke_width", 0, 100), ("padding_x", 0, 500)):
            if n.get(k) is not None:
                n[k] = min(hi, max(lo, round(n[k] * s, 2)))
        if n.get("letter_spacing"):
            n["letter_spacing"] = round(n["letter_spacing"] * s, 2)
        out["elements"].append(n)
    out["background"] = {**spec["background"], "scrims": [{**sc, **({"x": sc["x"] * s + ox, "y": sc["y"] * s + oy} if sc.get("w") is not None else {}),
                                                          **({"w": sc["w"] * s} if sc.get("w") is not None else {}), **({"h": sc["h"] * s} if sc.get("h") is not None else {})}
                                                         for sc in spec["background"].get("scrims", [])]}
    return CreativeSpec.model_validate(out).model_dump(mode="json")


def duplicate_to_size(db: Session, org_id: uuid.UUID, doc: CreativeDocument, creative: GeneratedCreative, user_id: uuid.UUID, width: int, height: int, storage: Storage) -> CreativeDocument:
    """Create a NEW creative + document for another size from this one. The source document is not modified, and no link remains between them."""
    if not formats.valid_custom(width, height):
        raise C.CommandError("invalid_size", f"Size must be between {formats.MIN_DIM} and {formats.MAX_DIM}px")
    spec = adapt_spec(doc.document, width, height)
    for a in C.referenced_asset_ids(spec):
        _check_assets(db, org_id, {a})
    new = GeneratedCreative(organization_id=org_id, campaign_id=creative.campaign_id, concept_id=creative.concept_id, width=width, height=height, spec=spec, spec_version=1,
                            format_keys=[f.id for f in formats.for_size(width, height)], status="rendered")
    db.add(new)
    db.flush()
    _persist_render(db, storage, org_id, new, render_spec(CreativeSpec.model_validate(spec), make_resolver(db, org_id, storage)))
    nd = get_or_create(db, org_id, new, user_id)
    nd.name = f"{doc.name.rsplit(' ', 1)[0] if doc.name else 'Banner'} {width}x{height}"
    nd.rendered_revision = 0
    db.flush()
    return nd
