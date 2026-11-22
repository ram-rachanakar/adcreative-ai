import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.ai.mock_image import MockImageProvider
from app.core.db import SessionLocal, set_org
from app.orchestration import factories
from conftest import owner_engine
from test_creative_api import env, fetch_png, gen, prepared  # noqa: F401  (env is autouse storage fixture)

SIZES = [{"width": 300, "height": 250}, {"width": 1080, "height": 1080}]


@pytest.fixture
def setup(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    creatives = gen(c, cid, sizes=SIZES).json()
    by = {(x["width"], x["height"]): x for x in creatives}
    assert set(by) == {(300, 250), (1080, 1080)}
    return c, cid, by


def open_doc(c, cr):
    r = c.req("post", f"/creatives/{cr['id']}/document")
    assert r.status_code == 200, r.text
    return r.json()


def ops(c, doc, commands, rev=None, **kw):
    return c.req("post", f"/creative-documents/{doc['id']}/operations", json={"expected_revision": doc["revision"] if rev is None else rev, "commands": commands, **kw})


def cmd(t, **payload):
    return {"type": t, "payload": payload}


def layer(doc, role):
    return next(l for l in doc["layers"] if l.get("role") == role)


def test_open_document_structure_and_idempotence(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    assert d["schema_version"] == 2 and d["revision"] == 0 and d["canvas"] == {"width": 300, "height": 250}
    assert [l["z_index"] for l in d["layers"]] == list(range(1, len(d["layers"]) + 1))
    assert {l["type"] for l in d["layers"]} >= {"text", "image", "button"}
    assert d["background"]["layer_id"] == "background" and d["fonts"]
    assert all(a.get("url") for a in d["assets"].values())
    assert open_doc(c, by[(300, 250)])["id"] == d["id"]


def test_edits_are_validated_logged_and_write_through(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    head = layer(d, "headline")
    r = ops(c, d, [cmd("UPDATE_TEXT", layer_id=head["id"], text="Brand new headline"), cmd("CHANGE_COLOR", layer_id=head["id"], field="color", value="#ff0000")])
    assert r.status_code == 200, r.text
    nd = r.json()["document"]
    assert nd["revision"] == 2 and layer(nd, "headline")["text"] == "Brand new headline" and layer(nd, "headline")["color"] == "#ff0000"
    spec = c.req("get", f"/creatives/{by[(300, 250)]['id']}").json()["spec"]       # M3-M6 readers see the edit: one source of truth
    assert spec["elements"][[e["id"] for e in spec["elements"]].index(head["id"])]["text"] == "Brand new headline"
    h = c.req("get", f"/creative-documents/{d['id']}/history").json()
    assert [o["type"] for o in h["operations"]][:2] == ["CHANGE_COLOR", "UPDATE_TEXT"] and h["can_undo"] and not h["can_redo"]


def test_invalid_commands_rejected_and_nothing_written(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    head = layer(d, "headline")
    cases = [(cmd("UPDATE_STYLE", layer_id=head["id"], changes={"onclick": "alert(1)"}), "invalid_document"),
             (cmd("UPDATE_STYLE", layer_id=head["id"], changes={"font_family": "Papyrus"}), "invalid_document"),
             (cmd("CHANGE_COLOR", layer_id=head["id"], field="color", value="url(javascript:x)"), "invalid_document"),
             (cmd("UPDATE_STYLE", layer_id=head["id"], changes={"opacity": 7}), "invalid_document"),
             (cmd("MOVE_ELEMENT", layer_id=head["id"], x=5000, y=5000), "out_of_canvas"),
             (cmd("NOPE"), "unknown_command"), (cmd("UPDATE_TEXT", layer_id="ghost", text="x"), "layer_not_found"),
             (cmd("REPLACE_IMAGE", layer_id=layer(d, "product")["id"], asset_id=str(uuid.uuid4())), "asset_not_found")]
    for command, code in cases:
        r = ops(c, d, [cmd("UPDATE_TEXT", layer_id=head["id"], text="should not persist"), command])
        assert r.status_code in (404, 422), (command, r.text)
        assert r.json()["error"]["code"] == code and r.json()["error"]["request_id"]
    assert c.req("get", f"/creative-documents/{d['id']}").json()["revision"] == 0        # batches are all-or-nothing


def test_locked_layers_cannot_be_edited_until_unlocked(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    lid = layer(d, "headline")["id"]
    d = ops(c, d, [cmd("TOGGLE_LOCK", layer_id=lid, locked=True)]).json()["document"]
    r = ops(c, d, [cmd("UPDATE_TEXT", layer_id=lid, text="x")])
    assert r.status_code == 423 and r.json()["error"]["code"] == "layer_locked"
    d = ops(c, d, [cmd("TOGGLE_LOCK", layer_id=lid, locked=False)]).json()["document"]
    assert ops(c, d, [cmd("UPDATE_TEXT", layer_id=lid, text="x")]).status_code == 200


def test_every_command_type_works(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    head, prod, btn = layer(d, "headline"), layer(d, "product"), layer(d, "cta")
    steps = [cmd("MOVE_ELEMENT", layer_id=prod["id"], x=20, y=20), cmd("RESIZE_ELEMENT", layer_id=prod["id"], w=90, h=90), cmd("ROTATE_ELEMENT", layer_id=prod["id"], rotation=15),
             cmd("CROP_IMAGE", layer_id=prod["id"], crop={"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}), cmd("UPDATE_STYLE", layer_id=prod["id"], changes={"fit": "cover", "shadow": "drop", "opacity": 0.9}),
             cmd("UPDATE_TEXT", layer_id=btn["id"], text="Buy now"), cmd("UPDATE_STYLE", layer_id=head["id"], changes={"font_weight": 700, "align": "center", "letter_spacing": 1, "line_height": 1.3}),
             cmd("CHANGE_COLOR", layer_id=btn["id"], field="fill", value="#112233"), cmd("CHANGE_COLOR", layer_id="background", field="color", value="#eeeeee"),
             cmd("CHANGE_BACKGROUND", background={"type": "gradient", "color": "#000000", "gradient_to": "#333333", "angle": 90}),
             cmd("REORDER_LAYER", layer_id=head["id"], direction="back"), cmd("REORDER_LAYER", layer_id=head["id"], to_index=2),
             cmd("DUPLICATE_ELEMENT", layer_id=head["id"], new_id="dup1"), cmd("TOGGLE_VISIBILITY", layer_id="dup1"),
             cmd("ADD_LAYER", layer={"type": "shape", "id": "box", "x": 10, "y": 10, "w": 40, "h": 40, "fill": "#abcdef", "radius": 6}),
             cmd("ALIGN_ELEMENT", layer_ids=["box"], edge="center"), cmd("DISTRIBUTE_ELEMENTS", layer_ids=["box", "dup1", prod["id"]], axis="horizontal"),
             cmd("DELETE_ELEMENT", layer_id="box")]
    for s in steps:
        r = ops(c, d, [s])
        assert r.status_code == 200, (s, r.text)
        d = r.json()["document"]
    assert d["revision"] == len(steps)
    assert not next(l for l in d["layers"] if l["id"] == "dup1")["visible"] and all(l["id"] != "box" for l in d["layers"])
    assert d["background"]["type"] == "gradient" and layer(d, "product")["rotation"] == 15
    # still renders and preflights through the deterministic renderer
    assert c.req("get", f"/creative-documents/{d['id']}/preflight").status_code == 200


def test_undo_redo_survive_reload_and_branch_invalidates_redo(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    head = layer(d, "headline")
    orig = head["text"]
    d = ops(c, d, [cmd("UPDATE_TEXT", layer_id=head["id"], text="One")]).json()["document"]
    d = ops(c, d, [cmd("UPDATE_TEXT", layer_id=head["id"], text="Two")]).json()["document"]
    u = c.req("post", f"/creative-documents/{d['id']}/undo", json={"expected_revision": d["revision"]})
    assert u.status_code == 200 and layer(u.json()["document"], "headline")["text"] == "One"
    h = c.req("get", f"/creative-documents/{d['id']}/history").json()
    assert h["can_undo"] and h["can_redo"]
    rd = c.req("post", f"/creative-documents/{d['id']}/redo")
    assert layer(rd.json()["document"], "headline")["text"] == "Two"
    c.req("post", f"/creative-documents/{d['id']}/undo"); c.req("post", f"/creative-documents/{d['id']}/undo")
    cur = c.req("get", f"/creative-documents/{d['id']}").json()
    assert layer(cur, "headline")["text"] == orig
    cur = ops(c, cur, [cmd("UPDATE_TEXT", layer_id=head["id"], text="Branch")]).json()["document"]     # new edit after undo kills redo
    assert not c.req("get", f"/creative-documents/{d['id']}/history").json()["can_redo"]
    r = c.req("post", f"/creative-documents/{d['id']}/redo")
    assert r.status_code == 409 and r.json()["error"]["code"] == "nothing_to_redo"
    # undo of a batch is one step
    cur = ops(c, cur, [cmd("MOVE_ELEMENT", layer_id=head["id"], x=11, y=11), cmd("ROTATE_ELEMENT", layer_id=head["id"], rotation=5)]).json()["document"]
    back = c.req("post", f"/creative-documents/{d['id']}/undo").json()["document"]
    assert layer(back, "headline")["rotation"] == 0 and layer(back, "headline")["x"] != 11


def test_undo_restores_deleted_layer_and_order_and_asset_reference(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    prod = layer(d, "product")
    order = [l["id"] for l in d["layers"]]
    d = ops(c, d, [cmd("DELETE_ELEMENT", layer_id=prod["id"])]).json()["document"]
    assert prod["id"] not in [l["id"] for l in d["layers"]]
    d = c.req("post", f"/creative-documents/{d['id']}/undo").json()["document"]
    assert [l["id"] for l in d["layers"]] == order and layer(d, "product")["asset_id"] == prod["asset_id"]


def test_revision_conflict_never_overwrites_and_returns_latest(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    head = layer(d, "headline")
    first = ops(c, d, [cmd("UPDATE_TEXT", layer_id=head["id"], text="Newest")])
    assert first.status_code == 200
    stale = ops(c, d, [cmd("UPDATE_TEXT", layer_id=head["id"], text="Stale in-flight save")])      # still based on revision 0
    assert stale.status_code == 409
    body = stale.json()
    assert body["error"]["code"] == "revision_conflict" and body["error"]["current_revision"] == 1 and layer(body["document"], "headline")["text"] == "Newest"
    assert layer(c.req("get", f"/creative-documents/{d['id']}").json(), "headline")["text"] == "Newest"


def test_retry_with_same_batch_id_is_idempotent(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    head = layer(d, "headline")
    body = [cmd("UPDATE_TEXT", layer_id=head["id"], text="Once")]
    a = ops(c, d, body, batch_id="batch-abcdef01")
    b = ops(c, d, body, batch_id="batch-abcdef01")      # response was "lost": client retries with the old expected revision
    assert a.status_code == b.status_code == 200 and b.json()["duplicate"] and b.json()["document"]["revision"] == 1
    assert len(c.req("get", f"/creative-documents/{d['id']}/history").json()["operations"]) == 1


def test_formats_are_independent_documents(setup):
    c, _, by = setup
    small, square = open_doc(c, by[(300, 250)]), open_doc(c, by[(1080, 1080)])
    assert small["id"] != square["id"] and small["canvas"] != square["canvas"]
    before = c.req("get", f"/creatives/{by[(1080, 1080)]['id']}").json()["spec"]
    r = ops(c, small, [cmd("UPDATE_TEXT", layer_id=layer(small, "headline")["id"], text="Only the small one"), cmd("CHANGE_COLOR", layer_id="background", field="color", value="#123456"),
                       cmd("MOVE_ELEMENT", layer_id=layer(small, "product")["id"], x=1, y=1)])
    assert r.status_code == 200
    assert c.req("get", f"/creatives/{by[(1080, 1080)]['id']}").json()["spec"] == before
    sq = c.req("get", f"/creative-documents/{square['id']}").json()
    assert sq["revision"] == 0 and layer(sq, "headline")["text"] != "Only the small one"
    assert sq["id"] != small["id"] and {d["format_id"] for d in c.req("get", f"/campaigns/{small['campaign_id']}/creative-documents").json()} == {small["format_id"], square["format_id"]}


def test_versions_restore_and_immutability(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    head = layer(d, "headline")
    orig = head["text"]
    v = c.req("post", f"/creative-documents/{d['id']}/versions", json={"name": "Before edits"}).json()
    d = ops(c, d, [cmd("UPDATE_TEXT", layer_id=head["id"], text="Edited"), cmd("DELETE_ELEMENT", layer_id=layer(d, "product")["id"])]).json()["document"]
    vs = c.req("get", f"/creative-documents/{d['id']}/versions").json()
    assert {x["kind"] for x in vs} >= {"initial", "manual"} and vs[0]["id"] != vs[-1]["id"]
    r = c.req("post", f"/creative-documents/{d['id']}/restore/{v['id']}")
    assert r.status_code == 200, r.text
    rd = r.json()["document"]
    assert layer(rd, "headline")["text"] == orig and layer(rd, "product") and rd["revision"] == d["revision"] + 1       # history moves forward
    assert any(x["kind"] == "restore_point" for x in c.req("get", f"/creative-documents/{d['id']}/versions").json())
    # restore itself is undoable
    u = c.req("post", f"/creative-documents/{d['id']}/undo").json()["document"]
    assert layer(u, "headline")["text"] == "Edited"
    # the runtime DB role cannot rewrite or delete history
    with SessionLocal() as db:
        set_org(db, uuid.UUID(c.org))
        with pytest.raises(DBAPIError):
            db.execute(text("update creative_versions set version_name='tampered'"))
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text("delete from edit_operations"))
        db.rollback()
    with owner_engine.begin() as conn, pytest.raises(DBAPIError):
        conn.execute(text("update creative_versions set version_name='tampered'"))


def test_tenant_isolation_for_every_editor_route(make_client, env, setup):
    a, _, by = setup
    d = open_doc(a, by[(300, 250)])
    vid = a.req("post", f"/creative-documents/{d['id']}/versions", json={}).json()["id"]
    b = make_client()
    b.register("intruder@t.example.com", org="Other")
    did = d["id"]
    checks = [("get", f"/creative-documents/{did}", None), ("patch", f"/creative-documents/{did}", {"name": "pwn"}),
              ("post", f"/creative-documents/{did}/operations", {"expected_revision": 0, "commands": [cmd("UPDATE_TEXT", layer_id=layer(d, "headline")["id"], text="pwn")]}),
              ("post", f"/creative-documents/{did}/undo", {}), ("post", f"/creative-documents/{did}/redo", {}), ("get", f"/creative-documents/{did}/history", None),
              ("get", f"/creative-documents/{did}/versions", None), ("post", f"/creative-documents/{did}/versions", {}), ("post", f"/creative-documents/{did}/restore/{vid}", None),
              ("get", f"/creative-documents/{did}/preflight", None), ("post", f"/creative-documents/{did}/render", {}), ("post", f"/creatives/{by[(300, 250)]['id']}/document", None)]
    for m, url, body in checks:
        r = b.req(m, url, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (m, url, r.status_code, r.text)
    # B cannot point its own layer at A's asset even by guessing the id
    cb_cid = prepared(b, env, n_select=1)
    cb = gen(b, cb_cid, sizes=[{"width": 300, "height": 250}]).json()[0]
    db_ = open_doc(b, cb)
    r = ops(b, db_, [cmd("REPLACE_IMAGE", layer_id=layer(db_, "product")["id"], asset_id=layer(d, "product")["asset_id"])])
    assert r.status_code == 422 and r.json()["error"]["code"] == "asset_not_found"
    r = ops(b, db_, [cmd("CHANGE_BACKGROUND", background={"type": "image", "asset_id": d["background"].get("asset_id") or layer(d, "product")["asset_id"]})])
    assert r.status_code == 422
    assert a.req("get", f"/creative-documents/{did}").json()["revision"] == 0
    # unauthenticated
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get(f"/creative-documents/{did}").status_code == 401


def test_render_job_exact_dimensions_formats_and_export_uses_latest_revision(setup):
    c, cid, by = setup
    d = open_doc(c, by[(300, 250)])
    head = layer(d, "headline")
    d = ops(c, d, [cmd("UPDATE_TEXT", layer_id=head["id"], text="Rendered after edit"), cmd("CHANGE_COLOR", layer_id="background", field="color", value="#ff00ff")]).json()["document"]
    sizes = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}
    for fmt, pil in sizes.items():
        j = c.req("post", f"/creative-documents/{d['id']}/render", json={"output_format": fmt})
        assert j.status_code == 202, j.text
        done = c.req("get", f"/render-jobs/{j.json()['id']}").json()
        assert done["status"] == "completed", done
        im = Image.open(io.BytesIO(c.http.get(done["url"]).content))
        assert im.size == (300, 250) and im.format == pil
    assert c.req("get", f"/creative-documents/{d['id']}").json()["rendered_revision"] == d["revision"]
    # the gallery export path renders the *document*, so an edit made after the last render is never exported stale
    d = ops(c, d, [cmd("CHANGE_BACKGROUND", background={"type": "solid", "color": "#00ff00"}), cmd("UPDATE_STYLE", layer_id="background", changes={"scrims": []})]).json()["document"]
    assert c.req("get", f"/creative-documents/{d['id']}").json()["rendered_revision"] != d["revision"]
    with SessionLocal() as db:
        set_org(db, uuid.UUID(c.org))
        from app.export.service import render_bytes
        from app.models import GeneratedCreative
        data = render_bytes(db, uuid.UUID(c.org), db.get(GeneratedCreative, uuid.UUID(by[(300, 250)]["id"])), env_storage())
        db.commit()
    px = Image.open(io.BytesIO(data)).convert("RGB").getpixel((299, 1))
    assert px[1] > 200 and px[0] < 80      # green background reached the exported pixels


def env_storage():
    from app.storage.base import get_storage
    from app.main import app
    return app.dependency_overrides[get_storage]()


def test_preflight_reports_real_problems(setup):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    head = layer(d, "headline")
    d = ops(c, d, [cmd("UPDATE_STYLE", layer_id=head["id"], changes={"auto_fit": False, "font_size": 90, "max_lines": 1}), cmd("TOGGLE_VISIBILITY", layer_id=layer(d, "cta")["id"], visible=False)]).json()["document"]
    codes = {w["code"] for w in c.req("get", f"/creative-documents/{d['id']}/preflight").json()["warnings"]}
    assert "text_overflow" in codes and "cta_missing" in codes


def _no_urls(d):
    """Signed asset URLs embed an expiry that changes every second; compare everything else."""
    d = dict(d)
    d["assets"] = {k: {x: y for x, y in v.items() if x != "url"} for k, v in d.get("assets", {}).items()}
    return d


def test_duplicate_to_another_size_creates_independent_document_and_leaves_source_untouched(setup, make_client):
    c, cid, by = setup
    src = open_doc(c, by[(300, 250)])
    src = ops(c, src, [cmd("UPDATE_TEXT", layer_id=layer(src, "headline")["id"], text="Copy me")]).json()["document"]
    before_src = c.req("get", f"/creative-documents/{src['id']}").json()
    r = c.req("post", f"/creative-documents/{src['id']}/duplicate", json={"width": 600, "height": 500})
    assert r.status_code == 201, r.text
    nd = r.json()
    assert nd["id"] != src["id"] and nd["canvas"] == {"width": 600, "height": 500} and nd["revision"] == 0 and nd["source_document_id"] == src["id"]
    h = layer(nd, "headline")
    assert h["text"] == "Copy me" and h["w"] == pytest.approx(layer(src, "headline")["w"] * 2) and h["font_size"] == pytest.approx(layer(src, "headline")["font_size"] * 2, rel=1e-2)
    assert _no_urls(c.req("get", f"/creative-documents/{src['id']}").json()) == _no_urls(before_src)                                    # source preserved exactly
    # it is a real banner now: listed, rendered at the exact size, and independent from then on
    listed = c.req("get", f"/campaigns/{cid}/creatives").json()
    assert any((x["width"], x["height"]) == (600, 500) for x in listed)
    assert ops(c, nd, [cmd("UPDATE_TEXT", layer_id=h["id"], text="Only the copy")]).status_code == 200
    assert layer(c.req("get", f"/creative-documents/{src['id']}").json(), "headline")["text"] == "Copy me"
    j = c.req("post", f"/creative-documents/{nd['id']}/render", json={"output_format": "png"}).json()
    done = c.req("get", f"/render-jobs/{j['id']}").json(); assert (done["width"], done["height"]) == (600, 500)
    # validation + isolation
    assert c.req("post", f"/creative-documents/{src['id']}/duplicate", json={"width": 10, "height": 10}).status_code == 422
    b = make_client(); b.register("dup-intruder@t.example.com", org="Other")
    assert b.req("post", f"/creative-documents/{src['id']}/duplicate", json={"width": 600, "height": 500}).status_code == 404
