# Operations Test Matrix

This is the runtime validation matrix for RelayTV's supported operations
profiles. It answers two questions:

1. What runtime decisions should a given host/mode combination produce?
   (machine-checked — the table below is generated from
   `tests/test_runtime_matrix.py`, which drives the real decision functions
   with faked host inputs)
2. How does an operator validate a profile on real hardware? (the
   per-profile checklists below; hardware validation stays manual)

Regenerate the decision table after intentional changes with:

    PYTHONPATH=app python3 tests/test_runtime_matrix.py --write

## Profile Decision Table

<!-- BEGIN GENERATED RUNTIME PROFILE MATRIX (tests/test_runtime_matrix.py) -->
| Profile | `RELAYTV_MODE` | Session | Backend | Qt runtime mode | Host profile | Entrypoint defaults | Decode profile | AV1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `amd64-x11-native-qt-embed` | `x11` | x11 | qt | embed | `amd64` | `RELAYTV_QT_RUNTIME_MODE=embed` | `intel_amd64_qsv` | yes |
| `amd64-wayland-native-qt-embed` | `wayland` | wayland | qt | embed | `amd64` | `RELAYTV_QT_RUNTIME_MODE=embed` | `intel_amd64_vaapi` | yes |
| `amd64-wayland-qt-external-mpv` | `wayland` | wayland | qt | external_mpv | `amd64` | — | `intel_amd64_vaapi` | yes |
| `amd64-unmanaged-wayland-auto` | `(unset)` | wayland | qt | auto → external_mpv | `amd64` | — | `intel_amd64_vaapi` | yes |
| `headless-remote` | `headless` | (none) | classic mpv | n/a (classic mpv) | `amd64` | `RELAYTV_HEADLESS_REMOTE_ENABLED=1` | `software` | no |
| `raspi-wayland-native-qt-embed` | `wayland` | wayland | qt | embed | `raspi` | `RELAYTV_QT_RUNTIME_MODE=embed`; `RELAYTV_QT_SHELL_MPV_ARGS=--gpu-api=opengl --opengl-es=yes` | `arm_safe` | no |
| `arm64-x11-native-qt-embed` | `x11` | x11 | qt | embed | `arm` | `RELAYTV_QT_RUNTIME_MODE=embed` | `arm_safe` | no |

- `amd64-x11-native-qt-embed`: amd64 mini PC, X11 session, native Qt embedded (product default; live appliance)
- `amd64-wayland-native-qt-embed`: amd64, Wayland session, native Qt embedded (installer default for wayland mode)
- `amd64-wayland-qt-external-mpv`: amd64, Wayland session, Qt shell with external mpv (explicit operator opt-in)
- `amd64-unmanaged-wayland-auto`: amd64, RELAYTV_MODE unset with a Wayland session: auto resolves to external mpv
- `headless-remote`: Headless host, classic mpv backend, remote playback surface enabled by default
- `raspi-wayland-native-qt-embed`: Raspberry Pi, Wayland session via xcb QPA, native Qt embedded with GLES mpv args
- `arm64-x11-native-qt-embed`: Generic arm64 box (not a Raspberry Pi), X11 session, native Qt embedded
<!-- END GENERATED RUNTIME PROFILE MATRIX (tests/test_runtime_matrix.py) -->

Decision sources: `container_entrypoint._host_profile` and
`_normalize_runtime_defaults` (host profile, entrypoint defaults),
`player.qt_runtime_mode_configured`/`qt_runtime_mode_effective` (Qt runtime
mode; `auto` resolves to `external_mpv` only on Wayland sessions),
`video_profile._decode_profile`/`_av1_allowed` (decode policy). Edge cases
pinned by tests but kept out of the operator table: explicit
`RELAYTV_HOST_PROFILE` override, generic-architecture fallback,
`RELAYTV_PLAYER_BACKEND` explicit selection, nvidia/vaapi/vulkan/software
decode branches, and the `RELAYTV_VIDEO_PROFILE_ALLOW_AV1` override.

## Per-Profile Validation Checklists

(Deferred to Phase 6 M2.)
