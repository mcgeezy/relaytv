# SPDX-License-Identifier: GPL-3.0-only
import os
from unittest.mock import patch, MagicMock

import pytest

from relaytv_app.ytdlp_format_policy import (
    normalize_quality_mode,
    _parse_cap,
    extract_quality_cap_from_format,
    _display_cap_height,
    _user_cap,
    _target_cap,
    _provider_specific_env,
    _av1_allowed,
    _arm_default_quality_cap,
    _auto_provider_format,
    youtube_progressive_startup_format,
    youtube_progressive_startup_candidates,
    youtube_progressive_startup_enabled,
    _arm_safe_if_needed,
    effective_ytdlp_format,
)


def test_normalize_quality_mode():
    assert normalize_quality_mode("auto") == "auto_profile"
    assert normalize_quality_mode("auto_profile") == "auto_profile"
    assert normalize_quality_mode("profile") == "auto_profile"
    assert normalize_quality_mode("manual") == "manual"

    with patch("relaytv_app.ytdlp_format_policy._env_bool", return_value=True):
        assert normalize_quality_mode("unknown") == "auto_profile"

    with patch("relaytv_app.ytdlp_format_policy._env_bool", return_value=False):
        assert normalize_quality_mode("unknown") == "manual"


def test__parse_cap():
    assert _parse_cap(None) is None
    assert _parse_cap("") is None
    assert _parse_cap("auto") is None
    assert _parse_cap("worst") is None
    assert _parse_cap("invalid") is None
    assert _parse_cap("-10") is None
    assert _parse_cap("0") is None
    assert _parse_cap("100") == 144
    assert _parse_cap("720") == 720
    assert _parse_cap("1080.5") == 1080
    assert _parse_cap("5000") == 4320


def test_extract_quality_cap_from_format():
    assert extract_quality_cap_from_format(None) is None
    assert extract_quality_cap_from_format("") is None
    assert extract_quality_cap_from_format("bestvideo[height<=720]") == 720
    assert extract_quality_cap_from_format("bestvideo[height<=1080]") == 1080
    assert extract_quality_cap_from_format("bestvideo[height<=144]") == 144
    assert extract_quality_cap_from_format("best[height<=5000]") == 4320
    assert extract_quality_cap_from_format("bestvideo[height<=invalid]") is None
    assert extract_quality_cap_from_format("bestvideo[height=720]") is None


@patch.dict(os.environ, {"RELAYTV_DISPLAY_CAP_HEIGHT": "720"}, clear=True)
def test__display_cap_height():
    assert _display_cap_height({"display_cap_height": "1080"}) == 1080
    assert _display_cap_height({"display_cap_height": "invalid"}) == 720
    assert _display_cap_height({}) == 720
    assert _display_cap_height(None) == 720

    with patch.dict(os.environ, {}, clear=True):
        assert _display_cap_height({}) is None


@patch("relaytv_app.ytdlp_format_policy.runtime_config")
def test__user_cap(mock_runtime_config):
    mock_snapshot = MagicMock()
    mock_runtime_config.snapshot.return_value = mock_snapshot

    # Case 1: from settings
    assert _user_cap({"quality_cap": "720"}) == 720

    # Case 2: from runtime_config
    mock_snapshot.raw.return_value = "1080"
    assert _user_cap({"quality_cap": "invalid"}) == 1080
    assert _user_cap({}) == 1080
    assert _user_cap(None) == 1080

    # Case 3: from ytdlp_format setting
    mock_snapshot.raw.return_value = None
    assert _user_cap({"ytdlp_format": "bestvideo[height<=1440]"}) == 1440

    # Case 4: None
    assert _user_cap({}) is None


def test__target_cap():
    with patch("relaytv_app.ytdlp_format_policy._display_cap_height", return_value=720):
        with patch("relaytv_app.ytdlp_format_policy._user_cap", return_value=1080):
            assert _target_cap({}, {}, "auto_profile") == 720

    with patch("relaytv_app.ytdlp_format_policy._display_cap_height", return_value=None):
        with patch("relaytv_app.ytdlp_format_policy._user_cap", return_value=1080):
            assert _target_cap({}, {}, "auto_profile") == 1080

    with patch("relaytv_app.ytdlp_format_policy._display_cap_height", return_value=720):
        with patch("relaytv_app.ytdlp_format_policy._user_cap", return_value=None):
            assert _target_cap({}, {}, "auto_profile") == 720

    with patch("relaytv_app.ytdlp_format_policy._display_cap_height", return_value=None):
        with patch("relaytv_app.ytdlp_format_policy._user_cap", return_value=None):
            assert _target_cap({}, {}, "auto_profile") == 1080

    with patch("relaytv_app.ytdlp_format_policy._display_cap_height", return_value=720):
        with patch("relaytv_app.ytdlp_format_policy._user_cap", return_value=1080):
            assert _target_cap({}, {}, "manual") == 1080


