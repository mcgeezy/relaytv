# SPDX-License-Identifier: GPL-3.0-only
import os
import re
import shlex
import subprocess
import json
import gzip
import time
import urllib.request
import shutil
import platform
import threading
from urllib.parse import urlparse, parse_qs, urlencode

from fastapi import HTTPException

from .debug import debug_log, get_logger
from .thumb_cache import attach_local_thumbnail
from . import upload_store
from . import ytdlp_format_policy


logger = get_logger("resolver")

# Compact resolver runtime telemetry for /status and /runtime/capabilities.
_RESOLVER_RUNTIME_LOCK = threading.Lock()
_RESOLVER_RUNTIME_STATE: dict[str, object] = {
    "provider": "",
    "effective_format": "",
    "transport": "",
    "last_outcome_category": "unknown",
    "last_error": "",
    "last_attempt_unix": 0.0,
    "last_success_unix": 0.0,
}


def get_resolver_runtime_state() -> dict[str, object]:
    with _RESOLVER_RUNTIME_LOCK:
        return dict(_RESOLVER_RUNTIME_STATE)


def _update_resolver_runtime_state(
    *,
    provider: str,
    effective_format: str,
    transport: str,
    outcome_category: str,
    error: str = "",
    success: bool = False,
) -> None:
    now_ts = time.time()
    with _RESOLVER_RUNTIME_LOCK:
        _RESOLVER_RUNTIME_STATE["provider"] = str(provider or "").strip().lower()
        _RESOLVER_RUNTIME_STATE["effective_format"] = str(effective_format or "").strip()
        _RESOLVER_RUNTIME_STATE["transport"] = str(transport or "").strip().lower()
        _RESOLVER_RUNTIME_STATE["last_outcome_category"] = (
            str(outcome_category or "unknown").strip().lower() or "unknown"
        )
        _RESOLVER_RUNTIME_STATE["last_error"] = str(error or "").strip()[:1200]
        _RESOLVER_RUNTIME_STATE["last_attempt_unix"] = float(now_ts)
        if success:
            _RESOLVER_RUNTIME_STATE["last_success_unix"] = float(now_ts)


def _categorize_resolver_error(error_text: str) -> str:
    low = str(error_text or "").strip().lower()
    if not low:
        return "resolve_error"
    if _youtube_error_is_botcheck(low):
        return "botcheck"
    if "requested format is not available" in low or "only images are available" in low:
        return "format_unavailable"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if "http error" in low or " 403 " in f" {low} " or " 429 " in f" {low} ":
        return "http_error"
    return "resolve_error"

# =========================
# URL helpers
# =========================

def _shared_url_score(url: str) -> int:
    try:
        p = urlparse(url)
    except Exception:
        return -100
    scheme = (p.scheme or "").lower()
    host = (p.netloc or "").lower()
    path = str(p.path or "").strip()
    if scheme not in ("http", "https") or not host:
        return -100
    rootish = path in ("", "/")
    if host.endswith("famelack.com"):
        parts = [seg for seg in path.split("/") if seg]
        if len(parts) >= 3 and parts[0].lower() in {"tv", "radio"}:
            return 120
        return -20 if rootish else 40
    if is_youtube_url(url):
        return 100 if youtube_id_from_url(url) else (10 if not rootish else -10)
    if upload_store.is_upload_url(url):
        return 100
    if ".m3u8" in url.lower():
        return 95
    if rootish:
        return -10
    return 20


def extract_first_url(text: str) -> str:
    """Extract the best http(s) URL from a blob of shared text."""
    if not text:
        return text
    text = text.strip()
    urls = [normalize_shared_url(m.group(0)) for m in re.finditer(r"https?://\S+", text)]
    urls = [u for u in urls if u]
    if not urls:
        return text
    if len(urls) == 1:
        return urls[0]
    # Android/browser shares may include a site home URL before the actual
    # playable link. Prefer the strongest deep/playable URL; ties choose the
    # later URL because share sheets often append the canonical link last.
    return max(enumerate(urls), key=lambda pair: (_shared_url_score(pair[1]), pair[0]))[1]


def normalize_shared_url(url: str) -> str:
    """Trim punctuation commonly attached by share text/markdown wrappers."""
    if not url:
        return url
    u = url.strip().strip("<>'\"`")

    # Remove obvious trailing punctuation while keeping valid URL characters.
    while u and u[-1] in ",.;!?":
        u = u[:-1]

    # Balance unmatched closing wrappers that often appear in markdown/chat text.
    pairs = {")": "(", "]": "[", "}": "{"}
    for close, open_ in pairs.items():
        while u.endswith(close) and u.count(close) > u.count(open_):
            u = u[:-1]

    return u


def validate_user_url(raw: str) -> str:
    """Normalize and validate a user-supplied URL.

    We intentionally only allow http/https in API entrypoints to avoid handing
    mpv local file paths or protocol-smuggled inputs from untrusted clients.
    """
    u = normalize_shared_url(extract_first_url(raw or ""))
    if not u:
        raise HTTPException(status_code=400, detail="Missing url")
    try:
        p = urlparse(u)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid url")
    if (p.scheme or "").lower() not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Unsupported URL scheme (http/https only)")
    if not (p.netloc or "").strip():
        raise HTTPException(status_code=400, detail="Invalid url host")
    return u

