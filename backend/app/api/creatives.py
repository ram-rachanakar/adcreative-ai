import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, org_context, require_role
from app.core.db import get_db
from app.creative import service
from app.models import Campaign, CreativeAsset, GeneratedCreative, Role
from app.rendering.spec import CreativeSpec
from app.repositories.org_repo import OrgRepo
from app.orchestration import factories
from app.storage.base import Storage, get_storage

router = APIRouter(tags=["creative-generation"])


def _out(db: Session, ctx: OrgContext, c: GeneratedCreative, storage: Storage, detail: bool = False) -> dict:
    a = OrgRepo(db, CreativeAsset, ctx.org_id).get(c.render_asset_id) if c.render_asset_id else None
    d = {"id": str(c.id), "campaign_id": str(c.campaign_id), "concept_id": str(c.concept_id), "width": c.width, "height": c.height,
         "status": c.status, "spec_version": c.spec_version, "favorite": c.favorite, "format_keys": c.format_keys or [], "job_id": str(c.job_id) if c.job_id else None, "render_report": c.render_report,
         "quality": {"score": c.quality_score, "status": c.quality_status, "stale": c.quality_score is not None and c.quality_spec_version != c.spec_version},
         "url": storage.signed_url(a.storage_key) if a else None}
    if detail:
        d["spec"] = c.spec
    return d


def _campaign(db, ctx, cid):
    c = OrgRepo(db, Campaign, ctx.org_id).get(cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


@router.get("/campaigns/{cid}/creatives")
def list_creatives(cid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    _campaign(db, ctx, cid)
    return [_out(db, ctx, c, storage) for c in reversed(OrgRepo(db, GeneratedCreative, ctx.org_id).list(campaign_id=cid))]


@router.get("/creatives/{crid}")
def get_creative(crid: uuid.UUID, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    c = OrgRepo(db, GeneratedCreative, ctx.org_id).get(crid)
    if not c:
        raise HTTPException(404, "Creative not found")
    return _out(db, ctx, c, storage, detail=True)


@router.put("/creatives/{crid}/spec")
def update_spec(crid: uuid.UUID, spec: CreativeSpec, ctx: OrgContext = Depends(require_role(Role.designer)), db: Session = Depends(get_db),
                storage: Storage = Depends(get_storage)):
    c = OrgRepo(db, GeneratedCreative, ctx.org_id).get(crid)
    if not c:
        raise HTTPException(404, "Creative not found")
    try:
        service.rerender(db, ctx.org_id, c, spec, storage)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.commit()
    return _out(db, ctx, c, storage, detail=True)


class FavIn(BaseModel):
    favorite: bool


@router.patch("/creatives/{crid}")
def set_favorite(crid: uuid.UUID, body: FavIn, ctx: OrgContext = Depends(require_role(Role.marketer)), db: Session = Depends(get_db), storage: Storage = Depends(get_storage)):
    c = OrgRepo(db, GeneratedCreative, ctx.org_id).get(crid)
    if not c:
        raise HTTPException(404, "Creative not found")
    c.favorite = body.favorite
    db.commit()
    return _out(db, ctx, c, storage)
