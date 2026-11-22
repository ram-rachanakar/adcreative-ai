import copy
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.ai.mock_image import MockImageProvider
from app.core.db import SessionLocal, set_org
from app.orchestration import factories
from app.rendering.spec import CreativeSpec
from app.templates import mapping, schema as S
from conftest import owner_engine
from test_creative_api import env, gen, prepared  # noqa: F401  (env is the autouse storage fixture)
from test_editor import cmd, layer, open_doc, ops

SIZES = [{"width": 300, "height": 250}, {"width": 1080, "height": 1080}]


@pytest.fixture
def setup(make_client, env):
    c = make_client()
    cid = prepared(c, env, n_select=1)
    factories.image_provider = lambda: MockImageProvider()
    creatives = gen(c, cid, sizes=SIZES).json()
    by = {(x["width"], x["height"]): x for x in creatives}
    return c, cid, by


def tpl(c, key_name="Minimal Centered"):
    items = c.req("get", "/templates?thumbnails=false").json()["items"]
    return next(t for t in items if t["name"] == key_name)


def preview(c, t, doc, mode, **kw):
    return c.req("post", f"/templates/{t['id']}/preview", json={"document_id": doc["id"], "mode": mode, **kw})


def apply(c, t, pv, doc, **kw):
    return c.req("post", f"/templates/{t['id']}/apply", json={"application_id": pv["application_id"], "expected_revision": doc["revision"], **kw})


def spec_of(c, doc):
    d = c.req("get", f"/creative-documents/{doc['id']}").json()
    return d, {l["id"]: {k: v for k, v in l.items() if k != "z_index"} for l in d["layers"]}, [l["id"] for l in d["layers"]]


# ---------------------------------------------------------------- library
def test_system_library_search_filters_and_supported_formats(setup):
    c, _, by = setup
    res = c.req("get", "/templates?thumbnails=false").json()
    assert res["total"] >= 9 and all(t["owner_type"] == "system" and t["status"] == "published" for t in res["items"])
    assert {t["category"] for t in res["items"]} >= {"beauty", "fashion", "electronics", "food", "home", "fitness", "services", "general"}
    q = lambda s: c.req("get", f"/templates?thumbnails=false&{s}").json()["items"]          # noqa: E731
    assert {t["name"] for t in q("style=luxury")} == {"Luxury Noir"}
    assert {t["name"] for t in q("industry=food")} == {"Fresh Food"}
    assert "Hero Full-bleed" in {t["name"] for t in q("layout=full-bleed")}
    assert {t["name"] for t in q("q=luxury")} == {"Luxury Noir"} and q("q=nonexistent-zzz") == []
    assert all(any(f["format_id"] == "300x250" for f in t["supported_formats"]) for t in q("width=300&height=250"))
    assert q("owner=personal") == [] and q("tab=mine") == []
    # strip formats: the hero template has NO 728x90 layout, so it is never shown as compatible
    hero = next(t for t in q("") if t["name"] == "Hero Full-bleed")
    assert "728x90" not in {f["format_id"] for f in hero["supported_formats"]}
    assert "Hero Full-bleed" not in {t["name"] for t in q("compatible_with=728x90")}
    d = c.req("get", f"/templates?thumbnails=false&document_id={open_doc(c, by[(300, 250)])['id']}").json()["items"]
    assert all("compatible" in t for t in d)


def test_thumbnails_are_rendered_once_and_signed(setup):
    c, *_ = setup
    items = c.req("get", "/templates?limit=2").json()["items"]
    assert all(t["thumbnail"] and t["thumbnail"]["url"].startswith("http") for t in items)
    t = items[0]
    a = c.req("get", f"/templates/{t['id']}/layouts/1080x1080/thumbnail").json()
    b = c.req("get", f"/templates/{t['id']}/layouts/1080x1080/thumbnail").json()
    assert a["asset_id"] == b["asset_id"] and (a["width"], a["height"]) == (1080, 1080)
    assert c.req("get", f"/templates/{t['id']}/layouts/7x7/thumbnail").status_code == 404


