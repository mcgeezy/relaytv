# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from types import SimpleNamespace
import pytest

from relaytv_app import devices, player
from relaytv_app.config import runtime_config


def test_curate_audio_devices_prefers_pulse_with_human_labels() -> None:
    mpv_entries = [
        ("auto", "Autoselect device"),
        ("pulse/alsa_output.pci-0000_00_1f.3.analog-stereo", "Built-in Audio Analog Stereo"),
        ("pulse/alsa_output.pci-0000_01_00.1.hdmi-stereo", "TU104 HD Audio Controller Digital Stereo (HDMI)"),
        ("pulse/auto_null", "Dummy Output"),
        ("alsa", "Default (alsa)"),
        ("alsa/plughw:CARD=PCH,DEV=0", "HDA Intel PCH, ALC1220 Analog/Hardware device with all software conversions"),
        ("alsa/front:CARD=PCH,DEV=0", "HDA Intel PCH, ALC1220 Analog/Front output / input"),
        ("alsa/surround51:CARD=PCH,DEV=0", "HDA Intel PCH, ALC1220 Analog/5.1 Surround output"),
        ("alsa/hdmi:CARD=NVidia,DEV=0", "HDA NVidia, 27G2G8/HDMI Audio Output"),
        ("alsa/hdmi:CARD=NVidia,DEV=1", "HDA NVidia, HDMI 1/HDMI Audio Output"),
        ("alsa/dmix:CARD=PCH,DEV=0", "HDA Intel PCH, ALC1220 Analog/Direct sample mixing device"),
        ("jack", "Default (jack)"),
        ("sdl", "Default (sdl)"),
    ]

    result = devices._curate_audio_devices(mpv_entries)
    assert [d["id"] for d in result] == [
        "pulse/alsa_output.pci-0000_01_00.1.hdmi-stereo",
        "pulse/alsa_output.pci-0000_00_1f.3.analog-stereo",
    ]
    assert [d["label"] for d in result] == [
        "HDMI — 27G2G8",
        "Built-in Audio (Analog / Headphones)",
    ]


def test_curate_audio_devices_nuc_pulse_with_usb_speaker() -> None:
    mpv_entries = [
        ("auto", "Autoselect device"),
        ("pulse/alsa_output.usb-Generic_USB_Audio-00.analog-stereo", "USB Audio Analog Stereo"),
        ("pulse/alsa_output.pci-0000_00_1f.3.hdmi-stereo", "Built-in Audio Digital Stereo (HDMI)"),
        ("pulse/alsa_output.usb-eMeet_eMeet_Luna_20080411-00.mono-fallback", "eMeet Luna Mono"),
        ("alsa/hdmi:CARD=PCH,DEV=0", "HDA Intel PCH, E601i-A3/HDMI Audio Output"),
        ("alsa/hdmi:CARD=PCH,DEV=1", "HDA Intel PCH, HDMI 1/HDMI Audio Output"),
    ]

    result = devices._curate_audio_devices(mpv_entries)
    assert [d["label"] for d in result] == [
        "HDMI — E601i-A3",
        "USB Audio (Analog)",
        "eMeet Luna",
    ]