def is_youtube_url(u: str) -> bool:
    try:
        p = urlparse(u)
        host = (p.netloc or "").lower()
        return (
            host.endswith("youtube.com")
            or host.endswith("www.youtube.com")
            or host.endswith("m.youtube.com")
            or host.endswith("youtube-nocookie.com")
            or host.endswith("youtu.be")
            or "youtube.com" in host
        )
    except Exception:
        return False


def youtube_id_from_url(u: str) -> str | None:
    """Extract a YouTube video id from common URL shapes."""
    try:
        p = urlparse(u)
        host = (p.netloc or "").lower()

        if host.endswith("youtu.be"):
            vid = p.path.strip("/").split("/")[0]
            return vid or None

        if "youtube.com" in host or host.endswith("youtube-nocookie.com"):
            q = parse_qs(p.query)
            if "v" in q and q["v"]:
                return q["v"][0]
            if p.path.startswith("/shorts/"):
                parts = p.path.split("/")
                if len(parts) >= 3:
                    return parts[2] or None
            if p.path.startswith("/embed/"):
                parts = p.path.split("/")
                if len(parts) >= 3:
                    return parts[2] or None
            if p.path.startswith("/live/"):
                parts = p.path.split("/")
                if len(parts) >= 3:
                    return parts[2] or None
    except Exception:
        pass
    return None


def provider_from_url(u: str) -> str:
    """Very small provider classifier (used only for UI niceness)."""
    if upload_store.is_upload_url(u):
        return "upload"
    try:
        host = (urlparse(u).netloc or "").lower()
    except Exception:
        host = ""
    if "youtu" in host:
        return "youtube"
    if host.endswith("rumble.com") or "rumble.com" in host:
        return "rumble"
    if host.endswith("twitch.tv") or "twitch.tv" in host:
        return "twitch"
    if host.endswith("tiktok.com") or "tiktok.com" in host:
        return "tiktok"
    if host.endswith("bitchute.com") or "bitchute.com" in host:
        return "bitchute"
    if host.endswith("odysee.com") or "odysee.com" in host or host.endswith("lbry.tv") or "lbry.tv" in host:
        return "odysee"
    if host.endswith("vimeo.com") or "vimeo.com" in host:
        return "vimeo"
    if host.endswith("famelack.com") or "famelack.com" in host:
        return "famelack"
    return "other"



# =========================
# Stream + metadata resolution
# =========================

_FAMELACK_DATA_ROOT = "https://raw.githubusercontent.com/famelack/famelack-data/main"
_FAMELACK_COUNTRY_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, object]]]] = {}
_FAMELACK_CACHE_LOCK = threading.Lock()


def _famelack_cache_ttl_sec() -> float:
    raw = (os.getenv("RELAYTV_FAMELACK_CACHE_TTL_SEC") or "3600").strip()
    try:
        return max(60.0, min(float(raw), 86400.0))
    except Exception:
        return 3600.0


def _famelack_ref_from_url(url: str) -> tuple[str, str, str] | None:
    try:
        p = urlparse(url or "")
    except Exception:
        return None
    host = (p.netloc or "").lower()
    if not (host.endswith("famelack.com") or "famelack.com" in host):
        return None
    parts = [seg.strip() for seg in str(p.path or "").split("/") if seg.strip()]
    if len(parts) >= 3 and parts[0].lower() in {"tv", "radio"}:
        mode, country, nanoid = parts[0].lower(), parts[1].lower(), parts[2]
    elif len(parts) >= 2:
        mode, country, nanoid = "tv", parts[0].lower(), parts[1]
    else:
        return None
    if not re.match(r"^[a-z]{2,3}$", country, re.I):
        return None
    if not re.match(r"^[A-Za-z0-9_-]{6,64}$", nanoid):
        return None
    return mode, country, nanoid


def _fetch_famelack_json(path: str) -> object:
    url = f"{_FAMELACK_DATA_ROOT.rstrip('/')}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers={"User-Agent": "RelayTV/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read()
    try:
        text = gzip.decompress(raw).decode("utf-8", "replace")
    except Exception:
        text = raw.decode("utf-8", "replace")
    return json.loads(text)


def _famelack_country_items(mode: str, country: str) -> list[dict[str, object]]:
    mode = str(mode or "tv").strip().lower()
    country = str(country or "").strip().lower()
    key = (mode, country)
    now = time.time()
    with _FAMELACK_CACHE_LOCK:
        cached = _FAMELACK_COUNTRY_CACHE.get(key)
        if cached and (now - cached[0]) < _famelack_cache_ttl_sec():
            return list(cached[1])

    data = _fetch_famelack_json(f"{mode}/compressed/countries/{country}.json")
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        raw_items = data.get("items") or data.get("channels") or data.get("stations") or []
        items = [item for item in raw_items if isinstance(item, dict)]
    else:
        items = []

    with _FAMELACK_CACHE_LOCK:
        _FAMELACK_COUNTRY_CACHE[key] = (now, list(items))
    return items


def famelack_item_from_url(url: str) -> dict[str, object] | None:
    ref = _famelack_ref_from_url(url)
    if not ref:
        return None
    mode, country, nanoid = ref
    for item in _famelack_country_items(mode, country):
        item_id = str(item.get("nanoid") or item.get("id") or "").strip()
        if item_id == nanoid:
            out = dict(item)
            out["_famelack_mode"] = mode
            out["_famelack_country"] = country
            return out
    return None


def _normalize_famelack_youtube_url(url: str) -> str:
    vid = youtube_id_from_url(url)
    if vid:
        return f"https://www.youtube.com/watch?v={vid}"
    return url


