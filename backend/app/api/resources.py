from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, org_context, require_role
from app.core.db import get_db
from app.models import AuditLog, Campaign, Membership, Project, Role, User, Workspace
from app.repositories.org_repo import OrgRepo
from app.schemas.api import *  # noqa: F403

router = APIRouter(tags=["resources"])


def _audit(db: Session, ctx: OrgContext, action: str, resource: str, **detail) -> None:
    OrgRepo(db, AuditLog, ctx.org_id).add(user_id=ctx.user.id, action=action, resource=resource, detail=detail)


@router.get("/workspaces", response_model=list[WorkspaceOut])  # noqa: F405
def list_workspaces(ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return OrgRepo(db, Workspace, ctx.org_id).list()


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)  # noqa: F405
def create_workspace(body: WorkspaceIn, ctx: OrgContext = Depends(require_role(Role.admin)), db: Session = Depends(get_db)):  # noqa: F405
    w = OrgRepo(db, Workspace, ctx.org_id).add(name=body.name)
    _audit(db, ctx, "workspace.create", str(w.id))
    db.commit()
    return w


@router.get("/projects", response_model=list[ProjectOut])  # noqa: F405
def list_projects(ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return OrgRepo(db, Project, ctx.org_id).list()


@router.post("/projects", response_model=ProjectOut, status_code=201)  # noqa: F405
def create_project(body: ProjectIn, ctx: OrgContext = Depends(require_role(Role.marketer)), db: Session = Depends(get_db)):  # noqa: F405
    if not OrgRepo(db, Workspace, ctx.org_id).get(body.workspace_id):
        raise HTTPException(404, "Workspace not found")  # also covers other orgs' workspaces
    p = OrgRepo(db, Project, ctx.org_id).add(**body.model_dump())
    _audit(db, ctx, "project.create", str(p.id))
    db.commit()
    return p


@router.get("/campaigns", response_model=list[CampaignOut])  # noqa: F405
def list_campaigns(ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    return OrgRepo(db, Campaign, ctx.org_id).list()


@router.post("/campaigns", response_model=CampaignOut, status_code=201)  # noqa: F405
def create_campaign(body: CampaignIn, ctx: OrgContext = Depends(require_role(Role.marketer)), db: Session = Depends(get_db)):  # noqa: F405
    if not OrgRepo(db, Project, ctx.org_id).get(body.project_id):
        raise HTTPException(404, "Project not found")
    c = OrgRepo(db, Campaign, ctx.org_id).add(**body.model_dump())
    _audit(db, ctx, "campaign.create", str(c.id))
    db.commit()
    return c


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)  # noqa: F405
def get_campaign(campaign_id: str, ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    import uuid
    try:
        cid = uuid.UUID(campaign_id)
    except ValueError:
        raise HTTPException(404, "Campaign not found")
    c = OrgRepo(db, Campaign, ctx.org_id).get(cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


@router.get("/dashboard")
def dashboard(ctx: OrgContext = Depends(org_context), db: Session = Depends(get_db)):
    def count(model):
        return db.scalar(select(func.count()).select_from(model).where(
            model.organization_id == ctx.org_id, model.deleted_at.is_(None)))
    return {
        "campaigns": count(Campaign), "projects": count(Project),
        "generated_creatives": 0,  # populated from M4 onward
        "recent_campaigns": [CampaignOut.model_validate(c).model_dump(mode="json")  # noqa: F405
                             for c in OrgRepo(db, Campaign, ctx.org_id).list()[:5]],
    }


@router.post("/members", status_code=201)
def add_member(body: AddMemberIn, ctx: OrgContext = Depends(require_role(Role.admin)), db: Session = Depends(get_db)):  # noqa: F405
    if body.role == Role.owner and ctx.role != Role.owner:
        raise HTTPException(403, "Only owners can grant owner")
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user:
        raise HTTPException(404, "User not found")
    if db.scalar(select(Membership.id).where(Membership.user_id == user.id, Membership.organization_id == ctx.org_id)):
        raise HTTPException(409, "Already a member")
    db.add(Membership(organization_id=ctx.org_id, user_id=user.id, role=body.role))
    _audit(db, ctx, "member.add", str(user.id), role=body.role.value)
    db.commit()
    return {"ok": True}
