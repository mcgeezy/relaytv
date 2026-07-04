# SPDX-License-Identifier: GPL-3.0-only
"""Machine-checked runtime profile matrix (docs/ARCHITECTURE_PHASE_6_ROADMAP.md).

Pins the runtime decisions RelayTV makes per operations profile — host
profile detection, entrypoint defaults, Qt runtime mode, and decode
profile — by driving the real decision functions with fake env/host
inputs. The operator-facing table in docs/OPERATIONS_TEST_MATRIX.md is
generated from RUNTIME_PROFILE_MATRIX; the pinned rows and the generated
table must change in the same commit.

Regenerate the doc table after intentional changes with:

    PYTHONPATH=app python3 tests/test_runtime_matrix.py --write

The rows use an explicit RELAYTV_PLAYER_BACKEND so the matrix stays
hermetic: the backend default path probes display sockets on the host and
is covered separately by the explicit-override tests below.
"""
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from relaytv_app import container_entrypoint, player, video_profile


DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "OPERATIONS_TEST_MATRIX.md"
BEGIN_MARK = "<!-- BEGIN GENERATED RUNTIME PROFILE MATRIX (tests/test_runtime_matrix.py) -->"
END_MARK = "<!-- END GENERATED RUNTIME PROFILE MATRIX (tests/test_runtime_matrix.py) -->"

# Env variables the matrix controls; cleared before each row is applied.
_MATRIX_ENV_VARS = (
    "RELAYTV_MODE",
    "RELAYTV_HOST_SESSION_TYPE",
    "XDG_SESSION_TYPE",
    "RELAYTV_PLAYER_BACKEND",
    "RELAYTV_QT_RUNTIME_MODE",
    "RELAYTV_HOST_PROFILE",
    "RELAYTV_HEADLESS_REMOTE_ENABLED",
    "RELAYTV_QT_SHELL_MPV_ARGS",
    "RELAYTV_QT_SHELL_MODULE",
    "QT_QPA_PLATFORM",
    "RELAYTV_VIDEO_PROFILE_ALLOW_AV1",
)


