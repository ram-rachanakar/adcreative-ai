import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db, set_org
from app.core.security import hash_token, safe_equal
from app.models import AuthSession, Membership, Role, User
from app.models.core import ROLE_RANK

SESSION_COOKIE = "session"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class OrgContext:
    user: User
    org_id: uuid.UUID
    role: Role

    def require(self, minimum: Role) -> None:
        if ROLE_RANK[self.role] < ROLE_RANK[minimum]:
            raise HTTPException(403, "Insufficient role")


def current_session(request: Request, db: Session = Depends(get_db), session: str | None = Cookie(default=None)) -> AuthSession:
    if not session:
        raise HTTPException(401, "Not authenticated")
    s = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(session)))
    if not s or s.expires_at < datetime.now(timezone.utc):
        raise HTTPException(401, "Session expired")
    if request.method in UNSAFE:  # CSRF: header must match per-session token
        sent = request.headers.get("x-csrf-token", "")
        if not safe_equal(sent, s.csrf_token):
            raise HTTPException(403, "CSRF token missing or invalid")
    return s


def current_user(s: AuthSession = Depends(current_session), db: Session = Depends(get_db)) -> User:
    user = db.get(User, s.user_id)
    if not user:
        raise HTTPException(401, "Unknown user")
    return user


def org_context(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    x_org_id: str | None = Header(default=None),
) -> OrgContext:
    """X-Org-Id only *selects* an org; membership is always verified server-side."""
    q = select(Membership).where(Membership.user_id == user.id)
    if x_org_id:
        try:
            q = q.where(Membership.organization_id == uuid.UUID(x_org_id))
        except ValueError:
            raise HTTPException(400, "Invalid organization id")
    m = db.scalars(q.order_by(Membership.created_at)).first()
    if not m:
        raise HTTPException(403, "Not a member of this organization")
    set_org(db, m.organization_id)
    return OrgContext(user=user, org_id=m.organization_id, role=m.role)


def require_role(minimum: Role):
    def dep(ctx: OrgContext = Depends(org_context)) -> OrgContext:
        ctx.require(minimum)
        return ctx
    return dep
