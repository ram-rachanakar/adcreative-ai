import re
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, org_context, require_role
from app.api.jobs import _job_out
from app.core.db import get_db
from app.marketing.service import Conflict
from app.orchestration import textjobs
from app.workers import tasks
from app.models import BrandProfile, Campaign, Product, ProductAsset, Role
from app.repositories.org_repo import OrgRepo
from app.schemas.api import AssetOut, BrandIn, BrandOut, BrandPatch, ProductEdit, ProductOut
from app.scraping import service
from app.scraping.fetcher import FetchError, UnsafeURLError
from app.storage.base import Storage, get_storage, verify_signature

router = APIRouter(tags=["product-intelligence"])
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _campaign(db: Session, ctx: OrgContext, cid: uuid.UUID) -> Campaign:
    c = OrgRepo(db, Campaign, ctx.org_id).get(cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


def _product(db: Session, ctx: OrgContext, cid: uuid.UUID) -> Product:
    _campaign(db, ctx, cid)
    p = OrgRepo(db, Product, ctx.org_id).list(campaign_id=cid)
    if not p:
        raise HTTPException(404, "Product not analyzed yet")
    return p[0]


def _out(db: Session, ctx: OrgContext, p: Product, storage: Storage) -> ProductOut:
    assets = OrgRepo(db, ProductAsset, ctx.org_id).list(product_id=p.id)
    return ProductOut(id=p.id, campaign_id=p.campaign_id, source_url=p.source_url, data=p.data,
                      extraction_meta=p.extraction_meta, user_edited=p.user_edited,
                      assets=[AssetOut(id=a.id, kind=a.kind, mime=a.mime, width=a.width, height=a.height, origin=a.origin,
                                       source_url=a.source_url, url=storage.signed_url(a.storage_key)) for a in reversed(assets)])


@router.post("/campaigns/{cid}/analyze", status_code=202)
def analyze(cid: uuid.UUID, ctx: OrgContext = Depends(require_role(Role.marketer)), db: Session = Depends(get_db)):
    """Queues the analysis (fetching + extraction run in the Celery worker). Poll GET /jobs/{id}, then GET /campaigns/{cid}/product."""
    c = _campaign(db, ctx, cid)
    if not c.product_url:
        raise HTTPException(422, "Campaign has no product URL")
    try:
        job = textjobs.create(db, ctx.org_id, c, "analyze")
    except Conflict as e:
        raise HTTPException(409, str(e))
    db.commit()  # the worker must be able to see the job
    tasks.run_text_job.delay(str(ctx.org_id), str(job.id))
    db.refresh(job)
    return JSONResponse(_job_out(job), status_code=202)


@router.get("/campaigns/{cid}/product", response_model=ProductOut)
def get_product(cid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    return _out(db, ctx, _product(db, ctx, cid), storage)


@router.patch("/campaigns/{cid}/product", response_model=ProductOut)
def edit_product(cid: uuid.UUID, body: ProductEdit, ctx: OrgContext = Depends(require_role(Role.marketer)),
                 db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    p = _product(db, ctx, cid)
    p.data = {**p.data, **body.model_dump(exclude_unset=True)}
    p.user_edited = True
    db.commit()
    return _out(db, ctx, p, storage)


@router.post("/campaigns/{cid}/product/assets", response_model=ProductOut, status_code=201)
async def upload_asset(cid: uuid.UUID, kind: str = Query("image", pattern="^(image|logo)$"), file: UploadFile = File(...),
                       ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db),
                       storage: Storage = Depends(get_storage)):
    p = _product(db, ctx, cid)
    data = await file.read(service.MAX_IMAGE + 1)
    try:
        if service.store_asset(db, storage, ctx.org_id, p, data, kind, None, "upload") is None:
            raise HTTPException(409, "This image was already added")
    except service.ImageRejected as e:
        raise HTTPException(422, str(e))
    db.commit()
    return _out(db, ctx, p, storage)


@router.delete("/campaigns/{cid}/product/assets/{aid}", status_code=204)
def delete_asset(cid: uuid.UUID, aid: uuid.UUID, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    p = _product(db, ctx, cid)
    repo = OrgRepo(db, ProductAsset, ctx.org_id)
    a = repo.get(aid)
    if not a or a.product_id != p.id:
        raise HTTPException(404, "Asset not found")
    repo.soft_delete(a)
    db.commit()


@router.get("/files/{key:path}", include_in_schema=False)
def serve_file(key: str, exp: int, sig: str, storage: Storage = Depends(get_storage)):
    if not verify_signature(key, exp, sig):
        raise HTTPException(403, "Invalid or expired link")
    try:
        data = storage.get(key)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Not found")
    mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(key.rsplit(".", 1)[-1], "application/octet-stream")
    return Response(data, media_type=mime, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"})


# ---- Brand profiles -------------------------------------------------------------------------------------------
def _brand_out(b: BrandProfile) -> BrandOut:
    out = BrandOut.model_validate(b)
    out.effective = {**b.detected, **b.overrides}  # user overrides win over detection
    return out


def _check_overrides(o: dict) -> None:
    allowed = {"colors", "fonts", "tone", "personality", "logo_asset_id", "background_preferences", "image_treatment", "name"}
    bad = set(o) - allowed
    if bad:
        raise HTTPException(422, f"Unknown override keys: {sorted(bad)}")
    if "colors" in o and not (isinstance(o["colors"], list) and all(isinstance(c, str) and HEX.match(c) for c in o["colors"])):
        raise HTTPException(422, "colors must be a list of #RRGGBB values")


@router.get("/brand-profiles", response_model=list[BrandOut])
def list_brands(ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return [_brand_out(b) for b in OrgRepo(db, BrandProfile, ctx.org_id).list()]


@router.post("/brand-profiles", response_model=BrandOut, status_code=201)
def create_brand(body: BrandIn, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    _check_overrides(body.overrides)
    b = OrgRepo(db, BrandProfile, ctx.org_id).add(name=body.name, overrides=body.overrides, guidelines=body.guidelines, detected={})
    db.commit()
    return _brand_out(b)


@router.patch("/brand-profiles/{bid}", response_model=BrandOut)
def patch_brand(bid: uuid.UUID, body: BrandPatch, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db)):
    b = OrgRepo(db, BrandProfile, ctx.org_id).get(bid)
    if not b:
        raise HTTPException(404, "Brand profile not found")
    if body.overrides is not None:
        _check_overrides(body.overrides)
        b.overrides = body.overrides
    if body.name is not None:
        b.name = body.name
    if body.guidelines is not None:
        b.guidelines = body.guidelines
    db.commit()
    return _brand_out(b)


@router.delete("/brand-profiles/{bid}", status_code=204)
def delete_brand(bid: uuid.UUID, ctx: OrgContext = Depends(require_role(Role.admin)), db: Session = Depends(get_db)):
    repo = OrgRepo(db, BrandProfile, ctx.org_id)
    b = repo.get(bid)
    if not b:
        raise HTTPException(404, "Brand profile not found")
    repo.soft_delete(b)
    db.commit()
