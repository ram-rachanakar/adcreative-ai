"""Deterministic claim guard. Flags marketing copy containing facts that the source data does not support.
This is a safety net, not proof of truthfulness: flagged items must be reviewed by a human."""
import re

NUM = re.compile(r"\d+(?:[.,]\d+)?")
RISKY = re.compile(
    r"free\s+(shipping|delivery|returns?|trial|gift)|money[- ]back|guarantee[sd]?|warrant(y|ied)|certified|award[- ]?winning|awards?"
    r"|best[- ]?sell(er|ing)|#\s?1|number\s+one|top[- ]rated|rated|\bstars?\b|reviews?|testimonials?|clinically|scientifically|proven"
    r"|doctor|dermatologist|limited[- ]time|while\s+supplies|selling\s+fast|almost\s+gone|\bsale\b|discount|\d+\s*%\s*off|coupon|\bdeals?\b",
    re.I)
QUOTED = re.compile(r"[\"“”‘’«»][^\"“”‘’«»]{6,}[\"“”‘’«»]")


def source_text(product: dict, advertiser: dict) -> str:
    parts: list[str] = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, (int, float)):
            parts.append(str(v))
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for k, x in v.items():
                parts.append(str(k))
                walk(x)
    walk(product)
    walk(advertiser)
    return " ".join(parts).lower().replace(",", ".")


def check_copy(fields: dict[str, str], src: str) -> list[dict]:
    """fields: name -> text. Returns [{field, text, rule}] for each unsupported item."""
    out = []
    for name, text in fields.items():
        if not text:
            continue
        low = text.lower()
        for m in NUM.finditer(low):
            if m.group(0).replace(",", ".") not in src:
                out.append({"field": name, "text": m.group(0), "rule": "number not found in source data"})
        for m in RISKY.finditer(text):
            if m.group(0).lower() not in src:
                out.append({"field": name, "text": m.group(0), "rule": "risky claim not found in source data"})
        for m in QUOTED.finditer(text):
            if m.group(0).strip("\"“”‘’«»").lower() not in src:
                out.append({"field": name, "text": m.group(0)[:60], "rule": "quoted text (possible testimonial) not found in source data"})
    return out


def check_concept(c: dict, src: str) -> list[dict]:
    fields = {"headline": c["headline"], "supporting_copy": c["supporting_copy"], "cta": c["cta"]}
    fields.update({f"alternative_headlines[{i}]": h for i, h in enumerate(c.get("alternative_headlines", []))})
    return check_copy(fields, src)
