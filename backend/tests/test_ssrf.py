import httpx
import pytest

from app.scraping.fetcher import FetchError, SafeFetcher, UnsafeURLError

PUBLIC = "93.184.216.34"
HTML = ("text/html",)


def fetcher(handler, resolver=lambda h: [PUBLIC], **kw):
    return SafeFetcher(resolver=resolver, transport=httpx.MockTransport(handler), **kw)


def ok(request):
    return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")


@pytest.mark.parametrize("url", [
    "http://localhost/", "http://127.0.0.1/", "http://[::1]/", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://172.16.0.1/", "http://169.254.169.254/latest/meta-data/", "http://0.0.0.0/", "http://100.64.0.1/",
    "http://[::ffff:127.0.0.1]/", "http://[fe80::1]/", "http://[fd00::1]/", "http://metadata.google.internal/",
    "http://service.internal/", "http://foo.localhost/", "file:///etc/passwd", "ftp://example.com/", "gopher://x/",
    "http://user:pw@example.com/", "http://example.com:22/", "http://example.com:6379/", "http://2130706433/",
])
def test_blocked_urls(url):
    with pytest.raises((UnsafeURLError, FetchError)):
        fetcher(ok).fetch(url, allowed_mime=HTML, max_bytes=1000)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "::ffff:10.0.0.1"])
def test_dns_resolving_to_private_blocked(ip):
    with pytest.raises(UnsafeURLError):
        fetcher(ok, resolver=lambda h: [ip]).fetch("http://evil.example.com/", allowed_mime=HTML, max_bytes=1000)


def test_any_private_answer_among_many_blocks():
    with pytest.raises(UnsafeURLError):
        fetcher(ok, resolver=lambda h: [PUBLIC, "10.0.0.1"]).fetch("http://mixed.example.com/", allowed_mime=HTML, max_bytes=1000)


def test_connection_is_pinned_to_validated_ip():
    seen = {}

    def h(request):
        seen["host"], seen["hdr"] = request.url.host, request.headers["host"]
        return ok(request)
    fetcher(h).fetch("https://shop.example.com/p", allowed_mime=HTML, max_bytes=1000)
    assert seen == {"host": PUBLIC, "hdr": "shop.example.com"}  # never re-resolves the name


def test_redirect_to_internal_is_blocked():
    def h(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    with pytest.raises(UnsafeURLError):
        fetcher(h).fetch("http://example.com/", allowed_mime=HTML, max_bytes=1000)


def test_redirect_hostname_resolving_private_is_blocked():
    calls = []

    def resolver(host):
        calls.append(host)
        return [PUBLIC] if host == "example.com" else ["192.168.0.9"]

    def h(request):
        return httpx.Response(301, headers={"location": "http://sneaky.example.org/x"})
    with pytest.raises(UnsafeURLError):
        fetcher(h, resolver=resolver).fetch("http://example.com/", allowed_mime=HTML, max_bytes=1000)
    assert calls == ["example.com", "sneaky.example.org"]


def test_redirect_limit():
    def h(request):
        return httpx.Response(302, headers={"location": "/again"})
    with pytest.raises(FetchError, match="redirects"):
        fetcher(h, max_redirects=3).fetch("http://example.com/", allowed_mime=HTML, max_bytes=1000)


def test_relative_redirect_followed():
    def h(request):
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        return ok(request)
    r = fetcher(h).fetch("http://example.com/a", allowed_mime=HTML, max_bytes=1000)
    assert r.url == "http://example.com/b"


def test_size_limit_declared_and_streamed():
    big = httpx.Response(200, headers={"content-type": "text/html", "content-length": "99999"}, content=b"x")
    with pytest.raises(FetchError, match="large"):
        fetcher(lambda r: big).fetch("http://example.com/", allowed_mime=HTML, max_bytes=100)
    streamed = lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 5000)  # noqa: E731
    with pytest.raises(FetchError, match="large"):
        fetcher(streamed).fetch("http://example.com/", allowed_mime=HTML, max_bytes=100)


def test_mime_allowlist_and_http_errors():
    pdf = lambda r: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")  # noqa: E731
    with pytest.raises(FetchError, match="not allowed"):
        fetcher(pdf).fetch("http://example.com/", allowed_mime=HTML, max_bytes=1000)
    with pytest.raises(FetchError, match="404"):
        fetcher(lambda r: httpx.Response(404)).fetch("http://example.com/", allowed_mime=HTML, max_bytes=1000)


def test_timeout_mapped():
    def h(request):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(FetchError, match="timed out"):
        fetcher(h).fetch("http://example.com/", allowed_mime=HTML, max_bytes=1000)
