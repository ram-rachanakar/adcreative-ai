import copy
import uuid

import pytest

from app.ai.image import ImageProviderError
from app.ai.mock_image import MockImageProvider
from app.core.db import SessionLocal, set_org
from app.editor import regeneration
from app.orchestration import factories
from test_creative_api import env, gen, prepared  # noqa: F401
from test_editor import cmd, layer, open_doc, ops, setup  # noqa: F401


class Counting(MockImageProvider):
    def __init__(self):
        self.bg, self.asset = 0, 0
        self.prompts = []

    def generate_background(self, prompt, *a, **k):
        self.bg += 1
        self.prompts.append(prompt)
        return super().generate_background(prompt, *a, **k)

    def generate_visual_asset(self, prompt, *a, **k):
        self.asset += 1
        self.prompts.append(prompt)
        return super().generate_visual_asset(prompt, *a, **k)


@pytest.fixture
def prov():
    p = Counting()
    factories.image_provider = lambda: p
    return p


def regen(c, d, layer_id, prompt="a calm sunrise over mountains", **kw):
    return c.req("post", f"/creative-documents/{d['id']}/regenerate", json={"layer_id": layer_id, "prompt": prompt, "expected_revision": d["revision"], **kw})


def job(c, jid):
    return c.req("get", f"/regeneration-jobs/{jid}").json()


def others(d, skip):
    return {l["id"]: l for l in d["layers"] if l["id"] != skip}


def test_background_regeneration_previews_then_replaces_only_background(setup, prov):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    old_bg = copy.deepcopy(d["background"])
    r = regen(c, d, "background", candidates=2)
    assert r.status_code == 202, r.text
    j = job(c, r.json()["id"])
    assert j["status"] == "ready" and len(j["candidates"]) == 2 and all(x["url"] for x in j["candidates"]) and j["target_type"] == "background"
    # nothing changed in the document just by generating: a candidate is only a preview
    same = c.req("get", f"/creative-documents/{d['id']}").json()
    assert same["revision"] == d["revision"] and same["background"] == old_bg
    # generation guardrails are appended by code
    assert all("No text" in p and "no logos" in p for p in prov.prompts) and prov.bg == 2 and prov.asset == 0
    # reject candidate set 1, then regenerate and accept one candidate of the new job
    assert c.req("post", f"/regeneration-jobs/{j['id']}/reject").json()["status"] == "rejected"
    assert c.req("post", f"/regeneration-jobs/{j['id']}/accept", json={}).status_code == 409
    assert c.req("get", f"/creative-documents/{d['id']}").json()["background"] == old_bg
    j2 = job(c, regen(c, d, "background").json()["id"])
    acc = c.req("post", f"/regeneration-jobs/{j2['id']}/accept", json={"candidate_index": 1})
    assert acc.status_code == 200, acc.text
    nd = acc.json()["document"]
    assert nd["background"]["asset_id"] == j2["candidates"][1]["asset_id"] != old_bg["asset_id"] and nd["background"]["type"] == "image"
    assert nd["revision"] == d["revision"] + 1
    assert others(nd, "background") == others(d, "background")                          # product, logo, text, CTA, decor: untouched
    assert {k: v for k, v in nd["background"].items() if k != "asset_id"} == {k: v for k, v in old_bg.items() if k != "asset_id"}
    # accepted AI edit is a version checkpoint and is undoable back to the exact previous asset
    assert any(v["kind"] == "ai_accept" for v in c.req("get", f"/creative-documents/{d['id']}/versions").json())
    u = c.req("post", f"/creative-documents/{d['id']}/undo").json()["document"]
    assert u["background"] == old_bg and old_bg["asset_id"] in u["assets"] and not u["assets"][old_bg["asset_id"]].get("missing")
    assert job(c, j2["id"])["status"] == "accepted"
    assert c.req("post", f"/regeneration-jobs/{j2['id']}/accept", json={}).status_code == 409   # cannot accept twice


