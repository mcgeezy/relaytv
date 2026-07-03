# SPDX-License-Identifier: GPL-3.0-only
"""Behavior tests for the shared typed env parsing helpers (Phase 2 M2)."""
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
