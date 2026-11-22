import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import LLMError, LLMProvider, LLMRequest
from app.core.config import get_settings
from app.marketing import claims, prompts
from app.marketing.schemas import TONES, Approach, Brief, Concept, ConceptSet
from app.models import BrandProfile, Campaign, CreativeBrief, CreativeConcept, Product, UsageRecord
from app.repositories.org_repo import OrgRepo

PRODUCT_FIELDS = ("title", "description", "category", "brand_name", "price", "currency", "features", "specifications", "variants", "user_description")
DEFAULT_ORDER = [Approach.benefit_focused, Approach.problem_solution, Approach.premium, Approach.lifestyle, Approach.feature_highlight,
                 Approach.minimalist, Approach.emotional_storytelling, Approach.product_innovation, Approach.promotional, Approach.seasonal]


class Conflict(Exception):
    pass


def build_context(db: Session, org_id: uuid.UUID, campaign: Campaign) -> dict:
    prods = OrgRepo(db, Product, org_id).list(campaign_id=campaign.id)
    if not prods:
        raise Conflict("Analyze the product first")
    flags: list[str] = []
    product = prompts.sanitize_untrusted({k: prods[0].data.get(k) for k in PRODUCT_FIELDS if prods[0].data.get(k)}, flags, "product")
    b = campaign.brief or {}
    advertiser = prompts.sanitize_untrusted({k: b[k] for k in ("audience", "objective", "offer", "language") if b.get(k)}, flags, "advertiser")
    brand: dict = {}
    if b.get("brand_profile_id"):
        bp = OrgRepo(db, BrandProfile, org_id).get(uuid.UUID(b["brand_profile_id"]))
        if bp:
            eff = {**bp.detected, **bp.overrides}
            brand = {"name": eff.get("name"), "colors": eff.get("colors") or [], "fonts": eff.get("fonts"), "tone": eff.get("tone")}
            if bp.guidelines:
                brand["guidelines"] = prompts.sanitize_untrusted(bp.guidelines[:2000], flags, "brand.guidelines")
    tone = b.get("tone") if b.get("tone") in TONES else "professional"
    return {"product": product, "advertiser": advertiser, "brand": brand, "tone": tone,
            "language": b.get("language") or "English", "injection_flags": flags,
            "src": claims.source_text(product, advertiser)}


def record_usage(db: Session, org_id: uuid.UUID, campaign_id, kind: str, res, **meta) -> UsageRecord:
    s = get_settings()
    priced = bool(s.llm_input_cost_per_mtok or s.llm_output_cost_per_mtok) and res.provider != "mock"
    cost = (res.input_tokens * s.llm_input_cost_per_mtok + res.output_tokens * s.llm_output_cost_per_mtok) / 1e6 if priced else 0
    return OrgRepo(db, UsageRecord, org_id).add(campaign_id=campaign_id, kind=kind, provider=res.provider, model=res.model,
                                                 input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                                                 cost_usd=cost, priced=priced, meta={"attempts": res.attempts, **meta})


def pick_approaches(count: int, requested: list[Approach] | None, has_offer: bool) -> list[Approach]:
    if requested:
        chosen = list(dict.fromkeys(requested))
        if len(chosen) < count:
            chosen += [a for a in DEFAULT_ORDER if a not in chosen][:count - len(chosen)]
        return chosen
    order = [a for a in DEFAULT_ORDER if has_offer or a not in (Approach.promotional, Approach.seasonal)]
    order += [a for a in DEFAULT_ORDER if a not in order]  # only reached for count > 8
    return order[:count]


def _ask(llm: LLMProvider, ctx: dict, approaches: list[Approach], feedback: str = "") -> tuple[ConceptSet, object]:
    user = prompts.concepts_user_prompt(ctx["product"], ctx["advertiser"], ctx["brand"], [a.value for a in approaches], ctx["tone"], ctx["language"])
    if feedback:
        user += f"\n\nYour previous attempt contained claims not supported by the data. Remove or replace them:\n{feedback}"
    res = llm.generate_json(LLMRequest("concepts", prompts.SYSTEM_CONCEPTS, user, ConceptSet, prompts.CONCEPTS_VERSION,
                                       {"product": ctx["product"], "brand_colors": ctx["brand"].get("colors"), "audience": ctx["advertiser"].get("audience"),
                                        "objective": ctx["advertiser"].get("objective"), "tone": ctx["tone"], "approaches": approaches}))
    return res.parsed, res


