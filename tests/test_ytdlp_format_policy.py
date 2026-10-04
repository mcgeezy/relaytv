# SPDX-License-Identifier: GPL-3.0-only

from relaytv_app.ytdlp_format_policy import _parse_cap


def test_parse_cap_none():
    assert _parse_cap(None) is None


def test_parse_cap_empty_or_special_strings():
    assert _parse_cap("") is None
    assert _parse_cap("   ") is None
    assert _parse_cap("auto") is None
    assert _parse_cap("worst") is None
    assert _parse_cap("AUTO") is None
    assert _parse_cap("WORST") is None


def test_parse_cap_valid_integers():
    assert _parse_cap(1080) == 1080
    assert _parse_cap("1080") == 1080
    assert _parse_cap(" 720 ") == 720


def test_parse_cap_valid_floats():
    assert _parse_cap(1080.5) == 1080
    assert _parse_cap("1080.5") == 1080
    assert _parse_cap("720.99") == 720


def test_parse_cap_zero_and_negative():
    assert _parse_cap(0) is None
    assert _parse_cap("0") is None
    assert _parse_cap(-100) is None
    assert _parse_cap("-100") is None


def test_parse_cap_invalid_strings():
    assert _parse_cap("abc") is None
    assert _parse_cap("1000px") is None
    assert _parse_cap("NaN") is None


def test_parse_cap_clamps_minimum():
    assert _parse_cap(144) == 144
    assert _parse_cap(100) == 144
    assert _parse_cap("100") == 144


def test_parse_cap_clamps_maximum():
    assert _parse_cap(4320) == 4320
    assert _parse_cap(5000) == 4320
    assert _parse_cap("5000") == 4320