RUNTIME_PROFILE_MATRIX = [
    {
        "profile": "amd64-x11-native-qt-embed",
        "description": "amd64 mini PC, X11 session, native Qt embedded (product default)",
        "env": {
            "RELAYTV_MODE": "x11",
            "RELAYTV_HOST_SESSION_TYPE": "x11",
            "RELAYTV_PLAYER_BACKEND": "qt",
        },
        "host": {"machine": "x86_64", "model": "", "has_dri": True},
        "decode": {"hwaccels": ["qsv", "vaapi"], "av1_paths": ["libdav1d"]},
        "expected": {
            "host_profile": "amd64",
            "entrypoint_defaults": {"RELAYTV_QT_RUNTIME_MODE": "embed"},
            "qt_backend_enabled": True,
            "qt_runtime_mode_configured": "embed",
            "qt_runtime_mode_effective": "embed",
            "decode_profile": "intel_amd64_qsv",
            "av1_allowed": True,
        },
    },
    {
        "profile": "amd64-wayland-native-qt-embed",
        "description": "amd64, Wayland session, native Qt embedded (installer default for wayland mode; live appliance)",
        "env": {
            "RELAYTV_MODE": "wayland",
            "RELAYTV_HOST_SESSION_TYPE": "wayland",
            "RELAYTV_PLAYER_BACKEND": "qt",
        },
        "host": {"machine": "x86_64", "model": "", "has_dri": True},
        "decode": {"hwaccels": ["qsv", "vaapi"], "av1_paths": ["libdav1d"]},
        "expected": {
            "host_profile": "amd64",
            "entrypoint_defaults": {"RELAYTV_QT_RUNTIME_MODE": "embed"},
            "qt_backend_enabled": True,
            "qt_runtime_mode_configured": "embed",
            "qt_runtime_mode_effective": "embed",
            "decode_profile": "intel_amd64_qsv",
            "av1_allowed": True,
        },
    },
    {
        "profile": "amd64-wayland-qt-external-mpv",
        "description": "amd64, Wayland session, Qt shell with external mpv (explicit operator opt-in)",
        "env": {
            "RELAYTV_MODE": "wayland",
            "RELAYTV_HOST_SESSION_TYPE": "wayland",
            "RELAYTV_PLAYER_BACKEND": "qt",
            "RELAYTV_QT_RUNTIME_MODE": "external_mpv",
        },
        "host": {"machine": "x86_64", "model": "", "has_dri": True},
        "decode": {"hwaccels": ["vaapi"], "av1_paths": ["libdav1d"]},
        "expected": {
            "host_profile": "amd64",
            "entrypoint_defaults": {},
            "qt_backend_enabled": True,
            "qt_runtime_mode_configured": "external_mpv",
            "qt_runtime_mode_effective": "external_mpv",
            "decode_profile": "intel_amd64_vaapi",
            "av1_allowed": True,
        },
    },
    {
        "profile": "amd64-unmanaged-wayland-auto",
        "description": "amd64, RELAYTV_MODE unset with a Wayland session: auto resolves to external mpv",
        "env": {
            "RELAYTV_HOST_SESSION_TYPE": "wayland",
            "RELAYTV_PLAYER_BACKEND": "qt",
        },
        "host": {"machine": "x86_64", "model": "", "has_dri": True},
        "decode": {"hwaccels": ["vaapi"], "av1_paths": ["libdav1d"]},
        "expected": {
            "host_profile": "amd64",
            "entrypoint_defaults": {},
            "qt_backend_enabled": True,
            "qt_runtime_mode_configured": "auto",
            "qt_runtime_mode_effective": "external_mpv",
            "decode_profile": "intel_amd64_vaapi",
            "av1_allowed": True,
        },
    },
    {
        "profile": "headless-remote",
        "description": "Headless host, classic mpv backend, remote playback surface enabled by default",
        "env": {
            "RELAYTV_MODE": "headless",
            "RELAYTV_PLAYER_BACKEND": "mpv",
        },
        "host": {"machine": "x86_64", "model": "", "has_dri": False},
        "decode": {"hwaccels": [], "av1_paths": []},
        "expected": {
            "host_profile": "amd64",
            "entrypoint_defaults": {"RELAYTV_HEADLESS_REMOTE_ENABLED": "1"},
            "qt_backend_enabled": False,
            "qt_runtime_mode_configured": "auto",
            "qt_runtime_mode_effective": "embed",
            "decode_profile": "software",
            "av1_allowed": False,
        },
    },
    {
        "profile": "raspi-wayland-native-qt-embed",
        "description": "Raspberry Pi, Wayland session via xcb QPA, native Qt embedded with GLES mpv args",
        "env": {
            "RELAYTV_MODE": "wayland",
            "RELAYTV_HOST_SESSION_TYPE": "wayland",
            "RELAYTV_PLAYER_BACKEND": "qt",
            "QT_QPA_PLATFORM": "xcb",
        },
        "host": {"machine": "aarch64", "model": "Raspberry Pi 5 Model B Rev 1.0", "has_dri": True},
        "decode": {"hwaccels": ["drm"], "av1_paths": ["libdav1d"]},
        "expected": {
            "host_profile": "raspi",
            "entrypoint_defaults": {
                "RELAYTV_QT_RUNTIME_MODE": "embed",
                "RELAYTV_QT_SHELL_MPV_ARGS": "--gpu-api=opengl --opengl-es=yes",
            },
            "qt_backend_enabled": True,
            "qt_runtime_mode_configured": "embed",
            "qt_runtime_mode_effective": "embed",
            "decode_profile": "arm_safe",
            "av1_allowed": False,
        },
    },
    {
        "profile": "arm64-x11-native-qt-embed",
        "description": "Generic arm64 box (not a Raspberry Pi), X11 session, native Qt embedded",
        "env": {
            "RELAYTV_MODE": "x11",
            "RELAYTV_HOST_SESSION_TYPE": "x11",
            "RELAYTV_PLAYER_BACKEND": "qt",
        },
        "host": {"machine": "aarch64", "model": "", "has_dri": True},
        "decode": {"hwaccels": ["v4l2m2m"], "av1_paths": []},
        "expected": {
            "host_profile": "arm",
            "entrypoint_defaults": {"RELAYTV_QT_RUNTIME_MODE": "embed"},
            "qt_backend_enabled": True,
            "qt_runtime_mode_configured": "embed",
            "qt_runtime_mode_effective": "embed",
            "decode_profile": "arm_safe",
            "av1_allowed": False,
        },
    },
]


def _clear_matrix_env(monkeypatch) -> None:
    for var in _MATRIX_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _fake_host(monkeypatch, host: dict) -> None:
    monkeypatch.setattr(container_entrypoint, "_host_model", lambda: str(host.get("model") or ""))
    monkeypatch.setattr(container_entrypoint, "_has_dri", lambda: bool(host.get("has_dri")))
    monkeypatch.setattr(
        container_entrypoint.os,
        "uname",
        lambda: SimpleNamespace(machine=str(host.get("machine") or "")),
    )


