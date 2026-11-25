# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

from relaytv_app.routes import ui


STATIC_UI_DIR = Path(__file__).resolve().parents[1] / "app" / "relaytv_app" / "static" / "ui"


def test_ui_html_includes_android_header_bridge_markup_hidden_by_default():
    html = ui().body.decode("utf-8")

    # Header brand switcher wrapper and chevron icon (hidden in CSS unless html.android-app)
    assert '<div id="hdrBrand" class="hdrBrand">' in html
    assert '<div class="hdrBrandTitleRow">' in html
    assert '<h1 id="appBrandName">RelayTV</h1>' in html
    assert 'class="hdrBrandChevron"' in html
    # Normal browsers must not get android-app class or interactive brand role in initial HTML
    assert "android-app" not in html
    assert 'id="hdrBrand" class="hdrBrand" role="button"' not in html

    # Android menu section inside #hdrMenuPanel, hidden by default, placed before .hdrMenuFoot
    section_idx = html.index('id="hdrMenuAndroidSection"')
    panel_idx = html.index('id="hdrMenuPanel"')
    foot_idx = html.index('class="hdrMenuFoot"')
    assert panel_idx < section_idx < foot_idx
    assert 'id="hdrMenuAndroidSection" class="hdrMenuAndroid hidden"' in html

    for btn_id, label in (
        ("androidServersBtn", "Switch server"),
        ("androidSettingsBtn", "App settings"),
        ("androidReloadBtn", "Reload"),
        ("androidPrivacyBtn", "Privacy policy"),
    ):
        assert f'id="{btn_id}"' in html
        assert label in html


def test_app_js_wires_android_bridge_contract():
    app_js = (STATIC_UI_DIR / "app.js").read_text(encoding="utf-8")

    for token in (
        "window.RelayTVAndroid",
        "onBridgeReady",
        "openServers",
        "openSettings",
        "reload",
        "openPrivacy",
        "onThemeChanged",
        "bindAndroidBridge()",
    ):
        assert token in app_js
