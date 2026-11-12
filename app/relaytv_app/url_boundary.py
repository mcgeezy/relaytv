# SPDX-License-Identifier: GPL-3.0-only
"""One parser for the hostname/port boundary.

Three places asked the same question about a URL and answered it three ways:
input validation (``resolver.validate_user_url``), public serialization
(``public_media.sanitize_public_url``), and provider classification
(``resolver.provider_from_url``). Two defects lived in the gap.

The first is that ``urlsplit`` does not validate a port. It accepts
``http://host:99999/a`` and ``http://host:abc/a`` happily; it is the ``.port``
accessor that raises ``ValueError``. Validation never touched ``.port``, so
those URLs were accepted at ingestion and blew up later in serialization —
turning one poisoned queue item into a permanent failure of ``/queue``,
``/status``, ``/history``, and every realtime snapshot, which survived a
restart because the value was on disk.

The second is that provider classification matched hosts by substring, so
``evil-youtu.com`` classified as YouTube and ``rumble.com.evil.net`` as Rumble.
``str.endswith`` alone is not enough either: ``"evilrumble.com".endswith(
"rumble.com")`` is True. Matching needs an explicit dot boundary.

Parsing here never raises. Callers decide what a malformed URL means: input
validation rejects it, serialization omits it, classification calls it
``other``.
"""

from dataclasses import dataclass
import ipaddress
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit


@dataclass(frozen=True)
class ParsedUrl:
    """The parts of a URL the app actually makes decisions on."""

    scheme: str
    hostname: str
    port: int | None
    # The netloc exactly as it appeared. Distinguishes "no authority at all"
    # (a relative reference, or file:///path) from "an authority that has no
    # usable hostname" (http://:80/a) — different inputs that callers treat
    # differently, and which a rebuilt netloc collapses together.
    raw_netloc: str
    path: str
    query: str
    fragment: str
    username: str
    password: str

    @property
    def netloc(self) -> str:
        """Rebuild the netloc from validated parts, dropping credentials.

        IPv6 literals are re-bracketed; ``hostname`` strips the brackets that
        make the address parseable.
        """
        if not self.hostname:
            return ""
        host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        if self.port is not None:
            return f"{host}:{self.port}"
        return host


