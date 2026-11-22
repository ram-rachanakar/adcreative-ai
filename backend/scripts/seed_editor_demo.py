"""Creates a user + campaign with real generated banners (mock AI providers, local storage) for trying the editor and for the browser e2e test.
Usage: CELERY_EAGER=true .venv/bin/python scripts/seed_editor_demo.py   -> prints JSON {email,password,campaign_id,creatives:{"300x250":id,...}}"""
import io
import json
import os
import sys
import uuid

os.environ.setdefault("CELERY_EAGER", "true")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.core.db import SessionLocal, set_org
from app.main import app
from app.models import Product
from app.scraping.service import store_asset
from app.storage.base import get_storage


def png(size, draw):
    im = Image.new("RGBA", size, (250, 250, 250, 255)); draw(ImageDraw.Draw(im)); b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def main():
    c = TestClient(app); email = f"demo-{uuid.uuid4().hex[:6]}@example.com"; pw = "demopassword123"
    r = c.post("/auth/register", json={"email": email, "password": pw, "organization_name": "Demo Org"}); r.raise_for_status()
    csrf, org = r.json()["csrf_token"], r.json()["memberships"][0]["organization"]["id"]
    H = {"x-csrf-token": csrf, "x-org-id": org}
    ws = c.get("/workspaces", headers=H).json()[0]["id"]
    pid = c.post("/projects", json={"workspace_id": ws, "name": "Demo"}, headers=H).json()["id"]
    cid = c.post("/campaigns", json={"project_id": pid, "name": "AeroBottle launch", "brief": {"audience": "hikers", "tone": "bold"}}, headers=H).json()["id"]
    storage = get_storage()
    with SessionLocal() as db:
        set_org(db, uuid.UUID(org))
        p = Product(organization_id=uuid.UUID(org), campaign_id=uuid.UUID(cid), data={"title": "AeroBottle", "description": "Keeps drinks cold. Holds 750 ml.", "features": ["Keeps drinks cold", "Dishwasher safe", "Leak-proof lid"], "specifications": {}, "variants": []})
        db.add(p); db.flush()
        store_asset(db, storage, uuid.UUID(org), p, png((400, 500), lambda d: d.rounded_rectangle((120, 60, 280, 440), 30, fill=(20, 80, 200, 255))), "image", None, "upload")
        store_asset(db, storage, uuid.UUID(org), p, png((200, 80), lambda d: d.rectangle((0, 0, 199, 79), fill=(10, 160, 90, 255))), "logo", None, "upload")
        store_asset(db, storage, uuid.UUID(org), p, png((500, 400), lambda d: d.ellipse((50, 40, 450, 360), fill=(200, 60, 40, 255))), "image", None, "upload")
        db.commit()
    concepts = c.post(f"/campaigns/{cid}/concepts/generate", json={}, headers=H).json()
    c.post(f"/campaigns/{cid}/concepts/select", json={"concept_ids": [concepts[0]["id"]]}, headers=H)
    c.post(f"/campaigns/{cid}/briefs/generate", headers=H)
    g = c.post(f"/campaigns/{cid}/creatives/generate", json={"sizes": [{"width": 300, "height": 250}, {"width": 1080, "height": 1080}]}, headers=H)
    assert g.status_code == 202, g.text
    items = c.get(f"/campaigns/{cid}/creatives", headers=H).json()
    print(json.dumps({"email": email, "password": pw, "campaign_id": cid, "org_id": org, "creatives": {f"{x['width']}x{x['height']}": x["id"] for x in items}}))


if __name__ == "__main__":
    main()