def test_curate_audio_devices_alsa_only_dedups_and_hides_unconnected_hdmi() -> None:
    mpv_entries = [
        ("auto", "Autoselect device"),
        ("alsa", "Default (alsa)"),
        ("alsa/plughw:CARD=PCH,DEV=0", "HDA Intel PCH, ALC1220 Analog/Hardware device with all software conversions"),
        ("alsa/plughw:CARD=PCH,DEV=1", "HDA Intel PCH, ALC1220 Digital/Hardware device with all software conversions"),
        ("alsa/front:CARD=PCH,DEV=0", "HDA Intel PCH, ALC1220 Analog/Front output / input"),
        ("alsa/dmix:CARD=PCH,DEV=0", "HDA Intel PCH, ALC1220 Analog/Direct sample mixing device"),
        ("alsa/surround71:CARD=PCH,DEV=0", "HDA Intel PCH, ALC1220 Analog/7.1 Surround output"),
        ("alsa/iec958:CARD=PCH,DEV=0", "HDA Intel PCH, ALC1220 Digital/IEC958 (S/PDIF) Digital Audio Output"),
        ("alsa/hdmi:CARD=NVidia,DEV=0", "HDA NVidia, 27G2G8/HDMI Audio Output"),
        ("alsa/hdmi:CARD=NVidia,DEV=1", "HDA NVidia, HDMI 1/HDMI Audio Output"),
        ("alsa/hdmi:CARD=NVidia,DEV=2", "HDA NVidia, HDMI 2/HDMI Audio Output"),
        ("alsa/plughw:CARD=NVidia,DEV=3", "HDA NVidia, 27G2G8/Hardware device with all software conversions"),
        ("alsa/plughw:CARD=Luna,DEV=0", "eMeet Luna, USB Audio/Hardware device with all software conversions"),
        ("jack", "Default (jack)"),
        ("sdl", "Default (sdl)"),
    ]

    result = devices._curate_audio_devices(mpv_entries)
    assert [d["id"] for d in result] == [
        "alsa/hdmi:CARD=NVidia,DEV=0",
        "alsa/plughw:CARD=PCH,DEV=0",
        "alsa/plughw:CARD=Luna,DEV=0",
        "alsa/iec958:CARD=PCH,DEV=0",
    ]
    assert [d["label"] for d in result] == [
        "HDMI — 27G2G8",
        "Built-in Audio (Analog / Headphones)",
        "eMeet Luna (USB Audio)",
        "Built-in Audio (Digital / Optical)",
    ]


def test_curate_audio_devices_disambiguates_duplicate_labels() -> None:
    mpv_entries = [
        ("pulse/alsa_output.usb-Generic_A.analog-stereo", "USB Audio Analog Stereo"),
        ("pulse/alsa_output.usb-Generic_B.analog-stereo", "USB Audio Analog Stereo"),
    ]
    result = devices._curate_audio_devices(mpv_entries)
    assert [d["label"] for d in result] == [
        "USB Audio (Analog) (1)",
        "USB Audio (Analog) (2)",
    ]


def test_curate_audio_devices_raspi_supplements_connected_hdmi_and_hides_disconnected() -> None:
    mpv_entries = [
        ("auto", "Autoselect device"),
        ("pulse/alsa_output.platform-fe00b840.mailbox.pro-output-0", "Built-in Audio Pro"),
        ("alsa", "Default (alsa)"),
        ("alsa/plughw:CARD=vc4hdmi0,DEV=0", "vc4-hdmi-0, MAI PCM i2s-hifi-0/Hardware device with all software conversions"),
        ("alsa/hdmi:CARD=vc4hdmi0,DEV=0", "vc4-hdmi-0, MAI PCM i2s-hifi-0/HDMI Audio Output"),
        ("alsa/dmix:CARD=vc4hdmi0,DEV=0", "vc4-hdmi-0, MAI PCM i2s-hifi-0/Direct sample mixing device"),
        ("alsa/hdmi:CARD=vc4hdmi1,DEV=0", "vc4-hdmi-1, MAI PCM i2s-hifi-0/HDMI Audio Output"),
        ("alsa/plughw:CARD=Headphones,DEV=0", "bcm2835 Headphones, bcm2835 Headphones/Hardware device with all software conversions"),
        ("jack", "Default (jack)"),
        ("sdl", "Default (sdl)"),
    ]
    drm_connectors = [
        {"connector": "HDMI-A-1", "status": "connected"},
        {"connector": "HDMI-A-2", "status": "disconnected"},
    ]

    result = devices._curate_audio_devices(mpv_entries, drm_connectors=drm_connectors)
    assert [d["id"] for d in result] == [
        "alsa/hdmi:CARD=vc4hdmi0,DEV=0",
        "pulse/alsa_output.platform-fe00b840.mailbox.pro-output-0",
    ]
    assert [d["label"] for d in result] == [
        "HDMI 1",
        "Built-in Audio (Analog / Headphones)",
    ]


