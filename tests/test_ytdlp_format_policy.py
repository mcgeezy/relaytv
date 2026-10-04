# SPDX-License-Identifier: GPL-3.0-only
import pytest
from relaytv_app.ytdlp_format_policy import normalize_quality_mode

@pytest.mark.parametrize(
    ("value", "env_auto_profile", "expected"),
    [
        ("auto", True, "auto_profile"),
        ("auto_profile", True, "auto_profile"),
        ("profile", True, "auto_profile"),
        ("manual", True, "manual"),
        ("manual", False, "manual"),
        ("something_else", True, "auto_profile"),
        ("something_else", False, "manual"),
        (None, True, "auto_profile"),
        (None, False, "manual"),
        ("", True, "auto_profile"),
        ("", False, "manual"),
        ("  AUTO  ", True, "auto_profile"),
        (" MANUAL ", True, "manual"),
    ],
)
def test_normalize_quality_mode(monkeypatch: pytest.MonkeyPatch, value: str | None, env_auto_profile: bool, expected: str) -> None:
    if env_auto_profile:
        monkeypatch.setenv("RELAYTV_AUTO_STREAM_PROFILE", "1")
    else:
        monkeypatch.setenv("RELAYTV_AUTO_STREAM_PROFILE", "0")
    assert normalize_quality_mode(value) == expected