@patch.dict(
    os.environ,
    {
        "YTDLP_FORMAT_YOUTUBE": "youtube_fmt",
        "YTDLP_FORMAT_TWITCH": "twitch_fmt",
    },
    clear=True,
)
def test__provider_specific_env():
    assert _provider_specific_env("youtube") == "youtube_fmt"
    assert _provider_specific_env("YOUTUBE") == "youtube_fmt"
    assert _provider_specific_env("twitch") == "twitch_fmt"
    assert _provider_specific_env("tiktok") == ""
    assert _provider_specific_env("unknown") == ""
    assert _provider_specific_env(None) == ""


def test__av1_allowed():
    assert _av1_allowed({"av1_allowed": True}) is True
    assert _av1_allowed({"av1_allowed": "yes"}) is True
    assert _av1_allowed({"av1_allowed": False}) is False
    assert _av1_allowed({"av1_allowed": ""}) is False
    assert _av1_allowed({}) is False
    assert _av1_allowed(None) is False


@patch.dict(os.environ, {"RELAYTV_ARM_DEFAULT_QUALITY": "720"}, clear=True)
def test__arm_default_quality_cap():
    assert _arm_default_quality_cap() == 720

    with patch.dict(os.environ, {}, clear=True):
        assert _arm_default_quality_cap() == 1080


def test__auto_provider_format():
    assert _auto_provider_format("rumble", 1080, av1_allowed=True) == "best*[height<=1080][fps<=60]/best*[height<=1080]/best[height<=1080][fps<=60]/best"
    assert _auto_provider_format("twitch", 720, av1_allowed=False) == "best[height<=720][fps<=60]/best"

    # AV1 allowed
    assert _auto_provider_format("youtube", 1080, av1_allowed=True) == "bestvideo[height<=1080][fps<=60]+bestaudio/best[height<=1080]/best"
    # AV1 not allowed
    assert _auto_provider_format("youtube", 1080, av1_allowed=False) == "bestvideo[vcodec!*=av01][height<=1080][fps<=60]+bestaudio/best[vcodec!*=av01][height<=1080]/best"


@patch("relaytv_app.ytdlp_format_policy.runtime_config")
def test_youtube_progressive_startup_format(mock_runtime_config):
    mock_snapshot = MagicMock()
    mock_snapshot.raw.return_value = "auto_profile"
    mock_runtime_config.snapshot.return_value = mock_snapshot

    with patch("relaytv_app.ytdlp_format_policy._target_cap", return_value=1080):
        with patch("platform.machine", return_value="x86_64"):
            assert youtube_progressive_startup_format({}, profile={}) == "best*[height<=1080][fps<=30][vcodec!=none][acodec!=none][vcodec^=avc1]/best*[height<=1080][fps<=30][vcodec!=none][acodec!=none]/best[height<=1080]/best"

        with patch("platform.machine", return_value="aarch64"):
            with patch("relaytv_app.ytdlp_format_policy._arm_default_quality_cap", return_value=720):
                assert youtube_progressive_startup_format({}, profile={}) == "best*[height<=720][fps<=30][vcodec!=none][acodec!=none][vcodec^=avc1]/best*[height<=720][fps<=30][vcodec!=none][acodec!=none]/best[height<=720]/best"

        with patch("platform.machine", return_value="x86_64"):
            with patch("relaytv_app.ytdlp_format_policy._arm_default_quality_cap", return_value=720):
                assert youtube_progressive_startup_format({}, profile={"decode_profile": "arm_safe"}) == "best*[height<=720][fps<=30][vcodec!=none][acodec!=none][vcodec^=avc1]/best*[height<=720][fps<=30][vcodec!=none][acodec!=none]/best[height<=720]/best"


@patch("relaytv_app.ytdlp_format_policy.runtime_config")
def test_youtube_progressive_startup_candidates(mock_runtime_config):
    mock_snapshot = MagicMock()
    mock_snapshot.raw.return_value = "auto_profile"
    mock_runtime_config.snapshot.return_value = mock_snapshot

    with patch("relaytv_app.ytdlp_format_policy._target_cap", return_value=1080):
        with patch("platform.machine", return_value="x86_64"):
            candidates = youtube_progressive_startup_candidates({}, profile={})
            assert len(candidates) == 3
            assert candidates[0] == "best*[height<=1080][fps<=30][vcodec!=none][acodec!=none][vcodec^=avc1]/best*[height<=1080][fps<=30][vcodec!=none][acodec!=none]/best[height<=1080]/best"
            assert candidates[1] == "best*[height<=1080][vcodec!=none][acodec!=none]/best[height<=1080]/best"
            assert candidates[2] == "best[height<=1080]/best"


