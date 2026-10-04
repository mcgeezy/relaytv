# SPDX-License-Identifier: GPL-3.0-only

import os
from unittest import mock

import pytest

from relaytv_app import ytdlp_update
from relaytv_app.config import runtime_config


@pytest.fixture(autouse=True)
def _reset_runtime_config():
    # Make sure we start with a clean state
    with mock.patch.dict(os.environ, {}, clear=True):
        runtime_config.refresh_from_env()
    yield
    # Clean up after test
    with mock.patch.dict(os.environ, {}, clear=True):
        runtime_config.refresh_from_env()


def test_enabled_default():
    assert ytdlp_update.enabled() is False


def test_enabled_true():
    runtime_config.set_value("RELAYTV_YTDLP_AUTO_UPDATE", "1")
    assert ytdlp_update.enabled() is True

    runtime_config.set_value("RELAYTV_YTDLP_AUTO_UPDATE", "true")
    assert ytdlp_update.enabled() is True


def test_enabled_false():
    runtime_config.set_value("RELAYTV_YTDLP_AUTO_UPDATE", "0")
    assert ytdlp_update.enabled() is False

    runtime_config.set_value("RELAYTV_YTDLP_AUTO_UPDATE", "false")
    assert ytdlp_update.enabled() is False


def test_poll_interval_sec_default():
    with mock.patch.dict(os.environ, {}, clear=True):
        assert ytdlp_update._poll_interval_sec() == 3600.0


def test_poll_interval_sec_custom():
    with mock.patch.dict(os.environ, {"RELAYTV_YTDLP_AUTO_UPDATE_POLL_SEC": "7200"}, clear=True):
        assert ytdlp_update._poll_interval_sec() == 7200.0


def test_poll_interval_sec_minimum():
    with mock.patch.dict(os.environ, {"RELAYTV_YTDLP_AUTO_UPDATE_POLL_SEC": "30"}, clear=True):
        assert ytdlp_update._poll_interval_sec() == 60.0


def test_poll_interval_sec_invalid():
    with mock.patch.dict(os.environ, {"RELAYTV_YTDLP_AUTO_UPDATE_POLL_SEC": "invalid"}, clear=True):
        assert ytdlp_update._poll_interval_sec() == 3600.0
