"""Extract product data from HTML. Priority: JSON-LD Product > Open Graph > page text. Output is UNTRUSTED text:
it is length-capped and control-char stripped here, and must still be fenced as data when placed in prompts.
Nothing is invented: absent fields stay None/empty."""
import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(text, limit: int = 2000) -> str:
    if text is None:
        return ""
    return _CTRL.sub("", re.sub(r"\s+", " ", str(text))).strip()[:limit]


class ExtractedProduct(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    brand_name: str | None = None
    price: str | None = None
    currency: str | None = None
    features: list[str] = Field(default_factory=list)
    specifications: dict[str, str] = Field(default_factory=dict)
    variants: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    logo_url: str | None = None
    theme_color: str | None = None
    sources: dict[str, str] = Field(default_factory=dict)  # field -> where it came from


def _jsonld_blocks(soup: BeautifulSoup) -> list[dict]:
    out: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            out.append(node)
            walk(node.get("@graph", []))
    for tag in soup.find_all("script", type=lambda t: t and "ld+json" in t):
        try:
            walk(json.loads(tag.string or tag.get_text() or ""))
        except (ValueError, TypeError):
            continue
    return out


def _types(node: dict) -> list[str]:
    t = node.get("@type", [])
    return [t] if isinstance(t, str) else [str(x) for x in t]


def _as_list(v) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _img_url(v) -> str | None:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("url") or v.get("contentUrl")
    return None


def extract_product(html: str, base_url: str) -> ExtractedProduct:
    soup = BeautifulSoup(html, "html.parser")
    p = ExtractedProduct()
    src = p.sources

    def meta(*names) -> str | None:
        for n in names:
            t = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if t and t.get("content"):
                return clean(t["content"])
        return None

    # 1. JSON-LD
    products = [b for b in _jsonld_blocks(soup) if "Product" in _types(b)]
    if products:
        j = products[0]
        p.title, src["title"] = clean(j.get("name"), 300) or None, "json-ld"
        p.description = clean(j.get("description")) or None
        p.category = clean(j.get("category"), 200) or None
        brand = j.get("brand")
        p.brand_name = clean(brand.get("name") if isinstance(brand, dict) else brand, 200) or None
        offers = _as_list(j.get("offers"))
        if offers and isinstance(offers[0], dict):
            o = offers[0]
            if o.get("price") is None and isinstance(o.get("priceSpecification"), dict):
                o = o["priceSpecification"]
            if o.get("price") is not None:
                p.price, p.currency = clean(o["price"], 50), clean(o.get("priceCurrency"), 10) or None
                src["price"] = "json-ld"
            p.variants = [clean(x.get("name"), 200) for x in offers[1:] if isinstance(x, dict) and x.get("name")][:20]
        for im in _as_list(j.get("image")):
            u = _img_url(im)
            if u:
                p.image_urls.append(urljoin(base_url, u))
        for ap in _as_list(j.get("additionalProperty")):
            if isinstance(ap, dict) and ap.get("name") and ap.get("value") is not None:
                p.specifications[clean(ap["name"], 100)] = clean(ap["value"], 300)
        logo = _img_url(brand.get("logo")) if isinstance(brand, dict) else None
        if logo:
            p.logo_url = urljoin(base_url, logo)
        for k in ("description", "category", "brand_name"):
            if getattr(p, k):
                src[k] = "json-ld"
        if p.image_urls:
            src["images"] = "json-ld"

    # 2. Open Graph / meta
    if not p.title:
        p.title = meta("og:title", "twitter:title")
        if p.title:
            src["title"] = "open-graph"
    if not p.description:
        p.description = meta("og:description", "description", "twitter:description")
        if p.description:
            src["description"] = "open-graph"
    if not p.brand_name:
        p.brand_name = meta("og:site_name", "application-name")
        if p.brand_name:
            src["brand_name"] = "open-graph"
    if not p.price:
        pr = meta("product:price:amount", "og:price:amount")
        if pr:
            p.price, p.currency, src["price"] = pr, meta("product:price:currency", "og:price:currency"), "open-graph"
    og_img = meta("og:image", "twitter:image")
    if og_img:
        u = urljoin(base_url, og_img)
        if u not in p.image_urls:
            p.image_urls.append(u)
            src.setdefault("images", "open-graph")
    p.theme_color = meta("theme-color")

    # 3. Page text fallbacks
    if not p.title:
        h1 = soup.find("h1")
        t = clean(h1.get_text()) if h1 else clean(soup.title.string if soup.title else "")
        p.title = t or None
        if t:
            src["title"] = "page-text"
    for li in soup.select("ul li")[:60]:  # features: only from lists inside obvious feature/spec containers
        parent_attrs = " ".join(" ".join(a.get("class", []) + [a.get("id", "")]) for a in li.find_parents(limit=3)).lower()
        if re.search(r"feature|benefit|highlight|spec", parent_attrs):
            t = clean(li.get_text(), 300)
            if 3 < len(t) and t not in p.features:
                p.features.append(t)
    p.features = p.features[:12]

    if not p.logo_url:
        icon = soup.find("link", rel=lambda r: r and ("apple-touch-icon" in r or "icon" in r))
        if icon and icon.get("href"):
            p.logo_url = urljoin(base_url, icon["href"])
            src["logo"] = "page-icon"
    seen, imgs = set(), []
    for u in p.image_urls:
        if u not in seen and u.startswith(("http://", "https://")):
            seen.add(u)
            imgs.append(u)
    p.image_urls = imgs[:10]
    return p