@patch.dict(os.environ, {"RELAYTV_YOUTUBE_PROGRESSIVE_FIRST": "true"}, clear=True)
def test_youtube_progressive_startup_enabled():
    assert youtube_progressive_startup_enabled() is True

    with patch.dict(os.environ, {"RELAYTV_YOUTUBE_PROGRESSIVE_FIRST": "false"}, clear=True):
        assert youtube_progressive_startup_enabled() is False

    with patch.dict(os.environ, {"RELAYTV_YOUTUBE_PROGRESSIVE_FIRST": ""}, clear=True):
        assert youtube_progressive_startup_enabled() is False

    with patch.dict(os.environ, {}, clear=True):
        assert youtube_progressive_startup_enabled() is False


def test__arm_safe_if_needed():
    # Not ARM
    with patch("platform.machine", return_value="x86_64"):
        assert _arm_safe_if_needed("fmt", mode="auto_profile", cap=1080) == "fmt"

    # ARM, but not enforced
    with patch("platform.machine", return_value="aarch64"):
        with patch("relaytv_app.ytdlp_format_policy._env_bool", return_value=False):
            assert _arm_safe_if_needed("fmt", mode="auto_profile", cap=1080) == "fmt"

    # ARM and enforced
    with patch("platform.machine", return_value="aarch64"):
        with patch("relaytv_app.ytdlp_format_policy._env_bool", return_value=True):
            with patch("relaytv_app.ytdlp_format_policy._arm_default_quality_cap", return_value=720):
                expected_safe_fmt_720 = "best[height<=720][fps<=30][vcodec^=avc1]/best[height<=720][fps<=30]/best[height<=720]/best"

                # Auto profile -> safe fmt
                assert _arm_safe_if_needed("bestvideo+bestaudio", mode="auto_profile", cap=1080) == expected_safe_fmt_720

                # Heavy format -> safe fmt
                assert _arm_safe_if_needed("bestvideo+bestaudio", mode="manual", cap=1080) == expected_safe_fmt_720
                assert _arm_safe_if_needed("bv*+ba/best", mode="manual", cap=1080) == expected_safe_fmt_720

                # Non-heavy format -> original
                assert _arm_safe_if_needed("best", mode="manual", cap=1080) == "best"

            with patch("relaytv_app.ytdlp_format_policy._arm_default_quality_cap", return_value=1080):
                expected_safe_fmt_1080 = "best[height<=1080][fps<=30][vcodec^=avc1]/best[height<=1080][fps<=30]/best[height<=1080]/best"
                # cap > arm_cap
                assert _arm_safe_if_needed("bestvideo", mode="auto_profile", cap=1440) == expected_safe_fmt_1080


@patch("relaytv_app.ytdlp_format_policy.runtime_config")
def test_effective_ytdlp_format(mock_runtime_config):
    mock_snapshot = MagicMock()
    mock_snapshot.raw.side_effect = lambda key: {"RELAYTV_QUALITY_MODE": "auto_profile", "YTDLP_FORMAT": ""}.get(key)
    mock_runtime_config.snapshot.return_value = mock_snapshot

    with patch("relaytv_app.ytdlp_format_policy._target_cap", return_value=1080):
        # Provider specific env override
        with patch("relaytv_app.ytdlp_format_policy._provider_specific_env", return_value="provider_fmt"):
            with patch("relaytv_app.ytdlp_format_policy._arm_safe_if_needed", return_value="provider_fmt_safe"):
                assert effective_ytdlp_format({}) == "provider_fmt_safe"

        # Explicit manual setting
        mock_snapshot.raw.side_effect = lambda key: {"RELAYTV_QUALITY_MODE": "manual", "YTDLP_FORMAT": "manual_fmt"}.get(key)
        with patch("relaytv_app.ytdlp_format_policy._provider_specific_env", return_value=""):
            with patch("relaytv_app.ytdlp_format_policy._arm_safe_if_needed", return_value="manual_fmt_safe"):
                assert effective_ytdlp_format({"quality_mode": "manual", "ytdlp_format": "manual_fmt"}) == "manual_fmt_safe"

        # Auto provider fallback
        mock_snapshot.raw.side_effect = lambda key: {"RELAYTV_QUALITY_MODE": "auto_profile", "YTDLP_FORMAT": ""}.get(key)
        with patch("relaytv_app.ytdlp_format_policy._provider_specific_env", return_value=""):
            with patch("relaytv_app.ytdlp_format_policy._auto_provider_format", return_value="auto_fmt"):
                with patch("relaytv_app.ytdlp_format_policy._arm_safe_if_needed", return_value="auto_fmt_safe"):
                    assert effective_ytdlp_format({}) == "auto_fmt_safe"