def _select_famelack_stream_url(info: dict[str, object]) -> str:
    raw_urls = info.get("stream_urls") or info.get("streamUrls") or []
    if isinstance(raw_urls, str):
        candidates = [raw_urls]
    elif isinstance(raw_urls, list):
        candidates = [str(u or "").strip() for u in raw_urls]
    else:
        candidates = []
    candidates = [
        u for u in candidates
        if u and urlparse(u).scheme.lower() in {"http", "https"}
    ]
    for candidate in candidates:
        if ".m3u8" in candidate.lower():
            return candidate
    if candidates:
        return candidates[0]
    youtube_urls = info.get("youtube_urls") or info.get("youtubeUrls") or []
    if isinstance(youtube_urls, str):
        youtube_candidates = [youtube_urls]
    elif isinstance(youtube_urls, list):
        youtube_candidates = [str(u or "").strip() for u in youtube_urls]
    else:
        youtube_candidates = []
    for candidate in youtube_candidates:
        if candidate and urlparse(candidate).scheme.lower() in {"http", "https"}:
            return _normalize_famelack_youtube_url(candidate)
    return ""


def resolve_streams_famelack(url: str) -> tuple[str, str | None]:
    info = famelack_item_from_url(url)
    if not isinstance(info, dict):
        msg = "Famelack: channel not found"
        _update_resolver_runtime_state(
            provider="famelack",
            effective_format="direct_hls",
            transport="famelack_data",
            outcome_category="resolve_error",
            error=msg,
            success=False,
        )
        raise HTTPException(status_code=400, detail=msg)
    stream = _select_famelack_stream_url(info)
    if not stream:
        msg = "Famelack: no playable stream URL found"
        _update_resolver_runtime_state(
            provider="famelack",
            effective_format="direct_hls",
            transport="famelack_data",
            outcome_category="format_unavailable",
            error=msg,
            success=False,
        )
        raise HTTPException(status_code=400, detail=msg)
    if is_youtube_url(stream):
        return resolve_streams(stream)
    _update_resolver_runtime_state(
        provider="famelack",
        effective_format="direct_hls",
        transport="famelack_data",
        outcome_category="success",
        success=True,
    )
    return stream, None

def run(argv: list[str], check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, text=True, capture_output=True, check=check, timeout=timeout)
    except FileNotFoundError as e:
        # Test/host environments may not have yt-dlp installed.
        # Return a failed process so callers can use normal fallback logic.
        return subprocess.CompletedProcess(argv, 127, "", str(e))
    except subprocess.TimeoutExpired as e:
        # Title/metadata lookups should degrade to fallback behavior rather than
        # surfacing an exception through request handlers.
        stdout = e.stdout if isinstance(e.stdout, str) else ""
        stderr = e.stderr if isinstance(e.stderr, str) else ""
        detail = stderr or f"Command timed out after {timeout}s"
        return subprocess.CompletedProcess(argv, 124, stdout, detail)

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _truthy(v: object) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _runtime_settings() -> dict:
    try:
        from . import state

        s = state.get_settings() if hasattr(state, "get_settings") else {}
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def _invidious_enabled(settings: dict | None = None) -> bool:
    s = settings if isinstance(settings, dict) else _runtime_settings()
    if "youtube_use_invidious" in s:
        return bool(s.get("youtube_use_invidious"))
    return _truthy(os.getenv("USE_INVIDIOUS"))


def _invidious_base(settings: dict | None = None) -> str:
    s = settings if isinstance(settings, dict) else _runtime_settings()
    base = str(s.get("youtube_invidious_base") or "").strip()
    if not base:
        base = str(os.getenv("INVIDIOUS_BASE") or "").strip()
    return base.rstrip("/")


def _has_opt(argv: list[str], opt: str) -> bool:
    return any(p == opt or p.startswith(opt + "=") for p in argv)


def _without_opts(argv: list[str], *opts: str) -> list[str]:
    opt_set = {str(opt or "").strip() for opt in opts if str(opt or "").strip()}
    if not opt_set:
        return list(argv or [])
    out: list[str] = []
    skip_next = False
    for part in list(argv or []):
        text = str(part or "")
        if skip_next:
            skip_next = False
            continue
        matched = False
        for opt in opt_set:
            if text == opt:
                skip_next = True
                matched = True
                break
            if text.startswith(opt + "="):
                matched = True
                break
        if not matched:
            out.append(text)
    return out


def _youtube_error_is_botcheck(low_err: str) -> bool:
    low = (low_err or "").replace("’", "'")
    return (
        "not a bot" in low
        or "sign in to confirm you're not a bot" in low
        or "confirm you're not a bot" in low
        or "confirm you are not a bot" in low
        or "use --cookies-from-browser or --cookies" in low
    )


def _youtube_strategy_related_retry(low_err: str) -> bool:
    return (
        _youtube_error_is_botcheck(low_err)
        or "challenge solving failed" in low_err
        or "remote component challenge solver script" in low_err
        or "remote components are disabled" in low_err
    )

