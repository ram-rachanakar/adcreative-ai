"""Deterministic offline provider for tests/dev. Builds output ONLY from facts in `context` (no invented claims).
It is not an AI and its copy is deliberately plain."""
from app.ai.llm import LLMProvider, LLMRequest, LLMResult
from app.marketing.schemas import Approach, Brief, ConceptSet

ANGLES = {
    Approach.problem_solution: "Name a everyday annoyance, then present the product as the answer",
    Approach.benefit_focused: "Lead with the single clearest benefit",
    Approach.premium: "Quiet confidence and craftsmanship",
    Approach.lifestyle: "Show the product in the moment it is used",
    Approach.promotional: "Direct, offer-led message using only the offer supplied by the advertiser",
    Approach.feature_highlight: "Spotlight one concrete feature",
    Approach.emotional_storytelling: "A small human story that the product enables",
    Approach.seasonal: "Tie the product to the current season without implying discounts",
    Approach.minimalist: "Few words, lots of space",
    Approach.product_innovation: "Present what is new about the product",
}


class MockLLM(LLMProvider):
    name, model = "mock", "mock-1"

    def generate_json(self, req: LLMRequest) -> LLMResult:
        c = req.context
        if req.task == "vision_quality":   # a mock cannot look at pixels: say so instead of inventing an opinion
            from app.quality.vision import VisionAssessment
            return LLMResult(VisionAssessment(assessed=False, summary="Mock provider: no visual assessment was performed."), self.name, self.model, 0, 0)
        if req.task == "copy_alternatives":   # deterministic: rearranges ONLY facts supplied in the context
            cur = (c.get("current") or "").strip().lower()
            pool = [*(c.get("facts") or []), c.get("title") or ""]
            alts = [t[: c.get("max_chars", 80)] for t in dict.fromkeys(x.strip() for x in pool) if t and t.lower() != cur][: c.get("n", 3)]
            return LLMResult(req.schema.model_validate({"alternatives": alts or [(c.get("current") or "")[: c.get("max_chars", 80)]]}), self.name, self.model, len(req.user) // 4, 20)
        out = self._concepts(c) if req.task == "concepts" else self._brief(c)
        return LLMResult(req.schema.model_validate(out), self.name, self.model, len(req.user) // 4, len(str(out)) // 4)

    def _concepts(self, c: dict) -> dict:
        p, title = c["product"], (c["product"].get("title") or "the product")
        feats = p.get("features") or []
        desc = (p.get("description") or "").split(".")[0].strip()
        palette = (c.get("brand_colors") or [])[:3]
        palette = palette + ["#111111", "#f5f5f5"] if len(palette) < 2 else palette
        concepts = []
        for i, ap in enumerate(c["approaches"]):
            feat = feats[i % len(feats)] if feats else desc
            headline = (feat or title)[:80]
            concepts.append({
                "name": f"{ap.value.replace('_', ' ').title()} · {title}"[:80], "approach": ap.value,
                "advertising_angle": ANGLES[ap], "target_audience": c.get("audience") or "General audience (assumed)",
                "marketing_objective": c.get("objective") or "Drive awareness (assumed)",
                "headline": headline, "alternative_headlines": [title[:80]],
                "supporting_copy": (desc or title)[:220], "cta": "Learn more",
                "emotional_appeal": f"{c.get('tone', 'professional')} tone", "visual_direction": f"Clean scene featuring {title}",
                "background_description": f"Soft, uncluttered {c.get('tone', 'professional')} backdrop",
                "product_positioning": desc[:300] or title, "color_palette": palette[:6],
                "typography": {"headline": "bold sans-serif, large", "body": "regular sans-serif", "cta": "semibold sans-serif, uppercase"},
                "claims_used": [{"text": headline, "source_field": "features" if feats else "description"}],
                "assumptions": [] if c.get("audience") else ["Target audience was not provided"],
            })
        return {"concepts": concepts}

    def _brief(self, c: dict) -> dict:
        cn = c["concept"]
        return {"objective": cn["marketing_objective"], "key_message": cn["headline"],
                "background_prompt": cn["background_description"], "composition_notes": "Leave a calm area for text and the product",
                "mood": cn["emotional_appeal"], "color_palette": cn["color_palette"], "layout_preferences": ["text-left product-right"],
                "must_avoid": []}
