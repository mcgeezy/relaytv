# SPDX-License-Identifier: GPL-3.0-only
from relaytv_app import ytdlp_format_policy

def test_effective_ytdlp_format_edge_cases() -> None:
    fmt1 = ytdlp_format_policy.effective_ytdlp_format(None, provider="unknown_service")
    fmt2 = ytdlp_format_policy.effective_ytdlp_format(None, provider=None)  # type: ignore[arg-type]

    expected = 'bestvideo[vcodec!*=av01][height<=1080][fps<=60]+bestaudio/best[vcodec!*=av01][height<=1080]/best'
    assert fmt1 == expected
    assert fmt2 == expected
