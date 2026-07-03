# SPDX-License-Identifier: GPL-3.0-only
import pytest

from relaytv_app.config import runtime_config


@pytest.fixture(autouse=True)
def _runtime_config_env_lockstep():
    """Keep the global RuntimeConfig snapshot in lockstep with the test env.

    Production guarantees env/snapshot lockstep because every settings-bus
    write goes through RuntimeConfig.set_value. Tests mutate os.environ directly
    (monkeypatch), so re-sync the snapshot from env at test start (discarding
    leftovers from earlier tests) and after teardown (once monkeypatch has
    restored the environment). Tests that change a settings-bus variable
    mid-test and exercise a snapshot-reading consumer must call
    runtime_config.refresh_from_env() themselves after the change.
    """
    runtime_config.refresh_from_env()
    yield
    runtime_config.refresh_from_env()


_YTDLP_PROVIDER_KEYS = (
    "YTDLP_FORMAT_YOUTUBE",
    "YTDLP_FORMAT_TWITCH",
    "YTDLP_FORMAT_TIKTOK",
    "YTDLP_FORMAT_RUMBLE",
    "YTDLP_FORMAT_BITCHUTE",
)


@pytest.fixture
def disable_arm_safe_ytdl(monkeypatch):
    monkeypatch.setenv("RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT", "0")


@pytest.fixture
def ytdlp_format_best(monkeypatch):
    monkeypatch.setenv("YTDLP_FORMAT", "best")
    for key in _YTDLP_PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def ytdlp_format_unset(monkeypatch):
    monkeypatch.delenv("YTDLP_FORMAT", raising=False)
    for key in _YTDLP_PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