def test_normalize_for_mpv_preserves_pulse_and_pipewire() -> None:
    assert (
        devices._normalize_for_mpv("pulse/alsa_output.pci-0000_00_1f.3.analog-stereo")
        == "pulse/alsa_output.pci-0000_00_1f.3.analog-stereo"
    )
    assert devices._normalize_for_mpv("pipewire/my:sink") == "pipewire/my:sink"
    assert devices._normalize_for_mpv("hdmi:CARD=NVidia,DEV=0") == "alsa/hdmi:CARD=NVidia,DEV=0"


def test_probe_mpv_audio_devices_parses_help_output_and_handles_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_stdout = (
        "List of detected audio devices:\n"
        "  'auto' (Autoselect device)\n"
        "  'pulse/alsa_output.pci-0000_00_1f.3.analog-stereo' (Built-in Audio Analog Stereo)\n"
        "  'pipewire/alsa_output.usb-DAC-00.analog-stereo' (USB DAC Analog Stereo)\n"
        "  'alsa/hdmi:CARD=NVidia,DEV=0' (HDA NVidia, 27G2G8/HDMI Audio Output)\n"
        "\n"
    )
    monkeypatch.setattr(
        devices.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=sample_stdout, stderr=""),
    )
    assert devices._probe_mpv_audio_devices() == [
        ("auto", "Autoselect device"),
        ("pulse/alsa_output.pci-0000_00_1f.3.analog-stereo", "Built-in Audio Analog Stereo"),
        ("pipewire/alsa_output.usb-DAC-00.analog-stereo", "USB DAC Analog Stereo"),
        ("alsa/hdmi:CARD=NVidia,DEV=0", "HDA NVidia, 27G2G8/HDMI Audio Output"),
    ]

    def _boom(*_a: object, **_kw: object) -> object:
        raise FileNotFoundError("mpv")

    monkeypatch.setattr(devices.subprocess, "run", _boom)
    assert devices._probe_mpv_audio_devices() == []


def test_list_audio_devices_falls_back_to_aplay_when_mpv_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(devices, "_probe_mpv_audio_devices", lambda: [])
    monkeypatch.setattr(
        devices,
        "list_alsa_devices",
        lambda: [
            {"id": "hdmi:CARD=NVidia,DEV=0", "desc": "HDA NVidia, 27G2G8"},
            {"id": "plughw:CARD=PCH,DEV=0", "desc": "HDA Intel PCH, ALC1220 Analog"},
        ],
    )
    drm_connectors = [{"connector": "HDMI-A-1", "status": "connected"}]
    result = devices.list_audio_devices(drm_connectors=drm_connectors)
    assert [d["id"] for d in result] == [
        "alsa/hdmi:CARD=NVidia,DEV=0",
        "alsa/plughw:CARD=PCH,DEV=0",
    ]
    assert [d["label"] for d in result] == [
        "HDMI — 27G2G8",
        "Built-in Audio (Analog / Headphones)",
    ]


