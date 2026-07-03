# SPDX-License-Identifier: GPL-3.0-only
"""Behavior tests for the shared env parsing helpers and RuntimeConfig (Phase 2 M2/M3)."""
import os

import pytest

from relaytv_app import config


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        (" YES ", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("off", False),
        ("enabled", False),  # env_bool never accepted extended spellings
        ("garbage", False),
        ("", False),
    ],
)
def test_env_bool_spellings(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("RELAYTV_TEST_FLAG", raw)
    assert config.env_bool("RELAYTV_TEST_FLAG") is expected


def test_env_bool_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELAYTV_TEST_FLAG", raising=False)
    assert config.env_bool("RELAYTV_TEST_FLAG") is False
    assert config.env_bool("RELAYTV_TEST_FLAG", default=True) is True


@pytest.mark.parametrize(
    ("raw", "strict", "extended"),
    [
        ("1", True, True),
        ("on", True, True),
        ("0", False, False),
        ("off", False, False),
        ("enabled", None, True),
        ("enable", None, True),
        ("disabled", None, False),
        ("disable", None, False),
        ("garbage", None, None),
        ("", None, None),
    ],
)
def test_env_choice_strict_vs_extended(
    monkeypatch: pytest.MonkeyPatch, raw: str, strict: bool | None, extended: bool | None
) -> None:
    monkeypatch.setenv("RELAYTV_TEST_CHOICE", raw)
    assert config.env_choice("RELAYTV_TEST_CHOICE") is strict
    assert config.env_choice("RELAYTV_TEST_CHOICE", extended=True) is extended


def test_env_choice_unset_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELAYTV_TEST_CHOICE", raising=False)
    assert config.env_choice("RELAYTV_TEST_CHOICE") is None
    assert config.env_choice("RELAYTV_TEST_CHOICE", extended=True) is None


def test_env_int_parses_clamps_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYTV_TEST_INT", " 42 ")
    assert config.env_int("RELAYTV_TEST_INT", 7) == 42
    assert config.env_int("RELAYTV_TEST_INT", 7, minimum=50) == 50
    assert config.env_int("RELAYTV_TEST_INT", 7, maximum=10) == 10
    monkeypatch.setenv("RELAYTV_TEST_INT", "not-a-number")
    assert config.env_int("RELAYTV_TEST_INT", 7) == 7
    monkeypatch.delenv("RELAYTV_TEST_INT", raising=False)
    assert config.env_int("RELAYTV_TEST_INT", 7) == 7


def test_env_float_parses_clamps_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYTV_TEST_FLOAT", "2.5")
    assert config.env_float("RELAYTV_TEST_FLOAT", 1.0) == 2.5
    assert config.env_float("RELAYTV_TEST_FLOAT", 1.0, minimum=3.0, maximum=9.0) == 3.0
    assert config.env_float("RELAYTV_TEST_FLOAT", 1.0, minimum=0.5, maximum=2.0) == 2.0
    monkeypatch.setenv("RELAYTV_TEST_FLOAT", "bogus")
    assert config.env_float("RELAYTV_TEST_FLOAT", 1.5) == 1.5
    monkeypatch.delenv("RELAYTV_TEST_FLOAT", raising=False)
    assert config.env_float("RELAYTV_TEST_FLOAT", 1.5) == 1.5


def test_env_str_strips_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYTV_TEST_STR", "  hello  ")
    assert config.env_str("RELAYTV_TEST_STR") == "hello"
    monkeypatch.delenv("RELAYTV_TEST_STR", raising=False)
    assert config.env_str("RELAYTV_TEST_STR") == ""
    assert config.env_str("RELAYTV_TEST_STR", "fallback") == "fallback"


def test_runtime_config_set_env_dual_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    rc = config.RuntimeConfig()
    monkeypatch.delenv("RELAYTV_QUALITY_MODE", raising=False)

    rc.set_env("RELAYTV_QUALITY_MODE", "manual")

    assert os.environ["RELAYTV_QUALITY_MODE"] == "manual"
    assert rc.snapshot().raw("RELAYTV_QUALITY_MODE") == "manual"
    monkeypatch.delenv("RELAYTV_QUALITY_MODE", raising=False)


def test_runtime_config_refresh_captures_operator_env(monkeypatch: pytest.MonkeyPatch) -> None:
    rc = config.RuntimeConfig()
    monkeypatch.setenv("RELAYTV_VIDEO_MODE", "drm")
    monkeypatch.setenv("RELAYTV_IDLE_QR_SIZE", "300")
    monkeypatch.delenv("RELAYTV_SUB_LANG", raising=False)

    snap = rc.refresh_from_env()

    assert snap.raw("RELAYTV_VIDEO_MODE") == "drm"
    assert snap.integer("RELAYTV_IDLE_QR_SIZE", 0) == 300
    assert snap.raw("RELAYTV_SUB_LANG") is None
    assert snap.text("RELAYTV_SUB_LANG", "en") == "en"


def test_runtime_config_snapshots_are_immutable_point_in_time_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rc = config.RuntimeConfig()
    monkeypatch.delenv("RELAYTV_QUALITY_CAP", raising=False)
    rc.set_env("RELAYTV_QUALITY_CAP", "1080p")
    before = rc.snapshot()

    rc.set_env("RELAYTV_QUALITY_CAP", "720p")

    assert before.raw("RELAYTV_QUALITY_CAP") == "1080p"
    assert rc.snapshot().raw("RELAYTV_QUALITY_CAP") == "720p"
    with pytest.raises(TypeError):
        before.values["RELAYTV_QUALITY_CAP"] = "mutated"  # type: ignore[index]
    with pytest.raises(Exception):
        before.values = {}  # type: ignore[misc]
    monkeypatch.delenv("RELAYTV_QUALITY_CAP", raising=False)


def test_snapshot_typed_accessors_mirror_env_helpers() -> None:
    snap = config.SettingsSnapshot(
        values={
            "RELAYTV_CEC_ENABLED": " ON ",
            "RELAYTV_IDLE_QR_SIZE": " 240 ",
            "RELAYTV_UPLOAD_MAX_SIZE_GB": "12.5",
            "RELAYTV_SUB_LANG": "  en  ",
            "RELAYTV_JELLYFIN_ENABLED": "garbage",
        }
    )

    assert snap.flag("RELAYTV_CEC_ENABLED") is True
    assert snap.flag("RELAYTV_JELLYFIN_ENABLED") is False
    assert snap.flag("RELAYTV_MISSING", default=True) is True
    assert snap.integer("RELAYTV_IDLE_QR_SIZE", 0) == 240
    assert snap.integer("RELAYTV_IDLE_QR_SIZE", 0, maximum=100) == 100
    assert snap.number("RELAYTV_UPLOAD_MAX_SIZE_GB", 5.0) == 12.5
    assert snap.number("RELAYTV_MISSING", 5.0) == 5.0
    assert snap.text("RELAYTV_SUB_LANG") == "en"
    assert snap.raw("RELAYTV_SUB_LANG") == "  en  "
