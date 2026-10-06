# SPDX-License-Identifier: GPL-3.0-only
"""One parser for the hostname/port boundary (F03, F14).

Input validation and public serialization used different parsers, and provider
classification used substrings. Two defects lived in the gap: a malformed port
was accepted at ingestion and raised at serialization, and lookalike domains
classified as real providers.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from relaytv_app import public_media, state, url_boundary
from relaytv_app.main import create_app
from relaytv_app.resolver import (
    is_youtube_url,
    provider_from_url,
    validate_user_url,
    youtube_id_from_url,
)


# --- the parser --------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://host:99999/a",   # port out of range
        "http://host:abc/a",     # port not an integer
        "http://host:-1/a",
        "http://[::1/a",         # unterminated IPv6 literal
    ],
)
def test_malformed_urls_do_not_parse(url: str) -> None:
    assert url_boundary.parse_url(url) is None


def test_parser_never_raises_on_hostile_input() -> None:
    for value in ("", None, 0, [], "http://" + "a" * 5000, "::::", "http://:::80/a"):
        url_boundary.parse_url(value)  # must not raise


def test_relative_and_ipv6_urls_parse() -> None:
    relative = url_boundary.parse_url("/media/uploads/u_1/a.mp4")
    assert relative is not None
    assert relative.scheme == "" and relative.hostname == ""

    ipv6 = url_boundary.parse_url("http://[::1]:8080/a")
    assert ipv6 is not None
    assert ipv6.hostname == "::1"
    assert ipv6.port == 8080
    # Re-bracketed when rebuilt, or the URL stops being parseable.
    assert ipv6.netloc == "[::1]:8080"


@pytest.mark.parametrize(
    "host,domain,expected",
    [
        ("youtube.com", "youtube.com", True),
        ("www.youtube.com", "youtube.com", True),
        ("m.youtube.com", "youtube.com", True),
        ("YouTube.COM", "youtube.com", True),
        ("youtube.com.", "youtube.com", True),
        # The two shapes the old matcher got wrong.
        ("youtube.com.phish.co", "youtube.com", False),
        ("evil-youtube.com", "youtube.com", False),
        # endswith alone is not enough: this has no dot boundary.
        ("evilrumble.com", "rumble.com", False),
        ("", "youtube.com", False),
        ("youtube.com", "", False),
    ],
)
def test_host_matches_requires_a_dot_boundary(host, domain, expected) -> None:
    assert url_boundary.host_matches(host, domain) is expected


# --- F03: ingestion rejects what serialization cannot represent ---------------


@pytest.mark.parametrize(
    "url",
    ["http://host:99999/a", "http://host:abc/a", "http://[::1/a", "http://:80/a", "http://user@:9/a"],
)
def test_malformed_urls_are_rejected_at_ingestion(url: str) -> None:
    with pytest.raises(HTTPException) as excinfo:
        validate_user_url(url)
    assert excinfo.value.status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/dQw4w9WgXcQ",
        "https://host.test:8443/a",
        "http://[::1]:8080/a",
        "http://192.168.1.5:8787/x",
    ],
)
def test_valid_urls_still_pass_ingestion(url: str) -> None:
    assert validate_user_url(url) == url


def test_serialization_never_raises_on_a_poisoned_value() -> None:
    """The migration case: bad values are already on disk from before the guard."""
    for url in ("http://host:99999/a", "http://host:abc/a", "http://[::1/a"):
        assert public_media.sanitize_public_url(url) == ""


def test_serialization_behavior_is_otherwise_unchanged() -> None:
    # Credentials and signing parameters still stripped.
    assert (
        public_media.sanitize_public_url("https://u:p@host.test/a?token=T&x=1")
        == "https://host.test/a?x=1"
    )
    # Ports, IPv6, and non-network URLs preserved exactly as before.
    assert public_media.sanitize_public_url("https://host.test:8443/a") == "https://host.test:8443/a"
    assert public_media.sanitize_public_url("http://[::1]:8080/a") == "http://[::1]:8080/a"
    assert public_media.sanitize_public_url("/media/uploads/u_1/a.mp4") == "/media/uploads/u_1/a.mp4"
    assert public_media.sanitize_public_url("//cdn.test/p.jpg") == "//cdn.test/p.jpg"
    assert public_media.sanitize_public_url("file:///data/x.mp4") == "file:///data/x.mp4"
    # An authority with no hostname is omitted, not echoed: the raw form can
    # still carry the credentials this function exists to remove.
    assert public_media.sanitize_public_url("http://user@:9/a") == ""


def test_poisoned_queue_item_does_not_break_public_payloads(monkeypatch) -> None:
    """One bad item used to fail /queue, /status, /history for every client."""
    poisoned = {"url": "http://host:99999/a", "title": "poisoned", "provider": "other"}
    healthy = {"url": "https://youtu.be/abc123", "title": "fine", "provider": "youtube"}

    monkeypatch.setattr(state, "QUEUE", [poisoned, healthy], raising=False)
    monkeypatch.setattr(state, "NOW_PLAYING", dict(poisoned), raising=False)
    monkeypatch.setattr(state, "HISTORY", [dict(poisoned)], raising=False)

    client = TestClient(create_app(testing=True))
    for path in ("/queue", "/status", "/history"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} failed on a poisoned item"
        # The healthy item is still served alongside it.
        assert "host:99999" not in response.text


# --- F14: provider classification --------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://evil-youtu.com/x",
        "https://youtube.com.phish.co/v",
        "https://rumble.com.evil.net/x",
        "https://evilrumble.com/x",
        "https://nottwitch.tv.attacker.io/x",
        "https://tiktok.com.evil.test/x",
        "https://vimeo.com.evil.test/x",
        "https://lbry.tv.evil.test/x",
    ],
)
def test_lookalike_domains_are_not_real_providers(url: str) -> None:
    assert provider_from_url(url) == "other"
    assert is_youtube_url(url) is False


@pytest.mark.parametrize(
    "url,provider",
    [
        ("https://www.youtube.com/watch?v=a", "youtube"),
        ("https://youtu.be/a", "youtube"),
        ("https://m.youtube.com/watch?v=a", "youtube"),
        ("https://music.youtube.com/watch?v=a", "youtube"),
        # Matched by the substring test this replaces; kept deliberately.
        ("https://www.youtube-nocookie.com/embed/a", "youtube"),
        ("https://www.youtubekids.com/watch?v=a", "youtube"),
        ("https://rumble.com/v1.html", "rumble"),
        ("https://www.twitch.tv/x", "twitch"),
        ("https://vm.tiktok.com/x", "tiktok"),
        ("https://www.bitchute.com/video/x", "bitchute"),
        ("https://odysee.com/@a/b", "odysee"),
        ("https://lbry.tv/@a/b", "odysee"),
        ("https://vimeo.com/123", "vimeo"),
        ("https://player.vimeo.com/video/123", "vimeo"),
        ("https://example.com/a.mp4", "other"),
        ("http://host:99999/a", "other"),
    ],
)
def test_real_provider_classification_is_unchanged(url: str, provider: str) -> None:
    assert provider_from_url(url) == provider


@pytest.mark.parametrize(
    "url,video_id",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/shorts/abc123", "abc123"),
        ("https://www.youtube.com/embed/abc123", "abc123"),
        ("https://www.youtube.com/live/abc123", "abc123"),
        # A lookalike must not yield an id an attacker chose.
        ("https://youtube.com.phish.co/watch?v=evil", None),
        ("https://evil-youtu.be/watch?v=evil", None),
    ],
)
def test_youtube_id_extraction_respects_the_boundary(url: str, video_id) -> None:
    assert youtube_id_from_url(url) == video_id