def test_single_hdmi_raspi_vc4hdmi_without_digit_curates_and_detects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mpv_entries = [
        ("auto", "Autoselect device"),
        ("pulse/alsa_output.platform-fe00b840.mailbox.pro-output-0", "Built-in Audio Pro"),
        ("alsa/hdmi:CARD=vc4hdmi,DEV=0", "vc4-hdmi, MAI PCM i2s-hifi-0/HDMI Audio Output"),
        ("alsa/plughw:CARD=vc4hdmi,DEV=0", "vc4-hdmi, MAI PCM i2s-hifi-0/Hardware device"),
    ]
    connected = [{"connector": "HDMI-A-1", "status": "connected"}]
    disconnected = [{"connector": "HDMI-A-1", "status": "disconnected"}]

    curated_conn = devices._curate_audio_devices(mpv_entries, drm_connectors=connected)
    assert [(d["id"], d["label"]) for d in curated_conn] == [
        ("alsa/hdmi:CARD=vc4hdmi,DEV=0", "HDMI 1"),
        (
            "pulse/alsa_output.platform-fe00b840.mailbox.pro-output-0",
            "Built-in Audio (Analog / Headphones)",
        ),
    ]

    curated_disc = devices._curate_audio_devices(mpv_entries, drm_connectors=disconnected)
    assert [d["id"] for d in curated_disc] == [
        "pulse/alsa_output.platform-fe00b840.mailbox.pro-output-0",
    ]

    # ALSA-only single-HDMI Pi auto-detection
    alsa_only_entries = [e for e in mpv_entries if not e[0].startswith("pulse/")]
    monkeypatch.setattr(devices, "_probe_mpv_audio_devices", lambda: alsa_only_entries)
    assert devices.detect_audio_device(drm_connectors=connected) == "alsa/hdmi:CARD=vc4hdmi,DEV=0"
    assert devices.detect_audio_device(drm_connectors=disconnected) == ""


def test_non_pi_pulse_host_does_not_inject_raw_alsa_hdmi_when_pulse_has_only_analog_or_usb() -> None:
    mpv_entries = [
        ("auto", "Autoselect device"),
        ("pulse/alsa_output.pci-0000_00_1f.3.analog-stereo", "Built-in Audio Analog Stereo"),
        ("pulse/alsa_output.usb-Generic_USB_Audio-00.analog-stereo", "USB Audio Analog Stereo"),
        ("alsa/hdmi:CARD=PCH,DEV=0", "HDA Intel PCH, 27G2G8/HDMI Audio Output"),
        ("alsa/hdmi:CARD=NVidia,DEV=0", "HDA NVidia, LG TV/HDMI Audio Output"),
    ]
    drm_connectors = [{"connector": "HDMI-A-1", "status": "connected"}]

    result = devices._curate_audio_devices(mpv_entries, drm_connectors=drm_connectors)
    assert [d["id"] for d in result] == [
        "pulse/alsa_output.pci-0000_00_1f.3.analog-stereo",
        "pulse/alsa_output.usb-Generic_USB_Audio-00.analog-stereo",
    ]


