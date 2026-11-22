"""Idempotent dev seed: two orgs (for isolation testing) with one user per role."""
from sqlalchemy import select

from app.api.auth import create_org
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import Membership, Role, User

PASSWORD = "devpassword123"


def seed() -> None:
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == "owner@acme.example.com")):
            return
        for org_name, slug in (("Acme Co", "acme"), ("Globex", "globex")):
            owner = User(email=f"owner@{slug}.example.com", password_hash=hash_password(PASSWORD), name=f"{org_name} Owner")
            db.add(owner)
            db.flush()
            org = create_org(db, org_name, owner)
            for role in (Role.admin, Role.designer, Role.marketer, Role.viewer):
                u = User(email=f"{role.value}@{slug}.example.com", password_hash=hash_password(PASSWORD), name=role.value.title())
                db.add(u)
                db.flush()
                db.add(Membership(organization_id=org.id, user_id=u.id, role=role))
        db.commit()
        print("Seeded dev data (password: %s)" % PASSWORD)


if __name__ == "__main__":
    seed()