def parse_url(value: object) -> ParsedUrl | None:
    """Parse a URL into canonical parts, or ``None`` when it is malformed.

    ``None`` means the value cannot be reasoned about: an unparseable netloc,
    or a port that is not an integer in 0-65535. It does *not* mean the URL is
    relative — a relative reference parses fine with an empty scheme and host,
    which is how upload paths like ``/media/uploads/...`` stay usable.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except Exception:
        return None
    try:
        # The accessor that validates. Both of these raise ValueError:
        # a non-numeric port, and one outside 0-65535.
        port = parts.port
        hostname = parts.hostname or ""
        username = parts.username or ""
        password = parts.password or ""
    except ValueError:
        return None
    return ParsedUrl(
        scheme=(parts.scheme or "").lower(),
        hostname=hostname.lower().rstrip("."),
        port=port,
        raw_netloc=parts.netloc or "",
        path=parts.path or "",
        query=parts.query or "",
        fragment=parts.fragment or "",
        username=username,
        password=password,
    )


def host_matches(host: str, domain: str) -> bool:
    """Return True when ``host`` is ``domain`` or a subdomain of it.

    The dot boundary is the whole point: a bare ``endswith`` matches
    ``evilrumble.com`` against ``rumble.com``, and a substring test matches
    ``rumble.com.evil.net``.
    """
    left = str(host or "").strip().lower().rstrip(".")
    right = str(domain or "").strip().lower().rstrip(".")
    if not left or not right:
        return False
    return left == right or left.endswith("." + right)


def host_matches_any(host: str, domains: tuple[str, ...]) -> bool:
    """Return True when ``host`` matches any of ``domains`` at a dot boundary."""
    return any(host_matches(host, domain) for domain in domains)


_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
_NON_CANONICAL_NUMERIC_HOST = re.compile(
    r"^(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*$"
)
_RFC2606_TEST_DOMAINS = (
    "example",
    "example.com",
    "example.net",
    "example.org",
    "invalid",
    "test",
)
_ORIG_URLOPEN = urllib.request.urlopen


def _is_forbidden_ip_obj(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        embedded: list[ipaddress.IPv4Address] = []
        if ip.ipv4_mapped is not None:
            embedded.append(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            embedded.append(ip.sixtofour)
        if ip.teredo is not None:
            embedded.extend(ip.teredo)
        if ip in _NAT64_WELL_KNOWN or ip in _NAT64_LOCAL:
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if int(ip) <= 0xFFFFFFFF and not ip.is_loopback and not ip.is_unspecified:
            # Deprecated IPv4-compatible IPv6 literal such as [::169.254.169.254].
            embedded.append(ipaddress.IPv4Address(int(ip)))
        if any(_is_forbidden_ip_obj(v4) for v4 in embedded):
            return True
    if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return True
    if isinstance(ip, ipaddress.IPv4Address) and ip.packed[0] == 0:
        return True
    return False


def is_forbidden_ip_literal(hostname: object) -> bool:
    """Return True when ``hostname`` is a forbidden or non-canonical IP literal.

    Private RFC1918 LAN addresses and loopback are intentionally permitted so
    local media servers (Jellyfin/Emby/Plex, IPTV proxies, NAS shares, and
    same-host relays) continue working while cloud metadata / link-local
    (169.254.0.0/16, fe80::/10), unspecified (0.0.0.0/8, ::), multicast,
    NAT64/6to4/Teredo/IPv4-compatible wrappers, and non-canonical BSD
    ``inet_aton`` IPv4 forms (hex, octal, integer, shorthand) are rejected.
    """
    raw = str(hostname or "").strip().lower().rstrip(".")
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if not raw:
        return False
    if "%" in raw:
        raw = raw.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        if _NON_CANONICAL_NUMERIC_HOST.fullmatch(raw):
            return True
        try:
            socket.inet_aton(raw)
        except OSError:
            return False
        return True
    return _is_forbidden_ip_obj(ip)


def has_forbidden_resolved_address(hostname: object) -> bool:
    """Return True when ``hostname`` is or resolves in DNS to a forbidden IP."""
    raw = str(hostname or "").strip().lower().rstrip(".")
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if not raw:
        return False
    if is_forbidden_ip_literal(raw):
        return True
    addr_candidate = raw.split("%", 1)[0]
    try:
        ipaddress.ip_address(addr_candidate)
        return False
    except ValueError:
        pass
    if host_matches_any(raw, _RFC2606_TEST_DOMAINS):
        return False
    try:
        infos = socket.getaddrinfo(raw, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for _family, _socktype, _proto, _canon, sockaddr in infos:
        if not sockaddr:
            continue
        if is_forbidden_ip_literal(sockaddr[0]):
            return True
    return False


def is_allowed_outbound_http_url(value: object) -> bool:
    """Return True when ``value`` is an http(s) URL with a permitted host and DNS target."""
    parsed = parse_url(value)
    if parsed is None or parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if is_forbidden_ip_literal(parsed.hostname):
        return False
    if has_forbidden_resolved_address(parsed.hostname):
        return False
    return True


class SafeHttpRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject HTTP redirects to non-http(s) schemes or forbidden IP/DNS targets."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        resolved = urljoin(req.full_url, str(newurl or ""))
        if not is_allowed_outbound_http_url(resolved):
            raise urllib.error.URLError(f"Blocked unsafe redirect target: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def safe_urlopen(url_or_req, *, timeout: float = 15.0):
    """Open an http(s) URL after validating its host, DNS resolution, and redirects."""
    raw_url = (
        url_or_req.full_url
        if isinstance(url_or_req, urllib.request.Request)
        else str(url_or_req or "")
    )
    if not is_allowed_outbound_http_url(raw_url):
        raise urllib.error.URLError("Blocked unsafe URL target")
    if urllib.request.urlopen is not _ORIG_URLOPEN:
        return urllib.request.urlopen(url_or_req, timeout=timeout)
    opener = urllib.request.build_opener(SafeHttpRedirectHandler)
    return opener.open(url_or_req, timeout=timeout)