def test_pulse_multi_gpu_hdmi_displayport_commaless_alsa_and_pipewire_backend() -> None:
    # Comma-less ALSA HDMI description must not treat card name as a monitor name
    assert (
        devices._extract_hdmi_monitor_names(
            [
                ("alsa/hdmi:CARD=NVidia,DEV=0", "HDA NVidia/HDMI Audio Output"),
                ("hdmi:CARD=PCH,DEV=0", "HDA Intel PCH"),
            ]
        )
        == []
    )

    # Multi-GPU host with 2 display sinks (1 HDMI, 1 DisplayPort) + 1 ELD monitor name:
    # must NOT collide both sinks onto "HDMI — 27G2G8", and pipewire/ must report backend="pipewire".
    mpv_entries = [
        ("pipewire/alsa_output.pci-0000_00_1f.3.hdmi-stereo", "Built-in Audio Digital Stereo (HDMI)"),
        (
            "pipewire/alsa_output.pci-0000_01_00.1.dp-stereo",
            "Navi 21/23 HDMI/DP Audio Controller Digital Stereo (DisplayPort 2)",
        ),
        ("pipewire/alsa_output.usb-DAC-00.analog-stereo", "USB DAC Analog Stereo"),
        ("alsa/hdmi:CARD=NVidia,DEV=0", "HDA NVidia, 27G2G8/HDMI Audio Output"),
    ]
    result = devices._curate_audio_devices(mpv_entries)
    by_id = {d["id"]: d for d in result}
    assert by_id["pipewire/alsa_output.pci-0000_00_1f.3.hdmi-stereo"] == {
        "id": "pipewire/alsa_output.pci-0000_00_1f.3.hdmi-stereo",
        "label": "HDMI Audio",
        "desc": "HDMI Audio",
        "backend": "pipewire",
        "kind": "hdmi",
    }
    assert by_id["pipewire/alsa_output.pci-0000_01_00.1.dp-stereo"] == {
        "id": "pipewire/alsa_output.pci-0000_01_00.1.dp-stereo",
        "label": "DisplayPort — Navi 21/23",
        "desc": "DisplayPort — Navi 21/23",
        "backend": "pipewire",
        "kind": "hdmi",
    }
    assert by_id["pipewire/alsa_output.usb-DAC-00.analog-stereo"]["backend"] == "pipewire"

    # Single DisplayPort sink with a single confirmed ELD monitor name uses "DisplayPort — <monitor>"
    single_dp = devices._curate_audio_devices(
        [
            (
                "pulse/alsa_output.pci-0000_01_00.1.dp-stereo",
                "TU104 HD Audio Controller Digital Stereo (DisplayPort)",
            ),
            ("alsa/hdmi:CARD=NVidia,DEV=1", "HDA NVidia, 27G2G8/HDMI Audio Output"),
        ]
    )
    assert single_dp == [
        {
            "id": "pulse/alsa_output.pci-0000_01_00.1.dp-stereo",
            "label": "DisplayPort — 27G2G8",
            "desc": "DisplayPort — 27G2G8",
            "backend": "pulse",
            "kind": "hdmi",
        }
    ]