def test_system_templates_are_read_only_but_duplicable(setup):
    c, *_ = setup
    t = tpl(c)
    assert c.req("patch", f"/templates/{t['id']}", json={"name": "x"}).status_code == 403
    assert c.req("post", f"/templates/{t['id']}/archive").status_code == 403
    assert c.req("post", f"/templates/{t['id']}/versions", json={"layouts": {"300x250": {"slots": []}}}).status_code in (403, 422)
    r = c.req("post", f"/templates/{t['id']}/duplicate", json={"name": "Mine", "visibility": "personal"})
    assert r.status_code == 201 and r.json()["owner_type"] == "personal" and r.json()["source"] == "duplicated" and r.json()["can_edit"]
    assert [x["name"] for x in c.req("get", "/templates?tab=mine&thumbnails=false").json()["items"]] == ["Mine"]


# ---------------------------------------------------------------- application modes
def test_layout_only_moves_but_preserves_copy_style_assets_and_background(setup):
    c, _, by = setup
    doc = open_doc(c, by[(300, 250)])
    before_doc, before, order0 = spec_of(c, doc)
    t = tpl(c, "Modern Split")
    pv = preview(c, t, doc, "layout_only")
    assert pv.status_code == 200, pv.text
    pv = pv.json()
    assert pv["before_url"] and pv["after_url"] and pv["can_apply"] and pv["mode"] == "layout_only" and not pv["requires_confirmation"]
    # non-destructive: the document is untouched by the preview
    assert c.req("get", f"/creative-documents/{doc['id']}").json()["revision"] == doc["revision"]
    r = apply(c, t, pv, doc)
    assert r.status_code == 200, r.text
    after_doc, after, order1 = spec_of(c, doc)
    assert after_doc["revision"] == doc["revision"] + 1
    moved = [i for i in after if any(after[i][k] != before[i][k] for k in "xywh")]
    assert moved, "layout-only must move something"
    keep = ("text", "label", "font_family", "font_weight", "color", "fill", "text_color", "asset_id", "opacity")
    for i in after:
        for k in keep:
            assert after[i].get(k) == before[i].get(k), (i, k)
    assert after_doc["background"] == before_doc["background"]
    assert set(after) == set(before)                                  # nothing deleted, nothing invented
    assert after_doc["metadata"]["source_template_id"] == t["id"]      # provenance
    # undo restores the EXACT previous state, redo the applied one
    u = c.req("post", f"/creative-documents/{doc['id']}/undo")
    assert u.status_code == 200
    d2, again, order2 = spec_of(c, doc)
    assert again == before and order2 == order0 and d2["background"] == before_doc["background"] and d2["metadata"].get("source_template_id") is None
    assert c.req("post", f"/creative-documents/{doc['id']}/redo").status_code == 200
    d3, redone, _ = spec_of(c, doc)
    assert redone == after and d3["metadata"]["source_template_id"] == t["id"]


def test_layout_style_restyles_but_keeps_product_logo_copy(setup):
    c, _, by = setup
    doc = open_doc(c, by[(1080, 1080)])
    _, before, _ = spec_of(c, doc)
    t = tpl(c, "Luxury Noir")
    pv = preview(c, t, doc, "layout_style").json()
    assert apply(c, t, pv, doc).status_code == 200
    _, after, _ = spec_of(c, doc)
    heads = [i for i, l in after.items() if l["type"] == "text" and l.get("slot") != "decorative_shape"]
    assert any(after[i]["font_family"] == "Liberation Serif" and after[i]["font_family"] != before[i]["font_family"] for i in heads), "typography is restyled"
    for i, l in before.items():
        if l["type"] == "image":
            assert after[i]["asset_id"] == l["asset_id"]                # product and logo are the same assets
        if l["type"] == "text":
            assert after[i]["text"] == l["text"]
        if l["type"] == "button":
            assert after[i]["label"] == l["label"]


def test_full_template_needs_confirmation_and_cancel_changes_nothing(setup):
    c, _, by = setup
    doc = open_doc(c, by[(300, 250)])
    d0, before, order0 = spec_of(c, doc)
    t = tpl(c, "Bold Promo")
    pv = preview(c, t, doc, "full").json()
    assert pv["requires_confirmation"] is True
    kinds = {i["action"] for i in pv["mapping"]["items"]}
    assert "replaced" in kinds and "added" in kinds                      # background replaced, badge added
    r = apply(c, t, pv, doc)                                              # no confirm flag
    assert r.status_code == 422 and r.json()["error"]["code"] == "confirmation_required"
    assert c.req("post", f"/template-applications/{pv['application_id']}/reject").json()["status"] == "rejected"
    assert apply(c, t, pv, doc, confirm=True).status_code == 409          # a rejected preview is closed
    d1, same, order1 = spec_of(c, doc)
    assert same == before and d1["revision"] == d0["revision"] and d1["background"] == d0["background"]
    pv2 = preview(c, t, doc, "full").json()
    r = apply(c, t, pv2, doc, confirm=True)
    assert r.status_code == 200
    d2, after, _ = spec_of(c, doc)
    assert d2["background"]["color"] == "#ffd400" and any(i.startswith("tpl_") for i in after)
    assert c.req("post", f"/creative-documents/{doc['id']}/undo").status_code == 200
    d3, restored, order3 = spec_of(c, doc)
    assert restored == before and order3 == order0 and d3["background"] == d0["background"]


