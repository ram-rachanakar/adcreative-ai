import uuid

from sqlalchemy import text

from app.core.db import SessionLocal, set_org


def setup_two_orgs(make_client):
    a, b = make_client(), make_client()
    a.register("a@a.example.com", "Org A")
    b.register("b@b.example.com", "Org B")
    return a, b


def test_register_login_me_logout(make_client):
    c = make_client()
    c.register("x@x.example.com")
    assert c.req("get", "/auth/me").json()["email"] == "x@x.example.com"
    assert c.req("post", "/auth/logout").status_code == 204
    assert c.req("get", "/auth/me").status_code == 401


def test_duplicate_email_and_bad_login(make_client):
    c = make_client()
    c.register("x@x.example.com")
    assert make_client().http.post("/auth/register", json={"email": "x@x.example.com", "password": "supersecret123", "organization_name": "O"}).status_code == 409
    r = make_client().http.post("/auth/login", json={"email": "x@x.example.com", "password": "wrong-password"})
    assert r.status_code == 401


def test_weak_password_rejected(make_client):
    r = make_client().http.post("/auth/register", json={"email": "w@w.example.com", "password": "short", "organization_name": "O"})
    assert r.status_code == 422


def test_csrf_required_on_unsafe_methods(make_client):
    c = make_client()
    c.register("x@x.example.com")
    r = c.http.post("/workspaces", json={"name": "W"}, headers={"x-org-id": c.org})  # no CSRF header
    assert r.status_code == 403


def test_login_rate_limited(make_client):
    c = make_client()
    codes = [c.http.post("/auth/login", json={"email": "n@n.example.com", "password": "x" * 12}).status_code for _ in range(12)]
    assert 429 in codes


def _project(c):
    ws = c.req("get", "/workspaces").json()[0]["id"]
    return c.req("post", "/projects", json={"workspace_id": ws, "name": "P"}).json()


def test_tenant_isolation_lists_and_gets(make_client):
    a, b = setup_two_orgs(make_client)
    pa = _project(a)
    ca = a.req("post", "/campaigns", json={"project_id": pa["id"], "name": "Camp A"}).json()
    assert [c["id"] for c in a.req("get", "/campaigns").json()] == [ca["id"]]
    assert b.req("get", "/campaigns").json() == []
    assert b.req("get", f"/campaigns/{ca['id']}").status_code == 404
    assert b.req("get", "/projects").json() == []


def test_cannot_use_other_orgs_parent_resources(make_client):
    a, b = setup_two_orgs(make_client)
    pa = _project(a)
    assert b.req("post", "/campaigns", json={"project_id": pa["id"], "name": "x"}).status_code == 404
    ws_a = a.req("get", "/workspaces").json()[0]["id"]
    assert b.req("post", "/projects", json={"workspace_id": ws_a, "name": "x"}).status_code == 404


def test_forged_org_header_rejected(make_client):
    a, b = setup_two_orgs(make_client)
    assert a.req("get", "/campaigns", headers={"x-org-id": b.org}).status_code == 403
    assert a.req("get", "/campaigns", headers={"x-org-id": str(uuid.uuid4())}).status_code == 403
    assert a.req("get", "/campaigns", headers={"x-org-id": "not-a-uuid"}).status_code == 400


def test_role_enforcement(make_client):
    owner = make_client()
    owner.register("o@o.example.com", "Org")
    viewer = make_client()
    viewer.register("v@v.example.com", "Viewer Own Org")
    assert owner.req("post", "/members", json={"email": "v@v.example.com", "role": "viewer"}).status_code == 201
    viewer.org = owner.org  # now acts as a viewer inside owner's org
    ws = viewer.req("get", "/workspaces").json()[0]["id"]
    assert viewer.req("get", "/projects").status_code == 200
    assert viewer.req("post", "/projects", json={"workspace_id": ws, "name": "x"}).status_code == 403
    assert viewer.req("post", "/workspaces", json={"name": "x"}).status_code == 403
    assert viewer.req("post", "/members", json={"email": "o@o.example.com", "role": "admin"}).status_code == 403


def test_dashboard_counts_only_own_org(make_client):
    a, b = setup_two_orgs(make_client)
    pa = _project(a)
    a.req("post", "/campaigns", json={"project_id": pa["id"], "name": "c"})
    assert a.req("get", "/dashboard").json()["campaigns"] == 1
    assert b.req("get", "/dashboard").json()["campaigns"] == 0


def test_rls_blocks_cross_tenant_rows_even_with_raw_sql(make_client):
    a, b = setup_two_orgs(make_client)
    _project(a)
    with SessionLocal() as db:
        assert db.execute(text("select count(*) from projects")).scalar() == 0  # no org context -> nothing
        set_org(db, uuid.UUID(b.org))
        assert db.execute(text("select count(*) from projects")).scalar() == 0  # wrong org -> nothing
        set_org(db, uuid.UUID(a.org))
        assert db.execute(text("select count(*) from projects")).scalar() == 1
