"""Live-provider smoke test. Makes REAL, billable API calls (a few cents). Not part of CI.

    cd backend && .venv/bin/python scripts/live_smoke.py [llm] [vision] [image]

Needs LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY + LLM_MODEL, and/or IMAGE_PROVIDER=openai + OPENAI_API_KEY + IMAGE_MODEL
(from the environment or ../.env). Each check is SKIPPED (not failed) when its provider is not configured.
Exit code 1 if any configured check fails."""
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from app.ai.image import get_image_provider
from app.ai.llm import LLMRequest, get_llm_provider
from app.core.config import get_settings
from app.marketing import prompts
from app.marketing.schemas import ConceptSet
from app.quality import vision

PRODUCT = {"name": "Trailblazer Insulated Bottle", "description": "Stainless steel 750ml bottle, keeps drinks cold 24h.",
           "price": "29.00 USD", "category": "outdoor"}
ADVERTISER = {"name": "Acme Outdoors", "url": "https://example.com"}
BRAND = {"colors": ["#1F6F5C", "#F2B134"], "tone": "friendly"}


def check_llm(s):
    llm = get_llm_provider()
    if llm.name == "mock":
        return "skip", "LLM_PROVIDER is not 'anthropic'"
    user = prompts.concepts_user_prompt(PRODUCT, ADVERTISER, BRAND, ["benefit_led", "emotional"], "friendly", "en")
    r = llm.generate_json(LLMRequest(task="concepts", system=prompts.SYSTEM_CONCEPTS, user=user, schema=ConceptSet,
                                     prompt_version=prompts.CONCEPTS_VERSION, max_tokens=3000))
    assert r.parsed.concepts, "no concepts"
    return "ok", f"{llm.model}: {len(r.parsed.concepts)} concepts, {r.input_tokens}in/{r.output_tokens}out, attempts={r.attempts}; first={r.parsed.concepts[0].model_dump().get('headline')!r}"


def check_vision(s):
    llm = get_llm_provider()
    if llm.name == "mock":
        return "skip", "LLM_PROVIDER is not 'anthropic'"
    im = Image.new("RGB", (728, 90), "#1F6F5C")
    d = ImageDraw.Draw(im)
    d.text((20, 35), "Cold for 24 hours", fill="white")
    d.rectangle((600, 25, 710, 65), fill="#F2B134")
    d.text((625, 38), "Shop now", fill="black")
    buf = io.BytesIO(); im.save(buf, "PNG")
    r = vision.assess(llm, buf.getvalue(), width=728, height=90, placements=["leaderboard"], brand_colors=BRAND["colors"],
                      tone="friendly", headline="Cold for 24 hours", cta="Shop now")
    a = r.parsed
    assert a.assessed and vision.vision_score(a) is not None, "model returned no scores"
    return "ok", f"{llm.model}: score={vision.vision_score(a)}, issues={[i.code for i in a.issues]}, attempts={r.attempts}"


def check_image(s):
    if s.image_provider == "mock":
        return "skip", "IMAGE_PROVIDER is not 'openai'"
    p = get_image_provider()
    r = p.generate_background("Soft abstract teal gradient with subtle light rays, studio backdrop", 1200, 628)
    im = Image.open(io.BytesIO(r.data)); im.load()
    return "ok", f"{r.model}: {im.size} {im.format}, {len(r.data)//1024} KB, {r.duration_ms} ms, requested size {r.params.get('size')}"


def main():
    s = get_settings()
    wanted = set(sys.argv[1:]) or {"llm", "vision", "image"}
    failed = False
    for name, fn in (("llm", check_llm), ("vision", check_vision), ("image", check_image)):
        if name not in wanted:
            continue
        t = time.time()
        try:
            status, msg = fn(s)
        except Exception as e:   # report the class only; never echo bodies/keys
            status, msg, failed = "FAIL", f"{type(e).__name__}: {str(e)[:300]}", True
        print(f"[{status:4}] {name:6} ({time.time()-t:.1f}s) {msg}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
