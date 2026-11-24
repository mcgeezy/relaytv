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


def _read_modes(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return [ln.strip() for ln in f.read().splitlines() if ln.strip()][:50]
    except Exception:
        return []


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
        modes = _read_modes(os.path.join(base, name, "modes"))

        # connector_id is the part after 'cardX-'
        connector_id = name.split("-", 1)[1] if "-" in name else name
        out.append(
            {
                "sys_name": name,
                "connector": connector_id,
                "status": status,
                "modes": modes,
            }
        )
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


def _alsa_device_sort_key(d: dict[str, str]) -> tuple[int, str]:
    """Prefer common HDMI entries at top (stable UX)"""
    i = d.get("id", "")
    if i.startswith("hdmi:") or "hdmi" in i.lower():
        return (0, i)
    if i in ("default", "pipewire", "pulse"):
        return (1, i)
    return (2, i)


def list_alsa_devices() -> list[dict[str, str]]:
    """Parse `aplay -L` into a list of devices."""
    try:
        p = subprocess.run(["aplay", "-L"], text=True, capture_output=True, timeout=3)
        txt = p.stdout or ""
    except Exception:
        return []

    devices: list[dict[str, str]] = []
    for ln in txt.splitlines():
        if not ln.strip():
            continue
        if not ln.startswith(" "):
            # new device id
            devices.append({"id": ln.strip(), "desc": ""})
        elif devices and not devices[-1]["desc"]:
            devices[-1]["desc"] = ln.strip()

    devices.sort(key=_alsa_device_sort_key)
    return devices


def _normalize_for_mpv(dev_id: str) -> str:
    d = (dev_id or "").strip()
    if not d:
        return ""
    low = d.lower()
    if low.startswith(("alsa/", "pulse/", "pipewire/")):
        return d
    if low in ("pulse", "pipewire", "jack", "sndio", "null", "auto"):
        return d
    if ":" in d:
        return f"alsa/{d}"
    return d


_GENERIC_HDMI_RE = re.compile(r"^(?:hdmi\s*\d*|mai\s+pcm\b.*)$", re.IGNORECASE)
_MPV_AUDIO_LINE_RE = re.compile(r"^\s*'([^']+)'\s*\((.+)\)\s*$")
_ALSA_CARD_DEV_RE = re.compile(r"CARD=([^,\s]+)(?:,DEV=(\d+))?", re.IGNORECASE)
_VC4_HDMI_CARD_RE = re.compile(r"^vc4hdmi(\d*)$", re.IGNORECASE)


def _is_viable_sound_server_sink(dev_id: str, desc: str) -> bool:
    low_id = (dev_id or "").lower()
    low_desc = (desc or "").lower()
    if not low_id.startswith(("pulse/", "pipewire/")):
        return False
    if (
        "auto_null" in low_id
        or ".monitor" in low_id
        or "dummy output" in low_desc
        or "null output" in low_desc
    ):
        return False
    return True


def _is_pulse_display_sink(dev_id: str, desc: str) -> bool:
    low_id = (dev_id or "").lower()
    low_desc = (desc or "").lower()
    return (
        "hdmi" in low_id
        or "displayport" in low_id
        or "hdmi" in low_desc
        or "displayport" in low_desc
    )


def _extract_hdmi_monitor_names(entries: list[tuple[str, str]]) -> list[str]:
    """Extract real connected HDMI monitor/TV names (e.g. '27G2G8', 'E601i-A3') from ALSA entries."""
    names: list[str] = []
    seen: set[str] = set()
    for dev_id, desc in entries:
        low_id = dev_id.lower()
        if not (low_id.startswith("alsa/hdmi:") or low_id.startswith("hdmi:")):
            continue
        # Typical mpv desc: "HDA NVidia, 27G2G8/HDMI Audio Output"
        # Typical aplay desc: "HDA NVidia, 27G2G8"
        head = desc.split("/", 1)[0].strip()
        if "," not in head:
            continue
        mon = head.split(",", 1)[1].strip()
        if not mon or _GENERIC_HDMI_RE.match(mon):
            continue
        key = mon.lower()
        if key not in seen:
            seen.add(key)
            names.append(mon)
    return names


def _human_label_for_pulse(
    dev_id: str,
    raw_desc: str,
    hdmi_monitors: list[str],
    *,
    pulse_hdmi_count: int = 1,
) -> str:
    desc = (raw_desc or "").strip()
    low_id = dev_id.lower()
    low_desc = desc.lower()

    # HDMI / DisplayPort sinks
    if _is_pulse_display_sink(dev_id, desc):
        is_dp = "displayport" in low_id or "displayport" in low_desc
        prefix = "DisplayPort" if is_dp else "HDMI"
        if pulse_hdmi_count == 1 and len(hdmi_monitors) == 1:
            return f"{prefix} — {hdmi_monitors[0]}"
        # Strip noisy PCI controller suffixes
        cleaned = re.sub(
            r"\s*(?:HDMI/DP\s+Audio\s+Controller|HD\s+Audio\s+Controller|Digital\s+Stereo\s*\((?:HDMI|DisplayPort)(?:\s*\d+)?\)|Digital\s+Surround[^)]*\))\s*",
            " ",
            desc,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -")
        if cleaned and cleaned.lower() not in ("built-in audio", "hdmi", "displayport"):
            return f"{prefix} — {cleaned}"
        return f"{prefix} Audio"

    # Built-in analog / headphone jack (including Raspberry Pi mailbox 3.5mm jack)
    if (
        "analog" in low_id
        or "analog" in low_desc
        or ("mailbox" in low_id and "built-in audio" in low_desc)
    ):
        if "built-in audio" in low_desc:
            return "Built-in Audio (Analog / Headphones)"
        cleaned = re.sub(r"\s*Analog\s+Stereo\s*$", "", desc, flags=re.IGNORECASE).strip(" -")
        if cleaned:
            return f"{cleaned} (Analog)"
        return "Analog Audio"

    # S/PDIF / optical digital
    if "iec958" in low_id or "spdif" in low_desc or "iec958" in low_desc:
        cleaned = re.sub(
            r"\s*(?:Digital Stereo\s*\(IEC958\)|IEC958|S/PDIF)\s*",
            " ",
            desc,
            flags=re.IGNORECASE,
        ).strip(" -")
        return f"{cleaned or 'Built-in Audio'} (Digital / Optical)"

    # USB / Bluetooth / generic speakers (strip trailing "Mono" / "Analog Stereo" noise)
    cleaned = re.sub(r"\s*(?:Mono|Stereo)\s*$", "", desc, flags=re.IGNORECASE).strip(" -")
    return cleaned or desc or dev_id


def _human_label_for_alsa(dev_id: str, raw_desc: str) -> str:
    head = (raw_desc or "").split("/", 1)[0].strip()
    card_label, sub_label = (head.split(",", 1)[0].strip(), head.split(",", 1)[1].strip()) if "," in head else (head, "")
    low_id = dev_id.lower()
    low_sub = sub_label.lower()

    if "hdmi:" in low_id:
        if sub_label and not _GENERIC_HDMI_RE.match(sub_label):
            return f"HDMI — {sub_label}"
        m = _ALSA_CARD_DEV_RE.search(dev_id)
        card = (m.group(1) if m else "").strip()
        vc4_m = _VC4_HDMI_CARD_RE.match(card)
        if vc4_m:
            return f"HDMI {int(vc4_m.group(1) or 0) + 1}"
        dev_num = m.group(2) if m and m.group(2) is not None else "0"
        return f"HDMI {dev_num} ({card_label})" if card_label else f"HDMI {dev_num}"

    if "iec958:" in low_id or "digital" in low_sub:
        base = "Built-in Audio" if "hda intel" in card_label.lower() else (card_label or "Audio")
        return f"{base} (Digital / Optical)"

    if "analog" in low_sub or "headphone" in low_sub:
        base = "Built-in Audio" if "hda intel" in card_label.lower() else (card_label or "Audio")
        return f"{base} (Analog / Headphones)"

    if "usb audio" in low_sub and card_label and card_label.lower() != "usb audio":
        return f"{card_label} (USB Audio)"

    if card_label and sub_label and card_label.lower() != sub_label.lower():
        return f"{card_label} — {sub_label}"
    return card_label or sub_label or dev_id


def _probe_mpv_audio_devices() -> list[tuple[str, str]]:
    """Run `mpv --audio-device=help` and return (id, description) pairs."""
    try:
        p = subprocess.run(
            ["mpv", "--audio-device=help"],
            text=True,
            capture_output=True,
            timeout=4,
        )
        txt = p.stdout or ""
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for ln in txt.splitlines():
        m = _MPV_AUDIO_LINE_RE.match(ln)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def _connected_hdmi_indices(drm_connectors: list[dict[str, Any]] | None) -> tuple[set[int], set[int]]:
    """Return (connected_zero_based_indices, disconnected_zero_based_indices) for HDMI DRM connectors."""
    connected: set[int] = set()
    disconnected: set[int] = set()
    for c in drm_connectors or []:
        name = str(c.get("connector") or "").strip()
        if "hdmi" not in name.lower():
            continue
        idx = _connector_index(name)
        if idx is None or idx <= 0:
            continue
        status = str(c.get("status") or "").strip().lower()
        if status == "connected":
            connected.add(idx - 1)
        elif status == "disconnected":
            disconnected.add(idx - 1)
    return connected, disconnected


def _curate_alsa_hdmi_entries(
    entries: list[tuple[str, str]],
    *,
    drm_connectors: list[dict[str, Any]] | None = None,
    require_confirmed: bool = False,
    vc4_only: bool = False,
) -> list[dict[str, str]]:
    """Extract curated ALSA HDMI outputs, filtering disconnected ports when known."""
    conn_hdmi, disc_hdmi = _connected_hdmi_indices(drm_connectors)

    named_hdmi_cards: set[str] = set()
    for dev_id, desc in entries:
        norm = _normalize_for_mpv(dev_id)
        if not norm.lower().startswith("alsa/hdmi:"):
            continue
        m = _ALSA_CARD_DEV_RE.search(norm)
        card = (m.group(1) if m else "").lower()
        head = desc.split("/", 1)[0]
        sub = head.split(",", 1)[1].strip() if "," in head else ""
        if card and sub and not _GENERIC_HDMI_RE.match(sub):
            named_hdmi_cards.add(card)

    out: list[dict[str, str]] = []
    seen_hw: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    for dev_id, desc in entries:
        norm = _normalize_for_mpv(dev_id)
        if not norm.lower().startswith("alsa/hdmi:"):
            continue
        m = _ALSA_CARD_DEV_RE.search(norm)
        card = (m.group(1) if m else "").lower()
        dev_num = m.group(2) if m and m.group(2) is not None else "0"
        head = desc.split("/", 1)[0]
        sub = head.split(",", 1)[1].strip() if "," in head else ""
        has_monitor_name = bool(sub and not _GENERIC_HDMI_RE.match(sub))

        vc4_m = _VC4_HDMI_CARD_RE.match(card)
        if vc4_only and not vc4_m:
            continue
        vc4_idx = int(vc4_m.group(1) or 0) if vc4_m else None
        vc4_connected = vc4_idx is not None and vc4_idx in conn_hdmi
        vc4_disconnected = vc4_idx is not None and vc4_idx in disc_hdmi

        if vc4_disconnected:
            continue
        if card in named_hdmi_cards and not has_monitor_name:
            continue
        if require_confirmed and not (has_monitor_name or vc4_connected):
            continue

        hw_key = (card, f"hdmi:{dev_num}")
        if hw_key in seen_hw or norm in seen_ids:
            continue
        seen_hw.add(hw_key)
        seen_ids.add(norm)
        label = _human_label_for_alsa(norm, desc)
        out.append({"id": norm, "label": label, "desc": label, "backend": "alsa", "kind": "hdmi"})
    return out


def _curate_audio_devices(
    entries: list[tuple[str, str]],
    *,
    drm_connectors: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Filter raw mpv/ALSA entries to only viable playback outputs with clean labels."""
    hdmi_monitors = _extract_hdmi_monitor_names(entries)

    # 1. Prefer real PipeWire / PulseAudio sinks when available (avoids ALSA busy conflicts)
    viable_pulse: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for dev_id, desc in entries:
        if not _is_viable_sound_server_sink(dev_id, desc):
            continue
        if dev_id in seen_ids:
            continue
        seen_ids.add(dev_id)
        viable_pulse.append((dev_id, desc))

    pulse_hdmi_count = sum(
        1 for dev_id, desc in viable_pulse if _is_pulse_display_sink(dev_id, desc)
    )
    pulse_out: list[dict[str, str]] = []
    for dev_id, desc in viable_pulse:
        low_id = dev_id.lower()
        low_desc = desc.lower()
        label = _human_label_for_pulse(
            dev_id,
            desc,
            hdmi_monitors,
            pulse_hdmi_count=pulse_hdmi_count,
        )
        kind = (
            "hdmi"
            if _is_pulse_display_sink(dev_id, desc)
            else (
                "analog"
                if ("analog" in low_id or "analog" in low_desc or "mailbox" in low_id)
                else "other"
            )
        )
        backend = "pipewire" if low_id.startswith("pipewire/") else "pulse"
        pulse_out.append(
            {"id": dev_id, "label": label, "desc": label, "backend": backend, "kind": kind}
        )

    if pulse_out:
        # On Raspberry Pi hosts where PipeWire only exposes the 3.5mm mailbox jack while
        # vc4-hdmi audio lives directly on ALSA, supplement with confirmed vc4hdmi outputs.
        if not any(d["kind"] == "hdmi" for d in pulse_out):
            pulse_out.extend(
                _curate_alsa_hdmi_entries(
                    entries,
                    drm_connectors=drm_connectors,
                    require_confirmed=True,
                    vc4_only=True,
                )
            )
        order = {"hdmi": 0, "analog": 1, "other": 2}
        pulse_out.sort(key=lambda d: (order.get(d["kind"], 9), d["label"].lower()))
        return _disambiguate_labels(pulse_out)

    # 2. ALSA-only fallback (headless, DRM, Raspberry Pi without PipeWire/Pulse)
    alsa_out: list[dict[str, str]] = _curate_alsa_hdmi_entries(
        entries,
        drm_connectors=drm_connectors,
        require_confirmed=False,
    )
    seen_hw: set[tuple[str, str]] = set()
    seen_ids = {d["id"] for d in alsa_out}

    # Pass B: Physical analog / USB / digital outputs via iec958 or plughw (skip HDMI plughw duplicates)
    # Process iec958 before plughw so dedicated S/PDIF PCMs win over generic digital plughw entries
    # regardless of whether entries came from `mpv` or `aplay -L`.
    pass_b_entries = sorted(
        entries,
        key=lambda item: (0 if _normalize_for_mpv(item[0]).lower().startswith("alsa/iec958:") else 1),
    )
    for dev_id, desc in pass_b_entries:
        norm = _normalize_for_mpv(dev_id)
        low = norm.lower()
        if not (low.startswith("alsa/plughw:") or low.startswith("alsa/iec958:")):
            continue
        head = desc.split("/", 1)[0]
        low_head = head.lower()
        if "hdmi" in low_head or "nvidia" in low_head or "vc4hdmi" in low:
            continue
        m = _ALSA_CARD_DEV_RE.search(norm)
        card = (m.group(1) if m else "").lower()
        dev_num = m.group(2) if m and m.group(2) is not None else "0"
        is_digital = low.startswith("alsa/iec958:") or "digital" in low_head or "spdif" in low_head
        hw_key = (card, "digital" if is_digital else f"analog:{dev_num}")
        if hw_key in seen_hw or norm in seen_ids:
            continue
        seen_hw.add(hw_key)
        seen_ids.add(norm)
        label = _human_label_for_alsa(norm, desc)
        kind = "digital" if is_digital else "analog"
        alsa_out.append({"id": norm, "label": label, "desc": label, "backend": "alsa", "kind": kind})

    order = {"hdmi": 0, "analog": 1, "digital": 2}
    alsa_out.sort(key=lambda d: (order.get(d["kind"], 9), d["label"].lower()))
    return _disambiguate_labels(alsa_out)


def _disambiguate_labels(devices: list[dict[str, str]]) -> list[dict[str, str]]:
    """Ensure every curated audio option has a distinct human-readable label."""
    counts: dict[str, int] = {}
    for d in devices:
        key = d.get("label", "").lower()
        counts[key] = counts.get(key, 0) + 1
    seen: dict[str, int] = {}
    for d in devices:
        label = d.get("label", "")
        key = label.lower()
        if counts.get(key, 0) > 1:
            idx = seen.get(key, 0) + 1
            seen[key] = idx
            d["label"] = f"{label} ({idx})"
            d["desc"] = d["label"]
    return devices


def list_audio_devices(drm_connectors: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """Return curated, viable playback outputs with human-readable labels."""
    connectors = drm_connectors if drm_connectors is not None else list_drm_connectors()
    mpv_entries = _probe_mpv_audio_devices()
    if mpv_entries:
        curated = _curate_audio_devices(mpv_entries, drm_connectors=connectors)
        if curated:
            return curated
    # Fallback to `aplay -L` if mpv probe is unavailable
    alsa_entries = [(d.get("id", ""), d.get("desc", "")) for d in list_alsa_devices() if d.get("id")]
    return _curate_audio_devices(alsa_entries, drm_connectors=connectors)


def _get_active_drm_connector() -> str:
    connected = [
        c for c in list_drm_connectors() if str(c.get("status", "")).lower() == "connected"
    ]
    return str(connected[0].get("connector") or "").strip() if connected else ""


def detect_audio_device(
    drm_connector: str = "",
    *,
    drm_connectors: list[dict[str, Any]] | None = None,
) -> str:
    """Best-effort HDMI-aware ALSA device detection for direct-ALSA hosts.

    Returns a normalized mpv ALSA device id when direct ALSA HDMI should be
    selected, or "" when PipeWire/PulseAudio is active or no HDMI sink is
    confirmed so mpv uses its system default ('auto').
    """
    mpv_entries = _probe_mpv_audio_devices()
    connectors = drm_connectors if drm_connectors is not None else list_drm_connectors()
    viable_sound_server = [
        (dev_id, desc)
        for dev_id, desc in mpv_entries
        if _is_viable_sound_server_sink(dev_id, desc)
    ]
    if viable_sound_server:
        # On Raspberry Pi, PipeWire/PulseAudio often exposes only the 3.5mm
        # mailbox jack while leaving connected vc4hdmi* outputs on direct ALSA.
        # When no sound-server HDMI/DP sink exists and a vc4hdmi* port is
        # confirmed connected, prefer it so Auto plays to the TV over HDMI
        # instead of silently routing to the unplugged 3.5mm jack.
        if not any(_is_pulse_display_sink(dev_id, desc) for dev_id, desc in viable_sound_server):
            vc4_hdmi = _curate_alsa_hdmi_entries(
                mpv_entries,
                drm_connectors=connectors,
                require_confirmed=True,
                vc4_only=True,
            )
            if vc4_hdmi:
                explicit_connector = (drm_connector or "").strip()
                target_connector = explicit_connector
                if not target_connector:
                    connected_hdmi = [
                        str(c.get("connector") or "").strip()
                        for c in connectors
                        if str(c.get("status", "")).strip().lower() == "connected"
                        and "hdmi" in str(c.get("connector") or "").lower()
                    ]
                    target_connector = connected_hdmi[0] if connected_hdmi else ""
                idx = (
                    _connector_index(target_connector)
                    if "hdmi" in target_connector.lower()
                    else None
                )
                if idx is not None and idx > 0:
                    for item in vc4_hdmi:
                        low = item["id"].lower()
                        if f"hdmi{idx - 1}" in low or (idx == 1 and "card=vc4hdmi," in low):
                            return item["id"]
                return vc4_hdmi[0]["id"]
        return ""

    alsa_entries = [
        (dev_id, desc)
        for dev_id, desc in mpv_entries
        if _normalize_for_mpv(dev_id).lower().startswith("alsa/")
    ]
    if not alsa_entries:
        alsa_entries = [
            (d.get("id", ""), d.get("desc", "")) for d in list_alsa_devices() if d.get("id")
        ]
    if not alsa_entries:
        return ""

    explicit_connector = (drm_connector or "").strip()
    target_connector = explicit_connector
    if not target_connector:
        connected_hdmi = [
            str(c.get("connector") or "").strip()
            for c in connectors
            if str(c.get("status", "")).strip().lower() == "connected"
            and "hdmi" in str(c.get("connector") or "").lower()
        ]
        target_connector = connected_hdmi[0] if connected_hdmi else ""

    # Only map connector index for HDMI connectors. DP/eDP index values do not
    # correspond to ALSA HDMI DEV numbering and can pick the wrong sink.
    idx = _connector_index(target_connector) if "hdmi" in target_connector.lower() else None

    confirmed_hdmi = _curate_alsa_hdmi_entries(
        alsa_entries,
        drm_connectors=connectors,
        require_confirmed=True,
    )

    if idx is not None and idx > 0:
        for item in confirmed_hdmi:
            low = item["id"].lower()
            if f"hdmi{idx - 1}" in low or f"dev={idx - 1}" in low:
                return item["id"]
        candidates = _curate_alsa_hdmi_entries(
            alsa_entries,
            drm_connectors=(None if explicit_connector else connectors),
            require_confirmed=False,
        )
        for item in candidates:
            low = item["id"].lower()
            if f"hdmi{idx - 1}" in low or f"dev={idx - 1}" in low:
                return item["id"]
        if explicit_connector:
            for dev_id, _desc in alsa_entries:
                norm = _normalize_for_mpv(dev_id)
                low = norm.lower()
                if low.startswith("alsa/hdmi:") and (
                    f"hdmi{idx - 1}" in low or f"dev={idx - 1}" in low
                ):
                    return norm

    if confirmed_hdmi:
        for item in confirmed_hdmi:
            if "dev=0" in item["id"].lower():
                return item["id"]
        return confirmed_hdmi[0]["id"]

    return ""


def discover() -> dict:
    cec_probe = cec_client_probe()
    profile: dict[str, Any] = {}
    try:
        from . import video_profile

        profile = video_profile.get_profile()
    except Exception:
        profile = {}
    drm_connectors = list_drm_connectors()
    return {
        "drm_connectors": drm_connectors,
        "audio_devices": list_audio_devices(drm_connectors=drm_connectors),
        "alsa_devices": list_alsa_devices(),
        "auto_audio_device": detect_audio_device(drm_connectors=drm_connectors),
        "cec_devices": list_cec_devices(),
        "cec_client_available": cec_probe.get("cec_client_available", False),
        "cec_adapters_reported": cec_probe.get("adapters_reported", []),
        "has_dri": os.path.exists("/dev/dri"),
        "has_snd": os.path.exists("/dev/snd"),
        "display": os.getenv("DISPLAY") or "",
        "video_profile": profile,
    }
