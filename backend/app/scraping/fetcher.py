"""SSRF-safe HTTP fetcher.

Defenses: http/https + ports 80/443 only; no URL credentials; every hop (incl. redirects) is DNS-resolved and
ALL resolved addresses must be globally routable; the connection is then PINNED to the validated IP (Host header +
TLS SNI keep the original name), so a second DNS answer (rebinding) can never be used; redirect cap; byte cap
enforced while streaming (post-decompression); MIME allowlist; timeouts.
"""
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata", "instance-data"}
BLOCKED_SUFFIXES = (".localhost", ".internal", ".local", ".localdomain", ".lan", ".home.arpa")
ALLOWED_PORTS = {80, 443}


class UnsafeURLError(Exception):
    pass


class FetchError(Exception):
    pass


def default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise FetchError(f"DNS resolution failed for {host}") from e
    return list({i[4][0] for i in infos})


def assert_public_ip(addr: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(addr.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip.sixtofour:
            ip = ip.sixtofour
        elif ip.teredo:
            raise UnsafeURLError("Teredo addresses are not allowed")
    if not ip.is_global or ip.is_multicast:
        raise UnsafeURLError(f"Address {addr} is not publicly routable")
    return ip


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes


class SafeFetcher:
    def __init__(self, resolver=default_resolver, transport: httpx.BaseTransport | None = None,
                 timeout: float = 10.0, max_redirects: int = 5, user_agent: str = "AdCreativeBot/1.0"):
        self.resolver, self.transport = resolver, transport
        self.timeout, self.max_redirects, self.ua = timeout, max_redirects, user_agent

    def validate(self, url: str) -> tuple[str, str, int, str]:
        """Returns (scheme, host, port, pinned_ip) or raises UnsafeURLError."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise UnsafeURLError("Only http and https URLs are allowed")
        if parts.username or parts.password:
            raise UnsafeURLError("Credentials in URLs are not allowed")
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            raise UnsafeURLError("URL has no host")
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            raise UnsafeURLError("Invalid port")
        if port not in ALLOWED_PORTS:
            raise UnsafeURLError(f"Port {port} is not allowed")
        if host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES):
            raise UnsafeURLError("Host is not allowed")
        if not _is_ip(host) and all(re.fullmatch(r"0x[0-9a-f]+|[0-9]+", lab) for lab in host.split(".")):
            raise UnsafeURLError("Numeric host encodings are not allowed")  # 2130706433, 0x7f.1, 0177.0.0.1
        addrs = [host] if _is_ip(host) else self.resolver(host)
        if not addrs:
            raise FetchError("Host did not resolve")
        for a in addrs:  # every answer must be public, not just the first
            assert_public_ip(a)
        return parts.scheme, host, port, addrs[0]

    def fetch(self, url: str, *, allowed_mime: tuple[str, ...], max_bytes: int) -> FetchResult:
        current = url
        for _ in range(self.max_redirects + 1):
            scheme, host, port, ip = self.validate(current)
            parts = urlsplit(current)
            ip_host = f"[{ip}]" if ":" in ip else ip
            default_port = 443 if scheme == "https" else 80
            netloc = ip_host if port == default_port else f"{ip_host}:{port}"
            pinned = parts._replace(netloc=netloc).geturl()
            host_header = host if port == default_port else f"{host}:{port}"
            headers = {"Host": host_header, "User-Agent": self.ua, "Accept": ", ".join(allowed_mime) + ",*/*;q=0.1"}
            ext = {"sni_hostname": host} if scheme == "https" else {}
            with httpx.Client(transport=self.transport, timeout=self.timeout, follow_redirects=False) as c:
                try:
                    with c.stream("GET", pinned, headers=headers, extensions=ext) as r:
                        if r.status_code in (301, 302, 303, 307, 308):
                            loc = r.headers.get("location")
                            if not loc:
                                raise FetchError("Redirect without Location")
                            current = urljoin(current, loc)  # re-validated at top of loop
                            continue
                        if r.status_code >= 400:
                            raise FetchError(f"Remote server returned {r.status_code}")
                        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                        if ctype not in allowed_mime:
                            raise FetchError(f"Content type '{ctype}' is not allowed")
                        cl = r.headers.get("content-length")
                        if cl and cl.isdigit() and int(cl) > max_bytes:
                            raise FetchError("Response too large")
                        buf = bytearray()
                        for chunk in r.iter_bytes():
                            buf += chunk
                            if len(buf) > max_bytes:
                                raise FetchError("Response too large")
                        return FetchResult(current, r.status_code, ctype, bytes(buf))
                except httpx.TimeoutException as e:
                    raise FetchError("Request timed out") from e
                except httpx.HTTPError as e:
                    raise FetchError(f"Network error: {type(e).__name__}") from e
        raise FetchError("Too many redirects")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