def _preferred_js_runtime_spec() -> str:
    override = (
        os.getenv("RELAYTV_YTDLP_JS_RUNTIME")
        or os.getenv("YTDLP_JS_RUNTIME")
        or ""
    ).strip()
    legacy_force_node = str(os.getenv("RELAYTV_YTDLP_USE_NODE") or "").strip().lower() in ("1", "true", "yes", "on")

    if override:
        low = override.lower()
        if low in ("0", "false", "no", "off", "none", "disable", "disabled"):
            return ""
        if low == "auto":
            override = ""
        elif ":" in override:
            runtime_name = override.split(":", 1)[0].strip().lower()
            if runtime_name in ("deno", "node"):
                binary_name = "node" if runtime_name == "node" else "deno"
                if shutil.which(binary_name):
                    return override
                logger.warning("configured_js_runtime_unavailable runtime=%s", override)
                return ""
            return override
        else:
            if low in ("deno", "node"):
                if shutil.which(low):
                    return low
                logger.warning("configured_js_runtime_unavailable runtime=%s", low)
                return ""
            return override

    if legacy_force_node:
        if shutil.which("node"):
            return "node"
        logger.warning("ytdlp_node_requested_but_unavailable")
        return ""

    if shutil.which("deno"):
        return "deno"
    if shutil.which("node"):
        return "node"
    return ""


def _build_youtube_arm_safe_strategies(base: list[str], candidates: list[str]) -> list[tuple[list[str], list[str]]]:
    has_cookie_auth = _has_opt(base, "--cookies") or _has_opt(base, "--cookies-from-browser")
    default_args = _without_opts(base, "--cookies", "--cookies-from-browser", "--js-runtimes", "--remote-components")
    js_runtime = _preferred_js_runtime_spec()
    strategies: list[tuple[list[str], list[str]]] = []

    if has_cookie_auth:
        challenge_cookie = list(base)
        if not _has_opt(challenge_cookie, "--js-runtimes") and js_runtime:
            challenge_cookie += ["--js-runtimes", js_runtime]
        if not _has_opt(challenge_cookie, "--remote-components"):
            challenge_cookie += ["--remote-components", "ejs:github"]
        strategies.append((challenge_cookie, candidates))

    challenge_public = _without_opts(base, "--cookies", "--cookies-from-browser")
    if not _has_opt(challenge_public, "--js-runtimes") and js_runtime:
        challenge_public += ["--js-runtimes", js_runtime]
    if not _has_opt(challenge_public, "--remote-components"):
        challenge_public += ["--remote-components", "ejs:github"]
    strategies.append((challenge_public, candidates))
    strategies.append((default_args, candidates))
    out: list[tuple[list[str], list[str]]] = []
    seen: set[tuple[str, ...]] = set()
    for args_base, strategy_candidates in strategies:
        key = tuple(args_base)
        if key in seen:
            continue
        seen.add(key)
        out.append((args_base, strategy_candidates))
    return out


def _build_youtube_strategies(base: list[str], candidates: list[str]) -> list[tuple[list[str], list[str]]]:
    has_cookie_auth = _has_opt(base, "--cookies") or _has_opt(base, "--cookies-from-browser")
    public_base = _without_opts(base, "--cookies", "--cookies-from-browser")
    default_args = _without_opts(base, "--cookies", "--cookies-from-browser", "--js-runtimes", "--remote-components")
    strategies: list[tuple[list[str], list[str]]] = []
    has_extractor_args = _has_opt(public_base, "--extractor-args")
    has_js_runtimes = _has_opt(public_base, "--js-runtimes")
    has_remote_components = _has_opt(public_base, "--remote-components")
    js_runtime = _preferred_js_runtime_spec()

    challenge_candidates = ["", "best"] if any(c in ("", "best", "b") for c in candidates) else candidates
    if has_cookie_auth:
        challenge_cookie = list(base)
        if not _has_opt(challenge_cookie, "--js-runtimes") and js_runtime:
            challenge_cookie += ["--js-runtimes", js_runtime]
        if not _has_opt(challenge_cookie, "--remote-components"):
            challenge_cookie += ["--remote-components", "ejs:github"]
        strategies.append((challenge_cookie, challenge_candidates))

    challenge_public = list(public_base)
    if not has_js_runtimes and js_runtime:
        challenge_public += ["--js-runtimes", js_runtime]
    if not has_remote_components:
        challenge_public += ["--remote-components", "ejs:github"]
    strategies.append((challenge_public, challenge_candidates))
    strategies.append((default_args, candidates))
    if tuple(public_base) != tuple(default_args):
        strategies.append((public_base, candidates))

    # Keep Android as a later fallback only.
    if not has_extractor_args:
        android_last = [*public_base, "--extractor-args", "youtube:player_client=android"]
        strategies.append((android_last, candidates))
        if has_cookie_auth:
            android_cookie = [*base, "--extractor-args", "youtube:player_client=android"]
            strategies.append((android_cookie, candidates))
    out: list[tuple[list[str], list[str]]] = []
    seen: set[tuple[str, ...]] = set()
    for args_base, strategy_candidates in strategies:
        key = tuple(args_base)
        if key in seen:
            continue
        seen.add(key)
        out.append((args_base, strategy_candidates))
    return out


