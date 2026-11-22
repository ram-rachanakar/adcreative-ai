import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import SESSION_COOKIE, current_session, current_user
from app.core.config import get_settings
from app.core.db import get_db, set_org
from app.core.security import hash_password, hash_token, new_token, verify_password
from app.models import AuthSession, Membership, Organization, Role, User, Workspace
from app.schemas.api import LoginIn, MeOut, MembershipOut, OrgOut, RegisterIn

router = APIRouter(prefix="/auth", tags=["auth"])
_attempts: dict[str, list[float]] = defaultdict(list)


def _rate_limit(key: str, limit: int = 10, window: int = 60) -> None:
    now = time.time()
    _attempts[key] = [t for t in _attempts[key] if now - t < window]
    if len(_attempts[key]) >= limit:
        raise HTTPException(429, "Too many attempts, try again later")
    _attempts[key].append(now)


def _start_session(db: Session, user: User, response: Response) -> AuthSession:
    s = get_settings()
    token = new_token()
    sess = AuthSession(
        user_id=user.id, token_hash=hash_token(token), csrf_token=new_token(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=s.session_ttl_hours),
    )
    db.add(sess)
    db.commit()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=s.cookie_secure,
                        max_age=s.session_ttl_hours * 3600, path="/")
    return sess


def _me(db: Session, user: User, sess: AuthSession) -> MeOut:
    rows = db.execute(
        select(Membership, Organization).join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == user.id)
    ).all()
    return MeOut(id=user.id, email=user.email, name=user.name, csrf_token=sess.csrf_token,
                 memberships=[MembershipOut(organization=OrgOut.model_validate(o), role=m.role) for m, o in rows])


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "org"


def create_org(db: Session, name: str, owner: User) -> Organization:
    base, n = slugify(name), 0
    while True:
        slug = base if n == 0 else f"{base}-{n}"
        if not db.scalar(select(Organization.id).where(Organization.slug == slug)):
            break
        n += 1
    org = Organization(name=name, slug=slug)
    db.add(org)
    db.flush()
    db.add(Membership(organization_id=org.id, user_id=owner.id, role=Role.owner))
    set_org(db, org.id)
    db.add(Workspace(organization_id=org.id, name="Default workspace"))
    db.flush()
    return org


@router.post("/register", response_model=MeOut, status_code=201)
def register(body: RegisterIn, response: Response, request: Request, db: Session = Depends(get_db)):
    _rate_limit(f"reg:{request.client.host if request.client else ''}")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password), name=body.name)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email already registered")
    create_org(db, body.organization_name, user)
    sess = _start_session(db, user, response)
    return _me(db, user, sess)


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, response: Response, request: Request, db: Session = Depends(get_db)):
    _rate_limit(f"login:{body.email.lower()}")
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    sess = _start_session(db, user, response)
    return _me(db, user, sess)


@router.post("/logout", status_code=204)
def logout(response: Response, sess: AuthSession = Depends(current_session), db: Session = Depends(get_db)):
    db.delete(sess)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user), sess: AuthSession = Depends(current_session), db: Session = Depends(get_db)):
    return _me(db, user, sess)