def generate_concepts(db: Session, org_id: uuid.UUID, campaign: Campaign, llm: LLMProvider, count: int = 5,
                      requested: list[Approach] | None = None) -> list[CreativeConcept]:
    ctx = build_context(db, org_id, campaign)
    approaches = pick_approaches(count, requested, bool(ctx["advertiser"].get("offer")))
    parsed, res = _ask(llm, ctx, approaches)
    record_usage(db, org_id, campaign.id, "llm_concepts", res, pass_=1)
    by_ap: dict[str, tuple[Concept, list]] = {}
    for c in parsed.concepts:
        if c.approach in approaches and c.approach.value not in by_ap:
            by_ap[c.approach.value] = (c, claims.check_concept(c.model_dump(), ctx["src"]))
    flagged = [a for a in approaches if a.value in by_ap and by_ap[a.value][1]]
    if flagged:  # one corrective pass, only for the offending approaches
        fb = "\n".join(f"- {a.value}: " + "; ".join(f"{v['field']} '{v['text']}' ({v['rule']})" for v in by_ap[a.value][1]) for a in flagged)
        p2, res2 = _ask(llm, ctx, flagged, fb)
        record_usage(db, org_id, campaign.id, "llm_concepts", res2, pass_=2)
        for c in p2.concepts:
            if c.approach.value in {a.value for a in flagged} and c.approach.value in by_ap:
                by_ap[c.approach.value] = (c, claims.check_concept(c.model_dump(), ctx["src"]))
    missing = [a.value for a in approaches if a.value not in by_ap]
    if len(by_ap) < min(count, 5) or len(by_ap) < len(approaches) - len(missing):
        raise LLMError(f"Model returned too few usable concepts (missing: {missing})")

    repo = OrgRepo(db, CreativeConcept, org_id)
    for old in repo.list(campaign_id=campaign.id):
        if not old.selected:
            repo.soft_delete(old)
    out = []
    for a in approaches:
        if a.value not in by_ap:
            continue
        c, viol = by_ap[a.value]
        out.append(repo.add(campaign_id=campaign.id, approach=a.value, data=c.model_dump(mode="json"), flags=viol,
                            status="flagged" if viol else "ok", prompt_version=prompts.CONCEPTS_VERSION, provider=res.provider, model=res.model))
    campaign.status = "concepts_ready"
    if ctx["injection_flags"]:
        campaign.brief = {**campaign.brief, "injection_flags": ctx["injection_flags"]}
    db.flush()
    return out


def recheck_concept(db: Session, org_id: uuid.UUID, campaign: Campaign, concept: CreativeConcept) -> None:
    ctx = build_context(db, org_id, campaign)
    viol = claims.check_concept(concept.data, ctx["src"])
    concept.flags = viol
    concept.status = "flagged" if viol else "ok"


def generate_briefs(db: Session, org_id: uuid.UUID, campaign: Campaign, llm: LLMProvider) -> list[CreativeBrief]:
    repo = OrgRepo(db, CreativeConcept, org_id)
    selected = [c for c in repo.list(campaign_id=campaign.id) if c.selected]
    if not selected:
        raise Conflict("Select at least one concept first")
    ctx = build_context(db, org_id, campaign)
    briefs = OrgRepo(db, CreativeBrief, org_id)
    out = []
    for c in selected:
        user = prompts.brief_user_prompt(c.data, ctx["brand"], ctx["language"])
        res = llm.generate_json(LLMRequest("brief", prompts.SYSTEM_BRIEF, user, Brief, prompts.BRIEF_VERSION, {"concept": c.data}))
        record_usage(db, org_id, campaign.id, "llm_brief", res, concept_id=str(c.id))
        data = res.parsed.model_dump(mode="json")
        data["background_prompt_final"] = f"{data['background_prompt'].rstrip('. ')}. {prompts.BG_SUFFIX}"  # constraints appended by code, not the model
        data["concept_id"] = str(c.id)
        for old in briefs.list(concept_id=c.id):
            briefs.soft_delete(old)
        out.append(briefs.add(concept_id=c.id, data=data, prompt_version=prompts.BRIEF_VERSION, provider=res.provider, model=res.model))
    campaign.status = "briefed"
    db.flush()
    return out
