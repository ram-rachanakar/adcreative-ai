import os

OWNER_URL = "postgresql+psycopg://adcreative:adcreative@localhost:5433/adcreative_test"
os.environ["DATABASE_URL"] = "postgresql+psycopg://adcreative_app:adcreative_app@localhost:5433/adcreative_test"
os.environ["MIGRATION_DATABASE_URL"] = OWNER_URL
os.environ["CELERY_EAGER"] = "true"   # tasks run inline; a real broker/worker is exercised separately

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

owner_engine = create_engine(OWNER_URL)
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _schema():
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean():
    with owner_engine.begin() as c:
        c.execute(text("truncate organizations, users cascade"))
    from app.api import auth
    auth._attempts.clear()
    yield


class Client:
    """Cookie-aware API client that mirrors the browser (sends CSRF header)."""
    def __init__(self):
        self.http = TestClient(app)
        self.csrf = ""
        self.org = None

    def register(self, email, org="Org", password="supersecret123"):
        r = self.http.post("/auth/register", json={"email": email, "password": password, "organization_name": org})
        assert r.status_code == 201, r.text
        self.csrf = r.json()["csrf_token"]
        self.org = r.json()["memberships"][0]["organization"]["id"]
        return r.json()

    def req(self, method, url, **kw):
        h = {"x-csrf-token": self.csrf, **kw.pop("headers", {})}
        if self.org and "x-org-id" not in h:
            h["x-org-id"] = self.org
        return self.http.request(method, url, headers=h, **kw)

    def sync(self, method, url, **kw):
        """For endpoints that queue a job (analyze / concepts / briefs). Under CELERY_EAGER the job has already run, so this
        mimics the old synchronous contract: returns the job's result, or a response carrying the status the job failed with."""
        r = self.req(method, url, **kw)
        if r.status_code != 202:
            return r
        job = r.json()
        job = self.req("get", f"/jobs/{job['id']}").json()
        if job["status"] != "completed":
            return _Resp(job["error_status"] or 500, {"detail": job["error"]})
        cid = job["campaign_id"]
        if job["kind"] == "analyze":
            return self.req("get", f"/campaigns/{cid}/product")
        if job["kind"] == "concepts":
            ids = {x["concept_id"] for x in job["results"]}
            return _Resp(200, [c for c in self.req("get", f"/campaigns/{cid}/concepts").json() if c["id"] in ids])
        return _Resp(200, job["results"])


class _Resp:
    def __init__(self, status_code, body):
        self.status_code, self._body, self.text = status_code, body, str(body)

    def json(self):
        return self._body


@pytest.fixture
def make_client():
    return Client
