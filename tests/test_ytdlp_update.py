# SPDX-License-Identifier: GPL-3.0-only
import os
from unittest import mock

import pytest

from relaytv_app import ytdlp_update
from relaytv_app.config import runtime_config


@pytest.fixture(autouse=True)
def _reset_runtime_config():
    # Store old values
    old_values = dict(runtime_config._values)
    # Clear the config for the test
    runtime_config._values.clear()
    # Re-create the snapshot
    runtime_config.set_value("dummy", "dummy")
    del runtime_config._values["dummy"]
    runtime_config._snapshot = type(runtime_config.snapshot())(runtime_config._values)

    yield

    # Restore old values
    runtime_config._values.clear()
    runtime_config._values.update(old_values)
    runtime_config._snapshot = type(runtime_config.snapshot())(runtime_config._values)


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
    with mock.patch.dict(os.environ, {}, clear=False):
        if "RELAYTV_YTDLP_AUTO_UPDATE_POLL_SEC" in os.environ:
            del os.environ["RELAYTV_YTDLP_AUTO_UPDATE_POLL_SEC"]
        assert ytdlp_update._poll_interval_sec() == 3600.0


def test_poll_interval_sec_custom():
    with mock.patch.dict(os.environ, {"RELAYTV_YTDLP_AUTO_UPDATE_POLL_SEC": "7200"}):
        assert ytdlp_update._poll_interval_sec() == 7200.0


def test_poll_interval_sec_minimum():
    with mock.patch.dict(os.environ, {"RELAYTV_YTDLP_AUTO_UPDATE_POLL_SEC": "30"}):
        assert ytdlp_update._poll_interval_sec() == 60.0


def test_poll_interval_sec_invalid():
    with mock.patch.dict(os.environ, {"RELAYTV_YTDLP_AUTO_UPDATE_POLL_SEC": "invalid"}):
        assert ytdlp_update._poll_interval_sec() == 3600.0