def build_ytdlp_base_args() -> list[str]:
    """Build yt-dlp extra args from env vars.

    Supported env vars:
      - YTDLP_ARGS: base extra args (existing behavior)
      - RELAYTV_YTDLP_JS_RUNTIME or YTDLP_JS_RUNTIME: `auto`, `deno`, `node`, or explicit `runtime:path`
      - RELAYTV_YTDLP_USE_NODE: legacy override that forces '--js-runtimes node'
      - RELAYTV_YTDLP_COOKIES or YTDLP_COOKIES: path to Netscape cookies.txt, adds '--cookies <path>'
      - RELAYTV_YTDLP_COOKIES_FROM_BROWSER or YTDLP_COOKIES_FROM_BROWSER: adds '--cookies-from-browser <spec>'
    """
    base = (os.getenv("YTDLP_ARGS") or "").strip()
    parts: list[str] = shlex.split(base) if base else []

    def truthy(v: str | None) -> bool:
        return (v or "").strip().lower() in ("1", "true", "yes", "on")

    def has_opt(opt: str) -> bool:
        # exact match or --opt=...
        return any(p == opt or p.startswith(opt + "=") for p in parts)

    def add_opt(opt: str, val: str | None = None) -> None:
        if has_opt(opt):
            return
        parts.append(opt)
        if val is not None:
            parts.append(val)

    js_runtime = _preferred_js_runtime_spec()
    if js_runtime:
        add_opt("--js-runtimes", js_runtime)

    cookies_from = (os.getenv("RELAYTV_YTDLP_COOKIES_FROM_BROWSER") or os.getenv("YTDLP_COOKIES_FROM_BROWSER") or "").strip()
    if cookies_from:
        add_opt("--cookies-from-browser", cookies_from)

    settings = _runtime_settings()
    cookies_file = (
        os.getenv("RELAYTV_YTDLP_COOKIES")
        or os.getenv("YTDLP_COOKIES")
        or str(settings.get("youtube_cookies_path") or "").strip()
    )
    if cookies_file and os.path.exists(cookies_file):
        add_opt("--cookies", cookies_file)
    elif cookies_file:
        debug_log("youtube", f"Skipping missing cookies file: {cookies_file}")

    return ["yt-dlp", *parts, "--no-playlist"]

def resolve_streams_ytdlp(url: str):
    """
    Resolve direct stream URLs using yt-dlp.
    Returns: (video_url, audio_url_or_None)
    """
    def _log_resolve(msg: str) -> None:
        logger.debug("%s", msg)
    u = normalize_shared_url(url)
    provider = provider_from_url(u)
    try:
        from . import state, video_profile
        settings = state.get_settings() if hasattr(state, "get_settings") else {}
        profile = video_profile.get_profile() if hasattr(video_profile, "get_profile") else {}
    except Exception:
        settings = {}
        profile = {}
    host_arch = (platform.machine() or "").strip().lower()
    fmt = ytdlp_format_policy.effective_ytdlp_format(settings, provider=provider, profile=profile)
    base = build_ytdlp_base_args()
    candidates = [fmt, "best", "b", ""]
    if is_youtube_url(u) and ytdlp_format_policy.youtube_progressive_startup_enabled(profile):
        candidates = ytdlp_format_policy.youtube_progressive_startup_candidates(settings, profile=profile)
    candidates = list(dict.fromkeys(candidates))

    strategies: list[tuple[list[str], list[str]]] = [(base, candidates)]
    if is_youtube_url(u):
        strategies = _build_youtube_strategies(base, candidates)

    def _format_related_retry(low_err: str) -> bool:
        return (
            "requested format is not available" in low_err
            or "only images are available" in low_err
        )

    def _run_strategies(strategy_list: list[tuple[list[str], list[str]]]) -> tuple[object, str, str]:
        p_local = None
        err_local = ""
        selected_format = fmt or "auto"
        for args_base, strategy_candidates in strategy_list:
            debug_log("youtube", f"Trying yt-dlp strategy: {' '.join(args_base)} (host_arch={host_arch or 'unknown'})")
            _log_resolve(f"yt-dlp strategy start host_arch={host_arch or 'unknown'} args={' '.join(args_base)}")
            for cand in strategy_candidates:
                t_attempt = time.monotonic()
                cmd = [*args_base, "-g", u] if not cand else [*args_base, "-f", cand, "-g", u]
                selected_format = cand or "auto"
                p_local = run(cmd, check=False)
                elapsed_ms = int((time.monotonic() - t_attempt) * 1000)
                debug_log(
                    "youtube",
                    f"yt-dlp attempt completed in {elapsed_ms}ms (format={cand or 'auto'}) rc={p_local.returncode}",
                )
                _log_resolve(f"yt-dlp attempt format={cand or 'auto'} rc={p_local.returncode} elapsed_ms={elapsed_ms}")
                if p_local.returncode == 0 and (p_local.stdout or "").strip():
                    return p_local, "", selected_format

                err_local = (p_local.stderr or "").strip()
                low_err = err_local.lower()
                if err_local:
                    one_line = " ".join(err_local.splitlines())
                    if len(one_line) > 280:
                        one_line = one_line[:280] + "..."
                    debug_log(
                        "youtube",
                        f"yt-dlp stderr (format={cand or 'auto'}): {one_line}",
                    )

                if _youtube_strategy_related_retry(low_err):
                    break
                if _format_related_retry(low_err):
                    continue
                break
        return p_local, err_local, selected_format

    p = None
    err = ""
    selected_format = fmt or "auto"
    t0 = time.monotonic()
    if is_youtube_url(u) and ytdlp_format_policy.youtube_progressive_startup_enabled(profile):
        _log_resolve("youtube arm-safe staged resolver enabled")
        p, err, selected_format = _run_strategies(_build_youtube_arm_safe_strategies(base, candidates))
    else:
        p, err, selected_format = _run_strategies(strategies)

    if not p:
        _update_resolver_runtime_state(
            provider=provider,
            effective_format=selected_format,
            transport="yt-dlp",
            outcome_category="resolve_error",
            error="yt-dlp failed: empty process response",
            success=False,
        )
        raise HTTPException(status_code=400, detail="yt-dlp failed: empty process response")
    if p.returncode != 0 or not (p.stdout or "").strip():
        err = err or (p.stderr or "").strip()
        outcome_category = _categorize_resolver_error(err)
        _update_resolver_runtime_state(
            provider=provider,
            effective_format=selected_format,
            transport="yt-dlp",
            outcome_category=outcome_category,
            error=err,
            success=False,
        )
        logger.warning("ytdlp_failed provider=%s format=%s error=%s", provider or "unknown", selected_format, err[:1200])
        if is_youtube_url(u) and _youtube_error_is_botcheck(err.lower()):
            raise HTTPException(
                status_code=400,
                detail=(
                    "yt-dlp failed: YouTube requires anti-bot verification/cookies. "
                    "Configure RELAYTV_YTDLP_COOKIES (cookies.txt) or enable a working Invidious server. "
                    f"Details: {err[:900]}"
                ),
            )
        raise HTTPException(status_code=400, detail=f"yt-dlp failed: {err[:1200]}")

    lines = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
    total_ms = int((time.monotonic() - t0) * 1000)
    debug_log("youtube", f"yt-dlp resolve succeeded in {total_ms}ms with {len(lines)} stream line(s)")
    _log_resolve(f"yt-dlp resolve succeeded total_ms={total_ms} stream_lines={len(lines)}")
    _update_resolver_runtime_state(
        provider=provider,
        effective_format=selected_format,
        transport="yt-dlp",
        outcome_category="success",
        success=True,
    )
    if len(lines) == 1:
        return lines[0], None
    return lines[0], lines[1]


