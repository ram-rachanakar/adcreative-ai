import json

from app.scraping.extractor import extract_product

LD = {"@context": "https://schema.org", "@graph": [{"@type": "WebSite", "name": "Shop"}, {
    "@type": "Product", "name": "AeroBottle 750", "description": "Insulated  bottle.\x00", "category": "Drinkware",
    "brand": {"@type": "Brand", "name": "Aero", "logo": "/logo.png"},
    "image": ["/img/1.jpg", {"@type": "ImageObject", "url": "https://cdn.x.com/2.jpg"}],
    "offers": {"@type": "Offer", "price": "29.90", "priceCurrency": "EUR"},
    "additionalProperty": [{"name": "Capacity", "value": "750 ml"}]}]}


def page(extra_head="", body=""):
    return f"<html><head>{extra_head}</head><body>{body}</body></html>"


def test_jsonld_priority_and_cleaning():
    html = page(f'<script type="application/ld+json">{json.dumps(LD)}</script>'
                '<meta property="og:title" content="OG Title">')
    p = extract_product(html, "https://shop.com/p/1")
    assert p.title == "AeroBottle 750" and p.sources["title"] == "json-ld"
    assert p.description == "Insulated bottle."  # control char + double space cleaned
    assert (p.price, p.currency, p.brand_name, p.category) == ("29.90", "EUR", "Aero", "Drinkware")
    assert p.image_urls == ["https://shop.com/img/1.jpg", "https://cdn.x.com/2.jpg"]
    assert p.logo_url == "https://shop.com/logo.png" and p.specifications == {"Capacity": "750 ml"}


def test_open_graph_fallback():
    html = page('<meta property="og:title" content="Lamp"><meta property="og:description" content="Warm light">'
                '<meta property="og:image" content="/l.png"><meta property="product:price:amount" content="10">'
                '<meta property="og:site_name" content="Lumo"><meta name="theme-color" content="#112233">')
    p = extract_product(html, "https://lumo.com/lamp")
    assert (p.title, p.description, p.brand_name, p.price, p.theme_color) == ("Lamp", "Warm light", "Lumo", "10", "#112233")
    assert p.image_urls == ["https://lumo.com/l.png"] and p.sources["title"] == "open-graph"


def test_text_fallback_and_no_fabrication():
    p = extract_product(page(body="<h1>Plain Thing</h1>"), "https://a.com/")
    assert p.title == "Plain Thing" and p.sources["title"] == "page-text"
    assert p.price is None and p.description is None and p.features == [] and p.image_urls == []


def test_malformed_jsonld_ignored_and_features_scoped():
    html = page('<script type="application/ld+json">{broken</script>',
                '<ul class="nav"><li>Home</li></ul><div class="product-features"><ul><li>Dishwasher safe</li></ul></div>')
    p = extract_product(html, "https://a.com/")
    assert p.features == ["Dishwasher safe"]


def test_non_http_image_urls_dropped():
    html = page('<meta property="og:image" content="javascript:alert(1)"><meta property="og:image" content="data:image/png;base64,AA">')
    assert extract_product(html, "https://a.com/").image_urls == []
