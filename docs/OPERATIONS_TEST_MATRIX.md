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

Hardware validation is manual. Every profile starts with the same three
steps, then adds profile-specific checks.

Common steps (all profiles):

1. `PYTHONPATH=app pytest -q tests/test_runtime_matrix.py` — the decision
   policy itself is green on the checkout being validated.
2. `./scripts/doctor.sh` on the host — records session type, display
   env, GPU/DRI state, and overlay caveats for the report.
3. After bring-up, verify the profile's row against the live runtime:

   ```bash
   curl -sS http://127.0.0.1:8787/runtime/capabilities | python3 -c '
   import json,sys
   d=json.load(sys.stdin)
   for k in ("player_backend","configured_player_backend","qt_runtime_mode_configured",
             "qt_runtime_mode_effective","player_runtime_engine","backend_ready",
             "backend_runtime_mismatch","host_session_type","decode_profile","av1_allowed"):
       print(f"{k}: {d.get(k)}")'
   ```

   `backend_runtime_mismatch` must be `False` and the mode/decode fields
   must match the profile's row in the decision table above.

### `amd64-x11-native-qt-embed` (product default)

- Bring-up: `./scripts/host-ops.sh up --x11-native --native-playback`
- Gate: `./scripts/host-ops.sh native-ready --wait 25` exits 0.
- Expect: `player_backend=qt`, `qt_runtime_mode_effective=embed`,
  `player_runtime_engine=qt_shell`, `decode_profile=intel_amd64_qsv` (QSV
  hosts) or `intel_amd64_vaapi`.
- Contract: `./scripts/host-ops.sh acceptance --no-up` (native telemetry,
  control acks, overlay deliverability, YouTube pipeline).
- Telemetry deep-check when touching player/qt_shell code:
  `./scripts/validate-native-qt-telemetry.sh`.
- Soak: follow "Native Soak" and "Acceptance + Overnight Playbook" in
  `NATIVE_RUNTIME_OPERATIONS.md`.

### `amd64-wayland-native-qt-embed`

- Bring-up: `./scripts/host-ops.sh up --wayland-native --stable-playback`
- Gate: `native-ready --wait 25` exits 0.
- Expect: same as the x11 row but `host_session_type=wayland`; note the
  host X11 overlay fallback is disabled under Wayland (`doctor.sh` prints
  the caveat) — native Qt overlay/toast is the only overlay path, so also
  verify a toast renders (`POST /overlay/toast`).

### `amd64-wayland-qt-external-mpv`

- Enable: set `RELAYTV_QT_RUNTIME_MODE=external_mpv` in `.env`, then
  recreate the container.
- Expect: `qt_runtime_mode_effective=external_mpv`,
  `player_runtime_engine=qt_external_mpv` during playback, `mpv_pid` set
  and `ipc_socket_exists=True`.
- Play/pause/seek/volume smoke via `/ui`, then remove the override and
  confirm the profile returns to `embed`.

### `amd64-unmanaged-wayland-auto`

- Setup: leave `RELAYTV_MODE` unset on a Wayland session host.
- Expect: `qt_runtime_mode_configured=auto`,
  `qt_runtime_mode_effective=external_mpv` — pins the auto fallback that
  protects unmanaged installs.

### `headless-remote`

- Bring-up: install with `--mode headless` (or `RELAYTV_MODE=headless`).
- Expect: `headless_runtime=True`, `player_backend=mpv` when the classic
  backend is selected, `RELAYTV_HEADLESS_REMOTE_ENABLED=1` in the container
  env (`docker exec relaytv env | grep HEADLESS`), `GET /health` ok, `/ui`
  reachable from another machine, playback controllable remotely.

### `raspi-wayland-native-qt-embed`

- Bring-up: `host-ops.sh up --wayland-native --stable-playback` on the Pi.
- Expect: GLES mpv args injected
  (`docker exec relaytv env | grep RELAYTV_QT_SHELL_MPV_ARGS` →
  `--gpu-api=opengl --opengl-es=yes`), `decode_profile=arm_safe`,
  `av1_allowed=False` (AV1 content must transcode or fall back), playback
  smoke with a 1080p H.264 stream.
- See also "Raspberry Pi Notes" in `INSTALL.md`.

### `arm64-x11-native-qt-embed`

- Bring-up: `host-ops.sh up --x11-native --native-playback` on the arm64
  box.
- Expect: `decode_profile=arm_safe`, `av1_allowed=False`, native Qt embed
  fields as in the amd64 x11 row.

## Reporting

Record validation runs (date, image tag, host, profile, pass/fail plus
`doctor.sh` output) in the release notes or the operations log for the
deployment — this file defines the checks, it does not store results.