@pytest.mark.parametrize("row", RUNTIME_PROFILE_MATRIX, ids=lambda r: r["profile"])
def test_runtime_profile_matrix(monkeypatch, row) -> None:
    host = row["host"]
    expected = row["expected"]
    _fake_host(monkeypatch, host)

    env = dict(row["env"])
    baseline = dict(env)
    container_entrypoint._normalize_runtime_defaults(env)

    assert container_entrypoint._host_profile(env) == expected["host_profile"]

    added = {k: v for k, v in env.items() if baseline.get(k) != v}
    assert added == expected["entrypoint_defaults"]

    _clear_matrix_env(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert player.qt_shell_backend_enabled() is expected["qt_backend_enabled"]
    assert player.qt_runtime_mode_configured() == expected["qt_runtime_mode_configured"]
    assert player.qt_runtime_mode_effective() == expected["qt_runtime_mode_effective"]

    decode = row["decode"]
    assert (
        video_profile._decode_profile(host["machine"], bool(host["has_dri"]), decode["hwaccels"])
        == expected["decode_profile"]
    )
    assert video_profile._av1_allowed(host["machine"], decode["av1_paths"]) is expected["av1_allowed"]


# --- decision edge cases not in the operator-facing table --------------------


def test_host_profile_explicit_override_wins(monkeypatch) -> None:
    _fake_host(monkeypatch, {"machine": "x86_64", "model": "Raspberry Pi 4", "has_dri": True})
    assert container_entrypoint._host_profile({"RELAYTV_HOST_PROFILE": "kiosk-a"}) == "kiosk-a"


def test_host_profile_generic_fallback(monkeypatch) -> None:
    _fake_host(monkeypatch, {"machine": "riscv64", "model": "", "has_dri": True})
    assert container_entrypoint._host_profile({}) == "generic"


def test_player_backend_explicit_classic(monkeypatch) -> None:
    _clear_matrix_env(monkeypatch)
    monkeypatch.setenv("RELAYTV_PLAYER_BACKEND", "mpv")
    assert player.qt_shell_backend_enabled() is False
    monkeypatch.setenv("RELAYTV_PLAYER_BACKEND", "qt")
    assert player.qt_shell_backend_enabled() is True


def test_decode_profile_accel_branches() -> None:
    assert video_profile._decode_profile("x86_64", True, ["cuda"]) == "nvidia_cuda"
    assert video_profile._decode_profile("riscv64", True, ["vaapi"]) == "vaapi_generic"
    assert video_profile._decode_profile("riscv64", True, ["vulkan"]) == "vulkan_generic"
    assert video_profile._decode_profile("x86_64", False, ["qsv"]) == "software"
    assert video_profile._decode_profile("x86_64", True, []) == "software"


def test_av1_env_override_beats_policy(monkeypatch) -> None:
    monkeypatch.setenv("RELAYTV_VIDEO_PROFILE_ALLOW_AV1", "0")
    assert video_profile._av1_allowed("x86_64", ["libdav1d"]) is False
    monkeypatch.setenv("RELAYTV_VIDEO_PROFILE_ALLOW_AV1", "1")
    assert video_profile._av1_allowed("aarch64", []) is True
    monkeypatch.delenv("RELAYTV_VIDEO_PROFILE_ALLOW_AV1", raising=False)
    assert video_profile._av1_allowed("x86_64", []) is False


# --- generated doc table ------------------------------------------------------


def _mode_cell(row: dict) -> str:
    return row["env"].get("RELAYTV_MODE") or "(unset)"


def _session_cell(row: dict) -> str:
    return row["env"].get("RELAYTV_HOST_SESSION_TYPE") or "(none)"


def _qt_mode_cell(row: dict) -> str:
    expected = row["expected"]
    if not expected["qt_backend_enabled"]:
        return "n/a (classic mpv)"
    configured = expected["qt_runtime_mode_configured"]
    effective = expected["qt_runtime_mode_effective"]
    return configured if configured == effective else f"{configured} → {effective}"


def _defaults_cell(row: dict) -> str:
    defaults = row["expected"]["entrypoint_defaults"]
    if not defaults:
        return "—"
    return "; ".join(f"`{k}={v}`" for k, v in sorted(defaults.items()))


def generate_matrix_table() -> str:
    lines = [
        "| Profile | `RELAYTV_MODE` | Session | Backend | Qt runtime mode | Host profile | Entrypoint defaults | Decode profile | AV1 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in RUNTIME_PROFILE_MATRIX:
        expected = row["expected"]
        lines.append(
            "| `{profile}` | `{mode}` | {session} | {backend} | {qt_mode} | `{host_profile}` | {defaults} | `{decode}` | {av1} |".format(
                profile=row["profile"],
                mode=_mode_cell(row),
                session=_session_cell(row),
                backend="qt" if expected["qt_backend_enabled"] else "classic mpv",
                qt_mode=_qt_mode_cell(row),
                host_profile=expected["host_profile"],
                defaults=_defaults_cell(row),
                decode=expected["decode_profile"],
                av1="yes" if expected["av1_allowed"] else "no",
            )
        )
    lines.append("")
    for row in RUNTIME_PROFILE_MATRIX:
        lines.append(f"- `{row['profile']}`: {row['description']}")
    return "\n".join(lines)


def _render_doc_block() -> str:
    return f"{BEGIN_MARK}\n{generate_matrix_table()}\n{END_MARK}"


def test_matrix_doc_matches_source() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    match = re.search(re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK), text, flags=re.DOTALL)
    assert match, f"generated matrix markers missing from {DOC_PATH}"
    assert match.group(0) == _render_doc_block(), (
        "docs/OPERATIONS_TEST_MATRIX.md is out of date; regenerate with "
        "`PYTHONPATH=app python3 tests/test_runtime_matrix.py --write`"
    )


def _write_doc() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK), flags=re.DOTALL)
    assert pattern.search(text), f"generated matrix markers missing from {DOC_PATH}"
    DOC_PATH.write_text(pattern.sub(lambda _m: _render_doc_block(), text), encoding="utf-8")
    print(f"wrote {DOC_PATH}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write_doc()
    else:
        print(generate_matrix_table())