def test_product_defaults_to_deterministic_original_never_ai(setup, prov):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    prod = layer(d, "product")
    j = job(c, regen(c, d, prod["id"], prompt="use the original photo").json()["id"])
    assert j["status"] == "ready" and prov.asset == 0 and prov.bg == 0 and j["provider"] == "local"
    assert {x["method"] for x in j["candidates"]} <= {"original", "cutout"} and all(x["asset_id"] != prod["asset_id"] for x in j["candidates"])
    nd = c.req("post", f"/regeneration-jobs/{j['id']}/accept", json={"candidate_index": 0}).json()["document"]
    assert layer(nd, "product")["asset_id"] == j["candidates"][0]["asset_id"]
    assert others(nd, prod["id"]) == others(d, prod["id"]) and nd["background"] == d["background"]
    # explicit opt-in is the only way an image model is used for the product
    d2 = nd
    j2 = job(c, regen(c, d2, prod["id"], prompt="a different bottle design", allow_ai_product=True, candidates=1).json()["id"])
    assert j2["status"] == "ready" and prov.asset == 1


def test_text_regeneration_keeps_existing_text_until_accepted(setup, prov):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    head, cta = layer(d, "headline"), layer(d, "cta")
    j = job(c, regen(c, d, head["id"], prompt="shorter and punchier").json()["id"])
    assert j["status"] == "ready" and j["candidates"] and all("text" in x for x in j["candidates"]) and prov.bg == prov.asset == 0
    assert layer(c.req("get", f"/creative-documents/{d['id']}").json(), "headline")["text"] == head["text"]
    nd = c.req("post", f"/regeneration-jobs/{j['id']}/accept", json={"candidate_index": 0}).json()["document"]
    assert layer(nd, "headline")["text"] == j["candidates"][0]["text"] != head["text"]
    assert others(nd, head["id"]) == others(d, head["id"]) and layer(nd, "cta")["label"] == cta["label"]


def test_unsupported_targets_and_validation(setup, prov):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    logo = next((l for l in d["layers"] if l.get("role") == "logo"), None)
    if logo:
        r = regen(c, d, logo["id"])
        assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_target"
    assert regen(c, d, "ghost").status_code == 404
    assert regen(c, d, "background", prompt="x").status_code == 422
    lid = layer(d, "headline")["id"]
    d = ops(c, d, [cmd("TOGGLE_LOCK", layer_id=lid, locked=True)]).json()["document"]
    assert regen(c, d, lid).status_code == 423
    stale = regen(c, d, "background", expected_revision=0)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"


def test_document_changed_while_generating_is_not_overwritten(setup, prov):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    head, prod = layer(d, "headline"), layer(d, "product")
    j = job(c, regen(c, d, "background").json()["id"])                      # generated against revision 0
    # unrelated edit while the candidate is waiting: allowed, flagged stale but not materially changed
    r = ops(c, d, [cmd("UPDATE_TEXT", layer_id=head["id"], text="Edited meanwhile")]).json()["document"]
    jj = job(c, j["id"])
    assert jj["stale"] and jj["target_exists"] and not jj["target_changed"]
    acc = c.req("post", f"/regeneration-jobs/{j['id']}/accept", json={"candidate_index": 0})
    assert acc.status_code == 200 and layer(acc.json()["document"], "headline")["text"] == "Edited meanwhile"      # newer edit preserved
    # background itself replaced meanwhile -> explicit acknowledgement required
    cur = acc.json()["document"]
    j2 = job(c, regen(c, cur, "background").json()["id"])
    cur2 = ops(c, cur, [cmd("CHANGE_BACKGROUND", background={"type": "solid", "color": "#445566"})]).json()["document"]
    assert job(c, j2["id"])["target_changed"]
    blocked = c.req("post", f"/regeneration-jobs/{j2['id']}/accept", json={"candidate_index": 0})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "target_changed"
    assert c.req("get", f"/creative-documents/{d['id']}").json()["background"]["color"] == "#445566"
    ok = c.req("post", f"/regeneration-jobs/{j2['id']}/accept", json={"candidate_index": 0, "acknowledge_changes": True})
    assert ok.status_code == 200
    # target deleted meanwhile
    j3 = job(c, regen(c, ok.json()["document"], prod["id"]).json()["id"])
    nd = ops(c, ok.json()["document"], [cmd("DELETE_ELEMENT", layer_id=prod["id"])]).json()["document"]
    gone = c.req("post", f"/regeneration-jobs/{j3['id']}/accept", json={})
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "target_missing"
    assert prod["id"] not in [l["id"] for l in c.req("get", f"/creative-documents/{d['id']}").json()["layers"]]