def test_detect_audio_device_and_effective_audio_device_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Active PulseAudio / PipeWire sink -> "" so mpv uses system default ('auto')
    monkeypatch.setattr(
        devices,
        "_probe_mpv_audio_devices",
        lambda: [
            ("pulse/alsa_output.usb-Speaker.analog-stereo", "USB Speaker Analog Stereo"),
            ("alsa/hdmi:CARD=PCH,DEV=0", "HDA Intel PCH, E601i-A3/HDMI Audio Output"),
        ],
    )
    monkeypatch.setattr(
        devices,
        "list_drm_connectors",
        lambda: [{"connector": "HDMI-A-1", "status": "connected"}],
    )
    assert devices.detect_audio_device() == ""
    monkeypatch.setattr(runtime_config, "set_value", runtime_config.set_value)
    prev_mpv_audio = runtime_config.snapshot().raw("MPV_AUDIO_DEVICE")
    try:
        runtime_config.set_value("MPV_AUDIO_DEVICE", "")
        assert player._effective_audio_device({"audio_device": ""}) == ""

        # 2. Legacy unprefixed ALSA IDs in settings or MPV_AUDIO_DEVICE normalize cleanly
        assert (
            player._effective_audio_device({"audio_device": "hdmi:CARD=vc4hdmi0,DEV=0"})
            == "alsa/hdmi:CARD=vc4hdmi0,DEV=0"
        )
        runtime_config.set_value("MPV_AUDIO_DEVICE", "hdmi:CARD=PCH,DEV=1")
        assert player._effective_audio_device({"audio_device": ""}) == "alsa/hdmi:CARD=PCH,DEV=1"
        runtime_config.set_value("MPV_AUDIO_DEVICE", "")

        # 3. ALSA-only multi-port GPU with disconnected HDMI-A-1 and unconfirmed HDMI PCMs -> ""
        monkeypatch.setattr(
            devices,
            "_probe_mpv_audio_devices",
            lambda: [
                ("pulse/auto_null", "Dummy Output"),
                ("alsa/hdmi:CARD=NVidia,DEV=0", "HDA NVidia, HDMI 0/HDMI Audio Output"),
                ("alsa/hdmi:CARD=NVidia,DEV=1", "HDA NVidia, HDMI 1/HDMI Audio Output"),
            ],
        )
        monkeypatch.setattr(
            devices,
            "list_drm_connectors",
            lambda: [
                {"connector": "HDMI-A-1", "status": "disconnected"},
                {"connector": "DP-1", "status": "connected"},
            ],
        )
        assert devices.detect_audio_device() == ""

        # 4. ALSA-only multi-port GPU where DEV=1 has confirmed ELD monitor name -> picks DEV=1 (not DEV=0)
        monkeypatch.setattr(
            devices,
            "_probe_mpv_audio_devices",
            lambda: [
                ("alsa/hdmi:CARD=NVidia,DEV=0", "HDA NVidia, HDMI 0/HDMI Audio Output"),
                ("alsa/hdmi:CARD=NVidia,DEV=1", "HDA NVidia, 27G2G8/HDMI Audio Output"),
            ],
        )
        assert devices.detect_audio_device() == "alsa/hdmi:CARD=NVidia,DEV=1"

        # 5. Explicit drm_connector="HDMI-A-2" on ALSA-only host selects matching HDMI sink
        monkeypatch.setattr(
            devices,
            "_probe_mpv_audio_devices",
            lambda: [
                ("alsa/hdmi:CARD=PCH,DEV=0", "HDA Intel PCH, HDMI 0/HDMI Audio Output"),
                ("alsa/hdmi:CARD=PCH,DEV=1", "HDA Intel PCH, HDMI 1/HDMI Audio Output"),
            ],
        )
        monkeypatch.setattr(devices, "list_drm_connectors", lambda: [])
        assert devices.detect_audio_device("HDMI-A-2") == "alsa/hdmi:CARD=PCH,DEV=1"
        assert (
            player._effective_audio_device({"audio_device": "", "drm_connector": "HDMI-A-2"})
            == "alsa/hdmi:CARD=PCH,DEV=1"
        )

        # 6. Raspberry Pi with PipeWire exposing only the 3.5mm mailbox jack and
        # HDMI-A-1 connected -> Auto selects connected vc4hdmi0 instead of silent 3.5mm jack
        monkeypatch.setattr(
            devices,
            "_probe_mpv_audio_devices",
            lambda: [
                ("pulse/alsa_output.platform-fe00b840.mailbox.pro-output-0", "Built-in Audio Pro"),
                ("alsa/hdmi:CARD=vc4hdmi0,DEV=0", "vc4-hdmi-0, MAI PCM i2s-hifi-0/HDMI Audio Output"),
                ("alsa/hdmi:CARD=vc4hdmi1,DEV=0", "vc4-hdmi-1, MAI PCM i2s-hifi-0/HDMI Audio Output"),
            ],
        )
        monkeypatch.setattr(
            devices,
            "list_drm_connectors",
            lambda: [
                {"connector": "HDMI-A-1", "status": "connected"},
                {"connector": "HDMI-A-2", "status": "disconnected"},
            ],
        )
        assert devices.detect_audio_device() == "alsa/hdmi:CARD=vc4hdmi0,DEV=0"
        assert player._effective_audio_device({"audio_device": ""}) == "alsa/hdmi:CARD=vc4hdmi0,DEV=0"

        # 7. Raspberry Pi where PipeWire DOES expose an HDMI sink -> "" (let PipeWire handle Auto)
        monkeypatch.setattr(
            devices,
            "_probe_mpv_audio_devices",
            lambda: [
                ("pulse/alsa_output.platform-fef00700.hdmi.hdmi-stereo", "Built-in Audio Digital Stereo (HDMI)"),
                ("alsa/hdmi:CARD=vc4hdmi0,DEV=0", "vc4-hdmi-0, MAI PCM i2s-hifi-0/HDMI Audio Output"),
            ],
        )
        assert devices.detect_audio_device() == ""
    finally:
        runtime_config.set_value("MPV_AUDIO_DEVICE", prev_mpv_audio or "")
