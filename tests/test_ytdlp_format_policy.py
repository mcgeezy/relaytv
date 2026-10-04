# SPDX-License-Identifier: GPL-3.0-only
import pytest
from unittest.mock import patch
from relaytv_app.ytdlp_format_policy import extract_quality_cap_from_format, _parse_cap

def test_extract_quality_cap_from_format_errors() -> None:
    """
    Test error paths in extract_quality_cap_from_format.
    Feeding strings with missing or invalid format limits returns None.
    """
    assert extract_quality_cap_from_format("height<=abc") is None
    assert extract_quality_cap_from_format("some_random_string") is None
    assert extract_quality_cap_from_format(None) is None
    assert extract_quality_cap_from_format("") is None

    # As re.search(r"height<=([0-9]{3,4})") restricts matching to strings of 3-4 digits,
    # the value passed to _parse_cap will be a valid int (or float), which won't naturally raise an exception
    # (unless something very low level like MemoryError occurs, which we cannot deterministically test natively).
    # Thus, we mock _parse_cap to test the exact `except Exception` branch.
    with patch("relaytv_app.ytdlp_format_policy._parse_cap", side_effect=Exception("Mocked parsing error")):
        assert extract_quality_cap_from_format("height<=1080") is None

def test_parse_cap_errors() -> None:
    """
    Test error paths in _parse_cap natively by feeding strings with
    mismatched or extremely large height limits which will exercise
    the exception path without complex setup.
    """
    assert _parse_cap("1e500") is None
    assert _parse_cap("not_a_number") is None
    assert _parse_cap(None) is None
    assert _parse_cap("") is None
    assert _parse_cap("auto") is None