def test_provider_failure_becomes_failed_job_and_document_untouched(setup, prov, monkeypatch):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    monkeypatch.setattr(regeneration.time, "sleep", lambda s: None)

    def boom(*a, **k):
        prov.bg += 1
        raise ImageProviderError("upstream 503")
    prov.generate_background = boom
    j = job(c, regen(c, d, "background").json()["id"])
    assert j["status"] == "failed" and "upstream 503" in j["error_message"] and prov.bg == regeneration.RETRIES + 1     # retried, then reported
    assert c.req("get", f"/creative-documents/{d['id']}").json()["revision"] == d["revision"]
    assert c.req("post", f"/regeneration-jobs/{j['id']}/accept", json={}).status_code == 409


def test_provider_not_configured_is_a_failed_job(setup, monkeypatch):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    from app.ai.image import ImageNotConfigured

    def nope():
        raise ImageNotConfigured("OPENAI_API_KEY must be set")
    factories.image_provider = nope
    j = job(c, regen(c, d, "background").json()["id"])
    assert j["status"] == "failed" and "OPENAI_API_KEY" in j["error_message"]


def test_regeneration_jobs_are_tenant_isolated(setup, make_client, prov):
    a, _, by = setup
    d = open_doc(a, by[(300, 250)])
    j = job(a, regen(a, d, "background").json()["id"])
    b = make_client()
    b.register("evil@t.example.com", org="Evil")
    assert b.req("get", f"/regeneration-jobs/{j['id']}").status_code == 404
    assert b.req("post", f"/regeneration-jobs/{j['id']}/accept", json={}).status_code == 404
    assert b.req("post", f"/regeneration-jobs/{j['id']}/reject").status_code == 404
    assert b.req("post", f"/creative-documents/{d['id']}/regenerate", json={"layer_id": "background", "prompt": "anything at all"}).status_code == 404


def test_cleanup_removes_only_unreferenced_candidates_and_keeps_version_assets(setup, prov):
    c, _, by = setup
    d = open_doc(c, by[(300, 250)])
    j = job(c, regen(c, d, "background", candidates=2).json()["id"])
    kept = j["candidates"][0]["asset_id"]
    nd = c.req("post", f"/regeneration-jobs/{j['id']}/accept", json={"candidate_index": 0}).json()["document"]
    c.req("post", f"/regeneration-jobs/{j['id']}/reject") if False else None
    j2 = job(c, regen(c, nd, "background", candidates=1).json()["id"])
    c.req("post", f"/regeneration-jobs/{j2['id']}/reject")
    # replace the accepted background with a solid colour: the old image is now only referenced by a version snapshot
    ops(c, nd, [cmd("CHANGE_BACKGROUND", background={"type": "solid", "color": "#101010"})])
    with SessionLocal() as db:
        set_org(db, uuid.UUID(c.org))
        n = regeneration.cleanup_candidates(db, uuid.UUID(c.org), older_than_s=-1)
        db.commit()
        from app.models import CreativeAsset
        alive = {str(a.id) for a in db.query(CreativeAsset).filter(CreativeAsset.kind == "candidate", CreativeAsset.deleted_at.is_(None))}
    assert j2["candidates"][0]["asset_id"] not in alive            # rejected, unreferenced -> cleaned
    assert j["candidates"][1]["asset_id"] not in alive and n >= 2
    assert kept in alive                                           # referenced by the AI-accept version snapshot -> kept