def resolve_streams_invidious(youtube_url: str, base: str | None = None):
    """
    Resolve streams via Invidious API (local=true for proxied videoplayback URLs).
    """
    resolved_base = (base or _invidious_base()).rstrip("/")
    if not resolved_base:
        raise HTTPException(status_code=400, detail="INVIDIOUS_BASE not set")

    vid = youtube_id_from_url(youtube_url)
    if not vid:
        raise HTTPException(status_code=400, detail="Could not extract YouTube video id")

    api_url = f"{resolved_base}/api/v1/videos/{vid}?{urlencode({'local': 'true'})}"
    try:
        with urllib.request.urlopen(api_url, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invidious API error: {e}")

    fmt_streams = data.get("formatStreams") or []
    adaptive = data.get("adaptiveFormats") or []

    # Prefer muxed streams (audio+video) if available
    muxed = [x for x in fmt_streams if isinstance(x, dict) and x.get("url")]
    if muxed:
        mp4_muxed = [x for x in muxed if "mp4" in str(x.get("mimeType", "")).lower()]
        pool = mp4_muxed or muxed

        def mux_score(x):
            br = int(x.get("bitrate") or 0)
            ql = str(x.get("qualityLabel") or "")
            m = re.search(r"(\d+)\s*p", ql)
            res = int(m.group(1)) if m else 0
            return (res, br)

        best = sorted(pool, key=mux_score, reverse=True)[0]
        return best["url"], None

    # Adaptive: choose best video + best audio
    vids = [
        x for x in adaptive
        if isinstance(x, dict)
        and x.get("url")
        and str(x.get("mimeType", "")).lower().startswith("video/")
    ]
    auds = [
        x for x in adaptive
        if isinstance(x, dict)
        and x.get("url")
        and str(x.get("mimeType", "")).lower().startswith("audio/")
    ]

    if not vids:
        raise HTTPException(status_code=400, detail="Invidious: no video streams found")

    def v_score(x):
        w = int(x.get("width") or 0)
        h = int(x.get("height") or 0)
        br = int(x.get("bitrate") or 0)
        return (h * w, br)

    def a_score(x):
        return int(x.get("bitrate") or 0)

    vbest = sorted(vids, key=v_score, reverse=True)[0]["url"]
    abest = sorted(auds, key=a_score, reverse=True)[0]["url"] if auds else None
    return vbest, abest


def resolve_streams(url: str):
    """
    Hybrid resolver:
      - YouTube -> Invidious if enabled (avoids bot checks)
      - Famelack -> public data feed direct stream URL
      - Others -> yt-dlp
    """
    url = validate_user_url(url)
    settings = _runtime_settings()
    use_invid = _invidious_enabled(settings)
    invid_base = _invidious_base(settings)
    provider = provider_from_url(url)
    debug_log("resolver", f"Resolving streams for provider={provider} use_invidious={use_invid}")
    if provider == "famelack":
        return resolve_streams_famelack(url)
    if use_invid and is_youtube_url(url):
        try:
            stream, audio = resolve_streams_invidious(url, base=invid_base)
            _update_resolver_runtime_state(
                provider=provider,
                effective_format="invidious_auto",
                transport="invidious",
                outcome_category="success",
                success=True,
            )
            return stream, audio
        except HTTPException as exc:
            _update_resolver_runtime_state(
                provider=provider,
                effective_format="invidious_auto",
                transport="invidious",
                outcome_category=_categorize_resolver_error(str(exc.detail or "")),
                error=str(exc.detail or ""),
                success=False,
            )
            raise
        except Exception as exc:
            _update_resolver_runtime_state(
                provider=provider,
                effective_format="invidious_auto",
                transport="invidious",
                outcome_category="resolve_error",
                error=f"{type(exc).__name__}: {exc}",
                success=False,
            )
            raise
    return resolve_streams_ytdlp(url)


def title_from_invidious(youtube_url: str, base: str | None = None) -> str | None:
    base = (base or _invidious_base()).rstrip("/")
    if not base:
        return None
    vid = youtube_id_from_url(youtube_url)
    if not vid:
        return None
    api_url = f"{base}/api/v1/videos/{vid}"
    try:
        with urllib.request.urlopen(api_url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        t = data.get("title")
        return t.strip() if isinstance(t, str) and t.strip() else None
    except Exception:
        return None


def title_from_ytdlp(url: str) -> str | None:
    """
    Fast title lookup using yt-dlp metadata (no download).
    """
    u = normalize_shared_url(url)
    p = run([*build_ytdlp_base_args(), "--print", "%(title)s", u], check=False, timeout=20)
    if p.returncode != 0:
        return None
    t = (p.stdout or "").strip()
    return t if t else None


def youtube_oembed_info(youtube_url: str) -> dict | None:
    """Best-effort YouTube metadata fallback via oEmbed.

    This endpoint is lightweight and often succeeds even when yt-dlp metadata
    extraction is blocked/rate-limited. It can provide title + author_name.
    """
    u = normalize_shared_url(extract_first_url(youtube_url or ""))
    if not u:
        return None
    api_url = "https://www.youtube.com/oembed?" + urlencode({"url": u, "format": "json"})
    try:
        with urllib.request.urlopen(api_url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        if isinstance(data, dict) and data:
            return data
    except Exception:
        pass
    return None


# Cache for yt-dlp metadata lookups (thumbnails, titles) to avoid repeated calls
_YTDLP_INFO_CACHE: dict[str, tuple[float, dict]] = {}
_YTDLP_INFO_TTL_SEC = int(os.getenv("YTDLP_INFO_TTL_SEC", "21600"))  # 6 hours


def ytdlp_info(url: str) -> dict | None:
    """
    Fetch metadata via yt-dlp (no download) and return a dict that may include:
      - title
      - thumbnail
      - extractor / extractor_key
      - webpage_url
    Uses a small in-memory TTL cache.
    """
    u = extract_first_url(url)
    if not u:
        return None

    now = time.time()
    cached = _YTDLP_INFO_CACHE.get(u)
    if cached and (now - cached[0]) < _YTDLP_INFO_TTL_SEC:
        return cached[1]

    cmd = [*build_ytdlp_base_args(), "--skip-download", "-J", u]
    try:
        p = run(cmd, check=False, timeout=20)
        if p.returncode != 0:
            return None
        data = json.loads((p.stdout or "").strip() or "{}")
        if not isinstance(data, dict) or not data:
            return None
        _YTDLP_INFO_CACHE[u] = (now, data)
        return data
    except Exception:
        return None


def resolve_title(url: str) -> str:
    """
    Best-effort title resolver.
    - If YouTube + Invidious enabled: use Invidious API title
    - Else: use yt-dlp metadata title
    - Fallback: url
    """
    u = extract_first_url(url)
    settings = _runtime_settings()
    use_invid = _invidious_enabled(settings)
    invid_base = _invidious_base(settings)
    prov = provider_from_url(u)

    if prov == "youtube" and use_invid:
        t = title_from_invidious(u, base=invid_base)
        if t:
            return t

    t = title_from_ytdlp(u)
    return t or u


def _provider_display_name(provider: str) -> str:
    prov = str(provider or "").strip().lower()
    labels = {
        "youtube": "YouTube",
        "rumble": "Rumble",
        "bitchute": "BitChute",
        "odysee": "Odysee",
        "vimeo": "Vimeo",
        "twitch": "Twitch",
        "tiktok": "TikTok",
        "jellyfin": "Jellyfin",
        "famelack": "Famelack",
    }
    return labels.get(prov, prov.title() if prov else "Video")


def _info_is_currently_live(info: object) -> bool:
    if not isinstance(info, dict):
        return False
    if bool(info.get("is_live")):
        return True
    live_status = str(info.get("live_status") or "").strip().lower()
    return live_status in ("is_live", "live")


def _apply_live_metadata(item: dict[str, object], info: object) -> bool:
    if not isinstance(item, dict) or not isinstance(info, dict):
        return False
    changed = False
    live_status = str(info.get("live_status") or "").strip()
    if live_status and item.get("live_status") != live_status:
        item["live_status"] = live_status
        changed = True
    is_live = _info_is_currently_live(info)
    if item.get("is_live") is not is_live:
        item["is_live"] = is_live
        changed = True
    return changed



def _fallback_item_title(url: str, provider: str) -> str:
    prov = str(provider or "").strip().lower()
    try:
        p = urlparse(url or "")
        parts = [seg for seg in str(p.path or "").split("/") if seg]
        label = parts[-1] if parts else ""
        label = urllib.parse.unquote(label).strip()

        if prov == "rumble" and label:
            if label.lower().endswith(".html"):
                label = label[:-5]
            if re.match(r"^v[0-9a-z]+-", label, re.I):
                label = label.split("-", 1)[1]
        elif prov == "bitchute":
            if len(parts) >= 2 and parts[0].lower() == "video" and parts[1]:
                # BitChute links often expose only an opaque id; prefer a
                # human label over the raw token for lightweight notifications.
                return f"{_provider_display_name(prov)} video"

        label = label.replace("-", " ").replace("_", " ").strip()
        label = re.sub(r"\s+", " ", label)
        if label and label.lower() not in {"watch", "video", "videos"}:
            return label

        host = str(p.netloc or "").strip()
        if host:
            return _provider_display_name(prov) if prov else host
    except Exception:
        pass
    return _provider_display_name(prov) if prov else url



def enrich_item_metadata(item: dict[str, object]) -> bool:
    if not isinstance(item, dict):
        return False
    u = validate_user_url(str(item.get("url") or ""))
    prov = str(item.get("provider") or provider_from_url(u) or "other").strip().lower() or "other"
    changed = False
    title = str(item.get("title") or "").strip()
    thumb = str(item.get("thumbnail") or "").strip()
    channel = str(item.get("channel") or "").strip()

    if prov == "youtube":
        info = ytdlp_info(u)
        if isinstance(info, dict):
            for k in ("channel", "uploader", "uploader_id"):
                v = info.get(k)
                if isinstance(v, str) and v.strip() and v.strip() != channel:
                    item["channel"] = v.strip()
                    channel = v.strip()
                    changed = True
                    break
            th = info.get("thumbnail")
            if isinstance(th, str) and th.strip() and th.strip() != thumb:
                item["thumbnail"] = th.strip()
                thumb = th.strip()
                changed = True
            if _apply_live_metadata(item, info):
                changed = True
        attach_local_thumbnail(item)
        if "_metadata_lightweight" in item:
            item.pop("_metadata_lightweight", None)
            changed = True
        return changed

    if prov == "famelack":
        info = famelack_item_from_url(u)
        if isinstance(info, dict):
            t = info.get("name") or info.get("title")
            if isinstance(t, str) and t.strip() and t.strip() != title:
                item["title"] = t.strip()
                title = t.strip()
                changed = True
            country = str(info.get("_famelack_country") or info.get("country") or "").strip().upper()
            if country and country != channel:
                item["channel"] = country
                channel = country
                changed = True
            if item.get("is_live") is not True:
                item["is_live"] = True
                changed = True
        if "_metadata_lightweight" in item:
            item.pop("_metadata_lightweight", None)
            changed = True
        return changed

    info = ytdlp_info(u)
    if isinstance(info, dict):
        t = info.get("title")
        if isinstance(t, str) and t.strip() and t.strip() != title:
            item["title"] = t.strip()
            title = t.strip()
            changed = True
        th = info.get("thumbnail")
        if isinstance(th, str) and th.strip() and th.strip() != thumb:
            item["thumbnail"] = th.strip()
            thumb = th.strip()
            changed = True
        for k in ("channel", "uploader", "uploader_id"):
            v = info.get(k)
            if isinstance(v, str) and v.strip() and v.strip() != channel:
                item["channel"] = v.strip()
                channel = v.strip()
                changed = True
                break
        if _apply_live_metadata(item, info):
            changed = True

    if not title or title == u:
        resolved_title = resolve_title(u)
        if resolved_title and resolved_title != title:
            item["title"] = resolved_title
            changed = True

    attach_local_thumbnail(item)
    if "_metadata_lightweight" in item:
        item.pop("_metadata_lightweight", None)
        changed = True
    return changed



def make_item(input_text: str, *, lightweight: bool = False) -> dict:
    """
    Convert shared text into a playback/queue item dict.

    When lightweight=True, prefer a fast queue-friendly item build and defer
    expensive metadata lookups to background enrichment.
    """
    u = validate_user_url(input_text or "")

    prov = provider_from_url(u)

    if prov == "upload":
        item = upload_store.item_from_url(u)
        attach_local_thumbnail(item)
        return item

    # Title:
    # - YouTube: lightweight path uses Invidious/oEmbed
    # - Others: either lightweight fallback or yt-dlp metadata
    info = None
    title = None
    thumb = None
    channel = None

    if prov == "youtube":
        settings = _runtime_settings()
        use_invid = _invidious_enabled(settings)
        invid_base = _invidious_base(settings)
        if use_invid:
            title = title_from_invidious(u, base=invid_base)
        vid = youtube_id_from_url(u)
        if vid:
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

        oembed = youtube_oembed_info(u)
        if isinstance(oembed, dict):
            if not title:
                t = oembed.get("title")
                if isinstance(t, str) and t.strip():
                    title = t.strip()
            a = oembed.get("author_name")
            if isinstance(a, str) and a.strip():
                channel = a.strip()
    elif prov == "famelack":
        info = famelack_item_from_url(u)
        if isinstance(info, dict):
            t = info.get("name") or info.get("title")
            if isinstance(t, str) and t.strip():
                title = t.strip()
            country = str(info.get("_famelack_country") or info.get("country") or "").strip().upper()
            if country:
                channel = country
    elif not lightweight:
        info = ytdlp_info(u)
        if isinstance(info, dict):
            t = info.get("title")
            if isinstance(t, str) and t.strip():
                title = t.strip()
            th = info.get("thumbnail")
            if isinstance(th, str) and th.strip():
                thumb = th.strip()

            for k in ("channel", "uploader", "uploader_id"):
                v = info.get(k)
                if isinstance(v, str) and v.strip():
                    channel = v.strip()
                    break

        if not title:
            title = resolve_title(u)
    else:
        title = _fallback_item_title(u, prov)

    item = {
        "url": u,
        "provider": prov,
        "title": title or u,
    }
    if channel:
        item["channel"] = channel
    if thumb:
        item["thumbnail"] = thumb
    if isinstance(info, dict):
        if prov == "famelack":
            item["is_live"] = True
            if info.get("_famelack_mode"):
                item["_famelack_mode"] = str(info.get("_famelack_mode") or "")
            if info.get("_famelack_country"):
                item["_famelack_country"] = str(info.get("_famelack_country") or "")
        else:
            _apply_live_metadata(item, info)
    if lightweight and prov != "famelack":
        item["_metadata_lightweight"] = True

    attach_local_thumbnail(item)
    return item
