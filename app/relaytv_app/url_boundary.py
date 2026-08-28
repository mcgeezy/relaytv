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
from urllib.parse import urlsplit


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
