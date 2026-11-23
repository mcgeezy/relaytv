from __future__ import annotations

from relaytv_app import devices


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
