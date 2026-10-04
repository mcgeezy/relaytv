# SPDX-License-Identifier: GPL-3.0-only
import os
import re
import subprocess
from typing import Any


def _connector_index(connector: str) -> int | None:
    """Return trailing connector number (HDMI-A-1 -> 1), if present."""
    m = re.search(r"(\d+)\s*$", (connector or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None

def _read_first_line(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return (f.readline() or "").strip()
    except Exception:
        return None

def list_drm_connectors() -> list[dict[str, Any]]:
    """Return HDMI/DP connectors with connection status (best-effort)."""
    out: list[dict[str, Any]] = []
    base = "/sys/class/drm"
    try:
        entries = os.listdir(base)
    except Exception:
        return out

    # Typical names: card0-HDMI-A-1, card1-DP-1, card0-eDP-1, etc.
    pat = re.compile(r"^card\d+-(HDMI|DP|eDP)-.*")
    for name in sorted(entries):
        if not pat.match(name):
            continue
        status = _read_first_line(os.path.join(base, name, "status")) or "unknown"
        modes = []
        try:
            mp = os.path.join(base, name, "modes")
            if os.path.exists(mp):
                with open(mp, "r", encoding="utf-8", errors="ignore") as f:
                    modes = [ln.strip() for ln in f.read().splitlines() if ln.strip()][:50]
        except Exception:
            modes = []
        # connector_id is the part after 'cardX-'
        connector_id = name.split("-", 1)[1] if "-" in name else name
        out.append({
            "sys_name": name,
            "connector": connector_id,
            "status": status,
            "modes": modes,
        })
    return out

def list_cec_devices() -> list[str]:
    devs = []
    for p in ("/dev/cec0", "/dev/cec1", "/dev/cec2", "/dev/cec3"):
        if os.path.exists(p):
            devs.append(p)
    return devs



def cec_client_probe() -> dict[str, Any]:
    out = {"cec_client_available": False, "adapters_reported": [], "raw": ""}
    try:
        p = subprocess.run(["cec-client", "-l"], text=True, capture_output=True, timeout=3)
    except FileNotFoundError:
        return out
    except Exception:
        return out

    txt = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
    out["raw"] = txt[:2000]
    if p.returncode == 0 and txt:
        out["cec_client_available"] = True
    adapters: list[str] = []
    for ln in txt.splitlines():
        if "adapter:" in ln.lower() or "device:" in ln.lower():
            adapters.append(ln.strip())
    out["adapters_reported"] = adapters[:20]
    return out


def list_alsa_devices() -> list[dict[str, str]]:
    """Parse `aplay -L` into a list of devices."""
    try:
        p = subprocess.run(["aplay", "-L"], text=True, capture_output=True, timeout=3)
        txt = p.stdout or ""
    except Exception:
        return []

    lines = txt.splitlines()
    devices: list[dict[str, str]] = []
    cur = None
    for ln in lines:
        if not ln.strip():
            continue
        if ln and not ln.startswith(" "):
            # new device id
            cur = {"id": ln.strip(), "desc": ""}
            devices.append(cur)
        else:
            if cur is not None and not cur["desc"]:
                cur["desc"] = ln.strip()

    # Prefer common HDMI entries at top (stable UX)
    def key(d):
        i = d.get("id","")
        if i.startswith("hdmi:") or "hdmi" in i.lower():
            return (0, i)
        if i in ("default","pipewire","pulse"):
            return (1, i)
        return (2, i)
    devices.sort(key=key)
    return devices


def detect_audio_device(drm_connector: str = "") -> str:
    """Best-effort HDMI-aware ALSA device detection.

    Returns an ALSA device id from `aplay -L`, or "" when nothing suitable is found.
    """
    alsa = list_alsa_devices()
    if not alsa:
        return ""

    def _normalize_for_mpv(dev_id: str) -> str:
        d = (dev_id or "").strip()
        if not d:
            return ""
        low = d.lower()
        if low.startswith("alsa/"):
            return d
        if low in ("pulse", "pipewire", "jack", "sndio", "null", "auto"):
            return d
        if ":" in d:
            return f"alsa/{d}"
        return d

    hdmi_ids = [d.get("id", "") for d in alsa if "hdmi" in d.get("id", "").lower()]
    if not hdmi_ids:
        return ""

    # If connector is not explicitly supplied, inspect currently connected outputs.
    connector = (drm_connector or "").strip()
    if not connector:
        connected = [c for c in list_drm_connectors() if str(c.get("status", "")).lower() == "connected"]
        if connected:
            connector = str(connected[0].get("connector") or "").strip()

    # Only map connector index for HDMI connectors. DP/eDP index values do not
    # correspond to ALSA HDMI DEV numbering and can pick the wrong sink.
    low_connector = connector.lower()
    idx = _connector_index(connector) if "hdmi" in low_connector else None
    if idx is not None and idx > 0:
        # Common mappings:
        # - HDMI-A-1 -> hdmi0 / DEV=0
        # - HDMI-A-2 -> hdmi1 / DEV=1
        for dev in hdmi_ids:
            low = dev.lower()
            if f"hdmi{idx - 1}" in low or f"dev={idx - 1}" in low:
                return _normalize_for_mpv(dev)

    # Prefer DEV=0 as a stable default when connector mapping is not available.
    for dev in hdmi_ids:
        if "dev=0" in dev.lower():
            return _normalize_for_mpv(dev)

    # Fall back to first HDMI-like entry.
    return _normalize_for_mpv(hdmi_ids[0])

def discover() -> dict:
    cec_probe = cec_client_probe()
    profile: dict[str, Any] = {}
    try:
        from . import video_profile

        profile = video_profile.get_profile()
    except Exception:
        profile = {}
    return {
        "drm_connectors": list_drm_connectors(),
        "alsa_devices": list_alsa_devices(),
        "auto_audio_device": detect_audio_device(),
        "cec_devices": list_cec_devices(),
        "cec_client_available": cec_probe.get("cec_client_available", False),
        "cec_adapters_reported": cec_probe.get("adapters_reported", []),
        "has_dri": os.path.exists("/dev/dri"),
        "has_snd": os.path.exists("/dev/snd"),
        "display": os.getenv("DISPLAY") or "",
        "video_profile": profile,
    }