def test_stale_preview_is_rejected_after_the_document_changes(setup):
    c, _, by = setup
    doc = open_doc(c, by[(300, 250)])
    t = tpl(c)
    pv = preview(c, t, doc, "layout_only").json()
    head = layer(doc, "headline")
    assert ops(c, doc, [cmd("UPDATE_TEXT", layer_id=head["id"], text="Edited meanwhile")]).status_code == 200
    r = c.req("post", f"/templates/{t['id']}/apply", json={"application_id": pv["application_id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "revision_conflict"
    assert layer(c.req("get", f"/creative-documents/{doc['id']}").json(), "headline")["text"] == "Edited meanwhile"


def test_unsupported_size_is_refused_not_stretched(setup):
    c, _, by = setup
    doc = open_doc(c, by[(300, 250)])
    t = tpl(c, "Hero Full-bleed")
    # make a banner size that this template lacks via copy-to-size
    r = c.req("post", f"/creative-documents/{doc['id']}/duplicate", json={"width": 728, "height": 90})
    assert r.status_code in (200, 201), r.text
    nd = r.json()["document"] if "document" in r.json() else r.json()
    pv = preview(c, t, nd, "layout_only")
    assert pv.status_code == 422 and pv.json()["error"]["code"] == "format_unsupported" and "supported" in pv.json()["error"]


def test_formats_stay_independent(setup):
    c, _, by = setup
    small, big = open_doc(c, by[(300, 250)]), open_doc(c, by[(1080, 1080)])
    _, big_before, _ = spec_of(c, big)
    t1, t2 = tpl(c, "Modern Split"), tpl(c, "Luxury Noir")
    pv = preview(c, t1, small, "layout_style").json()
    assert apply(c, t1, pv, small).status_code == 200
    d, big_after, _ = spec_of(c, big)
    assert big_after == big_before and d["revision"] == big["revision"] and d["metadata"].get("source_template_id") is None
    pv2 = preview(c, t2, big, "layout_style").json()
    assert apply(c, t2, pv2, big).status_code == 200
    ds, _, _ = spec_of(c, small)
    assert ds["metadata"]["source_template_id"] == t1["id"]               # the other banner kept its own template


def test_revert_application_and_later_edits(setup):
    c, _, by = setup
    doc = open_doc(c, by[(300, 250)])
    _, before, _ = spec_of(c, doc)
    t = tpl(c)
    pv = preview(c, t, doc, "layout_style").json()
    apply(c, t, pv, doc)
    cur = c.req("get", f"/creative-documents/{doc['id']}").json()
    assert ops(c, cur, [cmd("UPDATE_TEXT", layer_id=layer(cur, "headline")["id"], text="After template")]).status_code == 200
    r = c.req("post", f"/template-applications/{pv['application_id']}/revert", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "later_edits"
    r = c.req("post", f"/template-applications/{pv['application_id']}/revert", json={"force": True})
    assert r.status_code == 200 and r.json()["status"] == "reverted"
    _, back, _ = spec_of(c, doc)
    assert back == before
    hist = c.req("get", f"/creative-documents/{doc['id']}/template-applications").json()
    assert [h["status"] for h in hist] == ["reverted"]


def test_locked_layers_are_left_alone(setup):
    c, _, by = setup
    doc = open_doc(c, by[(300, 250)])
    head = layer(doc, "headline")
    assert ops(c, doc, [cmd("TOGGLE_LOCK", layer_id=head["id"], locked=True)]).status_code == 200
    doc = c.req("get", f"/creative-documents/{doc['id']}").json()
    t = tpl(c, "Modern Split")
    pv = preview(c, t, doc, "layout_style").json()
    assert any(w["code"] == "layer_locked" for w in pv["warnings"])
    assert apply(c, t, pv, doc).status_code == 200
    after = layer(c.req("get", f"/creative-documents/{doc['id']}").json(), "headline")
    assert (after["x"], after["y"], after["w"], after["h"], after["font_family"]) == (head["x"], head["y"], head["w"], head["h"], head["font_family"])


# ---------------------------------------------------------------- new banner from a template (workflow A)
def test_start_a_new_banner_from_a_template(setup):
    c, cid, by = setup
    t = tpl(c, "Minimal Centered")
    r = c.req("post", f"/templates/{t['id']}/preview", json={"campaign_id": cid, "format_id": "1080x1080", "mode": "full"})
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["document_id"] is None and pv["after_url"] and pv["format_id"] == "1080x1080"
    r = c.req("post", f"/templates/{t['id']}/apply", json={"application_id": pv["application_id"]})
    assert r.status_code == 200 and r.json()["created"] is True, r.text
    d = r.json()["document"]
    roles = {l["role"] for l in d["layers"]}
    assert {"headline", "product", "cta"} <= roles and d["canvas"] == {"width": 1080, "height": 1080}
    assert d["metadata"]["source_template_id"] == t["id"] and d["revision"] == 0
    head = layer(d, "headline")
    assert head["text"] and head["font_size"] > 10                       # campaign copy (from the concept), laid out by the template
    # fully editable afterwards
    assert ops(c, d, [cmd("UPDATE_TEXT", layer_id=head["id"], text="My own headline")]).status_code == 200
    assert c.req("post", f"/templates/{t['id']}/apply", json={"application_id": pv["application_id"]}).status_code in (200, 409)


def test_new_banner_needs_an_available_layout(setup):
    c, cid, _ = setup
    t = tpl(c, "Hero Full-bleed")
    r = c.req("post", f"/templates/{t['id']}/preview", json={"campaign_id": cid, "format_id": "728x90", "mode": "full"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "format_unsupported"


# ---------------------------------------------------------------- save as template, ownership, tenancy
def second_user_in_org(make_client, owner, role="designer"):
    u = make_client()
    email = f"{uuid.uuid4().hex[:8]}@u.example.com"
    u.register(email, "Own")
    assert owner.req("post", "/members", json={"email": email, "role": role}).status_code == 201
    u.org = owner.org
    return u


def save(c, doc, **kw):
    return c.req("post", f"/creative-documents/{doc['id']}/save-as-template", json={"name": "My Banner", "category": "beauty", "style_tags": ["minimal"], "layout_tags": ["centered"], **kw})


def test_save_as_template_uses_placeholders_and_excludes_customer_assets(setup, make_client):
    c, _, by = setup
    doc = open_doc(c, by[(300, 250)])
    head_text = layer(doc, "headline")["text"]
    r = save(c, doc, visibility="organization")
    assert r.status_code == 201, r.text
    out = r.json()
    t = out["template"]
    assert t["owner_type"] == "organization" and t["source"] == "saved_from_design" and t["status"] == "published" and out["included_assets"] == []
    detail = c.req("get", f"/templates/{t['id']}").json()
    assert {"headline_primary", "primary_cta", "product_primary"} <= {s["role"] for s in detail["slots_by_format"]["300x250"]}
    with SessionLocal() as db:
        set_org(db, uuid.UUID(c.org))
        raw = "".join(r[0] for r in db.execute(text("select layout_definition::text from template_layouts where organization_id = :o"), {"o": c.org}))     # (Row repr truncates!)
        defn = "".join(r[0] for r in db.execute(text("select definition::text from template_versions where organization_id = :o"), {"o": c.org}))
    assert head_text not in raw and head_text not in defn            # no customer copy
    assert '"asset_id": null' in raw and not __import__("re").search(r'"asset_id": "[0-9a-f-]{36}"', raw)      # no product/customer asset ids
    assert "{{headline_primary}}" in raw and "{{primary_cta}}" in raw
    # the saved template really works (apply it to the OTHER banner of the campaign)
    other = open_doc(c, by[(1080, 1080)])
    assert c.req("get", f"/templates/{t['id']}").json()["supported_formats"][0]["format_id"] == "300x250"
    pv = preview(c, t, other, "layout_only")
    assert pv.status_code == 422                                      # saved at 300x250 only: 1080x1080 is correctly unsupported
    same = preview(c, t, doc, "layout_style")
    assert same.status_code == 200 and apply(c, t, same.json(), doc).status_code == 200


def test_save_as_template_extra_formats_must_keep_the_aspect_ratio(setup):
    c, _, by = setup
    doc = open_doc(c, by[(300, 250)])
    assert save(c, doc, formats=["1080x1080"]).status_code == 422
    r = save(c, doc, formats=["336x280", "600x500"])
    assert r.status_code == 201 and {f["format_id"] for f in r.json()["template"]["supported_formats"]} == {"300x250", "336x280", "600x500"}
    bigger = [f for f in r.json()["template"]["supported_formats"] if f["format_id"] == "600x500"][0]
    assert bigger["inherits"] == "300x250"


def test_visibility_and_tenant_isolation(setup, make_client):
    c, cid, by = setup
    doc = open_doc(c, by[(300, 250)])
    org_t = save(c, doc, name="Org one", visibility="organization").json()["template"]
    mine = save(c, doc, name="Only mine", visibility="personal").json()["template"]
    mate = second_user_in_org(make_client, c, "designer")
    names = {t["name"] for t in mate.req("get", "/templates?thumbnails=false").json()["items"]}
    assert "Org one" in names and "Only mine" not in names                   # personal templates are private, organization ones are shared
    assert mate.req("get", f"/templates/{mine['id']}").status_code == 404
    assert mate.req("patch", f"/templates/{org_t['id']}", json={"name": "hijack"}).status_code == 403      # not the author, not a manager
    assert c.req("patch", f"/templates/{org_t['id']}", json={"name": "Renamed"}).status_code == 200
    viewer = second_user_in_org(make_client, c, "viewer")
    assert "Renamed" in {t["name"] for t in viewer.req("get", "/templates?thumbnails=false").json()["items"]}
    assert viewer.req("post", f"/templates/{org_t['id']}/preview", json={"document_id": doc["id"], "mode": "layout_only"}).status_code == 403
    # a different organization sees neither, can't use them, and can't touch the first org's documents
    other = make_client()
    other.register("rival@x.example.com", "Rival")
    assert {t["name"] for t in other.req("get", "/templates?thumbnails=false").json()["items"]}.isdisjoint({"Org one", "Only mine", "Renamed"})
    for t in (org_t, mine):
        assert other.req("get", f"/templates/{t['id']}").status_code == 404
        assert other.req("post", f"/templates/{t['id']}/duplicate", json={}).status_code == 404
        assert other.req("post", f"/templates/{t['id']}/favorite").status_code == 404
    sys_t = tpl(other, "Modern Split")
    assert other.req("post", f"/templates/{sys_t['id']}/preview", json={"document_id": doc["id"], "mode": "layout_only"}).status_code == 404   # foreign document
    pv = preview(c, tpl(c), doc, "layout_only").json()
    assert other.req("post", f"/templates/{sys_t['id']}/apply", json={"application_id": pv["application_id"]}).status_code == 404            # foreign application
    assert other.req("post", f"/template-applications/{pv['application_id']}/reject").status_code == 404
    assert other.req("post", f"/creative-documents/{doc['id']}/save-as-template", json={"name": "steal"}).status_code == 404
    with SessionLocal() as db:                                              # RLS: raw SQL as org B sees only system rows
        set_org(db, uuid.UUID(other.org))
        rows = db.execute(text("select name, owner_type from templates")).all()
    assert rows and all(r[1] == "system" for r in rows)


def test_template_cannot_reference_another_tenants_assets(setup, make_client):
    c, cid, by = setup
    doc = open_doc(c, by[(300, 250)])
    foreign_asset = layer(doc, "product")["asset_id"]
    other = make_client()
    other.register("rival2@x.example.com", "Rival2")
    layout = {"slots": [{"id": "d", "role": "decorative_shape", "kind": "image", "transform": {"x": 0, "y": 0, "w": .2, "h": .2}, "asset_policy": {"mode": "static", "asset_id": foreign_asset}}]}
    r = other.req("post", "/templates", json={"name": "Sneaky", "layouts": {"300x250": layout}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "asset_not_allowed"


def test_template_json_is_strictly_validated(setup):
    c, *_ = setup
    slot = {"id": "h", "role": "headline_primary", "kind": "text", "transform": {"x": 0, "y": 0, "w": 1, "h": .2}}
    bad = [
        {**slot, "default_style": {"font_family": "Comic Sans"}},
        {**slot, "default_style": {"color": "javascript:alert(1)"}},
        {**slot, "default_style": {"text": "<script>alert(1)</script>"}},
        {**slot, "default_style": {"font_size": {"$ref": "http://evil"}}},
        {**slot, "kind": "image"},                                            # text role as image
        {**slot, "html": "<b>x</b>"},                                         # unknown key
        {**slot, "content_policy": {"mode": "placeholder", "placeholder": "<img src=x onerror=1>"}},
    ]
    for s in bad:
        r = c.req("post", "/templates", json={"name": "x", "layouts": {"300x250": {"slots": [s]}}})
        assert r.status_code == 422, (s, r.text)
    ok = c.req("post", "/templates", json={"name": "hand made", "layouts": {"300x250": {"slots": [slot]}}})
    assert ok.status_code == 201, ok.text


def test_versions_are_immutable_and_archived_templates_do_not_break_banners(setup):
    c, _, by = setup
    doc = open_doc(c, by[(300, 250)])
    t = save(c, doc, visibility="organization").json()["template"]
    ver = [{"slots": [{"id": "headline_primary", "role": "headline_primary", "kind": "text", "transform": {"x": .1, "y": .1, "w": .8, "h": .3}}]}]
    r = c.req("post", f"/templates/{t['id']}/versions", json={"layouts": {"300x250": ver[0]}, "change_note": "v2"})
    assert r.status_code == 201 and r.json()["current_version"] == 2
    vs = c.req("get", f"/templates/{t['id']}/versions").json()
    assert [v["version"] for v in vs] == [2, 1] and vs[0]["current"]
    with owner_engine.begin() as conn:                                        # even the table owner hits the immutability trigger
        with pytest.raises(DBAPIError):
            conn.execute(text("update template_versions set change_note = 'tampered'"))
    with SessionLocal() as db:
        set_org(db, uuid.UUID(c.org))
        with pytest.raises(DBAPIError):
            db.execute(text("delete from template_layouts"))
            db.commit()
    # apply v1 to a banner, then archive: the banner keeps working and remembers v1
    pv = c.req("post", f"/templates/{t['id']}/preview", json={"document_id": doc["id"], "mode": "layout_style", "template_version": 1}).json()
    assert pv["template"]["version"] == 1
    assert apply(c, t, pv, doc).status_code == 200
    assert c.req("post", f"/templates/{t['id']}/archive").json()["status"] == "archived"
    d = c.req("get", f"/creative-documents/{doc['id']}").json()
    assert d["metadata"]["source_template_id"] == t["id"] and d["metadata"]["source_template_version"] == 1
    assert ops(c, d, [cmd("UPDATE_TEXT", layer_id=layer(d, "headline")["id"], text="Still editable")]).status_code == 200
    again = c.req("post", f"/templates/{t['id']}/preview", json={"document_id": doc["id"], "mode": "layout_only"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "template_archived"
    assert t["id"] not in {x["id"] for x in c.req("get", "/templates?thumbnails=false").json()["items"]}
    assert t["id"] in {x["id"] for x in c.req("get", "/templates?thumbnails=false&status=archived").json()["items"]}


def test_favorites_and_usage(setup):
    c, _, by = setup
    t = tpl(c)
    assert c.req("post", f"/templates/{t['id']}/favorite").status_code == 200
    assert c.req("post", f"/templates/{t['id']}/favorite").status_code == 200           # idempotent
    favs = c.req("get", "/templates?favorites=true&thumbnails=false").json()["items"]
    assert [x["id"] for x in favs] == [t["id"]] and favs[0]["favorite"]
    assert c.req("delete", f"/templates/{t['id']}/favorite").status_code == 200
    assert c.req("get", "/templates?favorites=true&thumbnails=false").json()["items"] == []
    doc = open_doc(c, by[(300, 250)])
    apply(c, t, preview(c, t, doc, "layout_only").json(), doc)
    u = c.req("get", f"/templates/{t['id']}/usage").json()
    assert u["total"] == 1 and u["by_mode"] == {"layout_only": 1}


def test_copy_layer_to_another_artboard(setup):
    c, _, by = setup
    small, big = open_doc(c, by[(300, 250)]), open_doc(c, by[(1080, 1080)])
    head = layer(small, "headline")
    r = c.req("post", f"/creative-documents/{big['id']}/copy-layer", json={"source_document_id": small["id"], "layer_id": head["id"], "expected_revision": big["revision"]})
    assert r.status_code == 200, r.text
    d = r.json()["document"]
    copied = [l for l in d["layers"] if l["name"].endswith("(copied)")][0]
    assert copied["text"] == head["text"] and copied["id"] != head["id"] and copied["font_size"] > head["font_size"]       # scaled to the larger artboard
    assert c.req("get", f"/creative-documents/{small['id']}").json()["revision"] == small["revision"]                 # source untouched
    assert c.req("post", f"/creative-documents/{big['id']}/undo").status_code == 200


# ---------------------------------------------------------------- the pure mapping engine
def _doc(**over):
    base = {"canvas": {"width": 400, "height": 400}, "background": {"type": "solid", "color": "#ffffff", "scrims": []}, "meta": {}, "elements": [
        {"id": "h", "type": "text", "role": "headline", "name": "H", "x": 10, "y": 10, "w": 380, "h": 60, "text": "Buy now", "font_size": 30, "color": "#123456"},
        {"id": "h2", "type": "text", "role": "headline", "name": "H2", "x": 10, "y": 80, "w": 380, "h": 30, "text": "Sub", "font_size": 18},
        {"id": "p", "type": "image", "role": "product", "name": "P", "x": 100, "y": 120, "w": 200, "h": 200, "asset_id": str(uuid.uuid4())},
        {"id": "x", "type": "shape", "role": "decor", "name": "Own decoration", "x": 0, "y": 390, "w": 400, "h": 10},
        {"id": "b", "type": "button", "role": "cta", "name": "B", "x": 150, "y": 340, "w": 100, "h": 40, "label": "Go", "font_size": 16}]}
    base.update(over)
    return CreativeSpec.model_validate(base).model_dump(mode="json")      # documents are always normalised (defaults filled) before mapping


def _layout(slots, bg=None, W=400, H=400):
    d = S.LayoutDef.model_validate({"slots": slots, **({"background": bg} if bg else {})}).model_dump(mode="json")
    return {"width": W, "height": H, "definition": d}


def _slot(sid, role, kind, x, y, w, h, **kw):
    return {"id": sid, "role": role, "kind": kind, "transform": {"x": x, "y": y, "w": w, "h": h}, **kw}


def test_mapping_is_semantic_unmapped_layers_survive_and_input_is_not_mutated():
    doc = _doc()
    snap = copy.deepcopy(doc)
    lay = _layout([_slot("a", "headline_primary", "text", .1, .5, .8, .2, required=True), _slot("b", "primary_cta", "button", .3, .9, .4, .08), _slot("c", "brand_logo", "image", 0, 0, .1, .1, required=True)])
    out = mapping.map_template(doc, lay, mapping.Options(mode="layout_only"))
    assert doc == snap
    by = {e["id"]: e for e in out.candidate["elements"]}
    assert (by["h"]["x"], by["h"]["y"]) == (40.0, 200.0) and (by["b"]["x"], by["b"]["y"]) == (120.0, 360.0)
    assert by["h"]["text"] == "Buy now" and by["h"]["color"] == "#123456"
    assert {"h2", "p", "x"} <= set(out.unmapped) and by["p"]["x"] == 100                       # untouched
    assert out.missing_required == ["brand_logo"] and not any(e["id"].startswith("tpl_") for e in out.candidate["elements"])
    assert len(out.candidate["elements"]) == len(doc["elements"])                            # never deletes / invents layers


def test_mapping_ambiguity_and_override():
    doc = _doc()
    for e in doc["elements"]:
        if e["id"] in ("h", "h2"):
            e["slot"] = "headline_primary"
    lay = _layout([_slot("a", "headline_primary", "text", .1, .5, .8, .2)])
    out = mapping.map_template(doc, lay, mapping.Options(mode="layout_only"))
    assert out.ambiguities and out.ambiguities[0]["chosen"] == "h"
    out2 = mapping.map_template(doc, lay, mapping.Options(mode="layout_only", slot_overrides={"headline_primary": "h2"}))
    by = {e["id"]: e for e in out2.candidate["elements"]}
    assert by["h2"]["y"] == 200.0 and by["h"]["y"] == 10
    with pytest.raises(mapping.MappingError):
        mapping.map_template(doc, lay, mapping.Options(mode="layout_only", slot_overrides={"headline_primary": "p"}))


def test_mapping_rejects_wrong_canvas_and_unknown_mode():
    lay = _layout([_slot("a", "headline_primary", "text", 0, 0, 1, .2)], W=300, H=300)
    with pytest.raises(mapping.MappingError) as e:
        mapping.map_template(_doc(), lay, mapping.Options(mode="layout_only"))
    assert e.value.code == "format_unsupported"
    with pytest.raises(mapping.MappingError):
        mapping.map_template(_doc(), _layout([_slot("a", "headline_primary", "text", 0, 0, 1, .2)]), mapping.Options(mode="everything"))


def test_brand_rules_beat_template_styling():
    st = {"font_family": "DejaVu Sans", "color": "#ff00ff", "font_size": 24}
    lay = _layout([_slot("a", "headline_primary", "text", .1, .1, .8, .2, default_style=st), _slot("b", "primary_cta", "button", .3, .8, .4, .1, default_style={"fill": "#00ff00", "font_size": 14})])
    out = mapping.map_template(_doc(), lay, mapping.Options(mode="layout_style", brand={"colors": ["#0a5c36", "#c8102e"], "fonts": ["Liberation Serif"]}))
    by = {e["id"]: e for e in out.candidate["elements"]}
    assert by["h"]["font_family"] == "Liberation Serif" and by["h"]["color"] in ("#0a5c36", "#c8102e", "#ffffff", "#111111")
    assert by["b"]["fill"] in ("#0a5c36", "#c8102e")
    assert {w["code"] for w in out.warnings} >= {"brand_font_enforced", "brand_color_substituted"}


def test_full_mode_replaces_owned_content_only_with_confirmation():
    static = {"content_policy": {"mode": "static", "text": "Template wording"}}
    lay = _layout([_slot("a", "headline_primary", "text", .1, .1, .8, .2, **static)], bg={"type": "solid", "color": "#101010"})
    out = mapping.map_template(_doc(), lay, mapping.Options(mode="full"))
    by = {e["id"]: e for e in out.candidate["elements"]}
    assert by["h"]["text"] == "Buy now" and out.needs_confirmation == ["headline_primary"] and out.candidate["background"]["color"] == "#101010"
    assert any(w["code"] == "replacement_not_confirmed" for w in out.warnings)
    out2 = mapping.map_template(_doc(), lay, mapping.Options(mode="full", confirm_replace={"headline_primary"}))
    assert {e["id"]: e for e in out2.candidate["elements"]}["h"]["text"] == "Template wording" and "h" in out2.replaced
    lay_only = mapping.map_template(_doc(), lay, mapping.Options(mode="layout_only"))
    assert lay_only.candidate["background"]["color"] == "#ffffff"                              # background untouched outside full mode


def test_previous_template_decoration_is_replaced_not_stacked():
    deco = _slot("bar", "decorative_shape", "shape", 0, 0, 1, .05, default_style={"fill": "#ff0000"})
    lay = _layout([_slot("a", "headline_primary", "text", .1, .1, .8, .2), deco])
    first = mapping.map_template(_doc(), lay, mapping.Options(mode="layout_style"))
    assert first.created == ["tpl_bar"]
    second = mapping.map_template(first.candidate, lay, mapping.Options(mode="layout_style"))
    assert second.created == ["tpl_bar"] and second.removed == ["tpl_bar"]
    assert sum(e["id"].startswith("tpl_") for e in second.candidate["elements"]) == 1
    keep = mapping.map_template(first.candidate, lay, mapping.Options(mode="layout_only"))
    assert sum(e["id"].startswith("tpl_") for e in keep.candidate["elements"]) == 1 and not keep.created


def test_layout_inheritance_resolves_and_rejects_cycles():
    s = _slot("h", "headline_primary", "text", 0, 0, 1, .2, default_style={"font_size": 20})
    layouts = {"a": {"slots": [s]}, "b": {"extends": "a", "slot_overrides": {"h": {"default_style": {"font_size": 40}}}}, "c": {"extends": "d"}, "d": {"extends": "c"}}
    r = S.resolve_layout(layouts, "b")
    assert r["slots"][0]["default_style"]["font_size"] == 40 and r["slots"][0]["transform"]["w"] == 1.0 and r["extends"] is None
    with pytest.raises(ValueError):
        S.resolve_layout(layouts, "c")
    with pytest.raises(ValueError):
        S.resolve_layout({"b": {"extends": "zzz"}}, "b")
