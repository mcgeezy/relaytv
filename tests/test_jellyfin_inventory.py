# SPDX-License-Identifier: GPL-3.0-only
"""Guardrail: the Jellyfin route-surface inventory is machine-checked.

The Phase 4 Jellyfin product-service work moves Jellyfin product logic out of
the routes package into ``integrations/jellyfin_service.py``. This test keeps
``docs/ARCHITECTURE_PHASE_4_JELLYFIN_INVENTORY.md`` in sync with the source
tree and pins the Jellyfin function definitions allowed per routes module so
each migration milestone tightens the contract explicitly.

Regenerate the doc listing after intentional changes with:

    PYTHONPATH=app python3 tests/test_jellyfin_inventory.py --write
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app" / "relaytv_app"
ROUTES_DIR = APP_DIR / "routes"
INVENTORY_DOC = REPO_ROOT / "docs" / "ARCHITECTURE_PHASE_4_JELLYFIN_INVENTORY.md"

LISTING_BEGIN = "<!-- BEGIN GENERATED JELLYFIN ROUTE LISTING (tests/test_jellyfin_inventory.py) -->"
LISTING_END = "<!-- END GENERATED JELLYFIN ROUTE LISTING -->"

# A "Jellyfin function" is any function defined in a routes module whose name
# mentions jellyfin. HTTP endpoint handlers stay in routes by design; product
# helpers migrate to integrations/jellyfin_service.py during Phase 4.
_DEF_RE = re.compile(r"^(?:async )?def (\w*[Jj]ellyfin\w*)\(", re.M)


def _module_name(path: Path) -> str:
    return str(path.relative_to(APP_DIR))


def scan_route_jellyfin_functions() -> dict[str, list[str]]:
    """Return {routes module: sorted jellyfin function names defined there}."""
    out: dict[str, list[str]] = {}
    for path in sorted(ROUTES_DIR.rglob("*.py")):
        names = sorted(_DEF_RE.findall(path.read_text(encoding="utf-8")))
        if names:
            out[_module_name(path)] = names
    return out


def render_listing() -> list[str]:
    lines: list[str] = []
    for module, names in sorted(scan_route_jellyfin_functions().items()):
        lines.append(f"### `{module}` ({len(names)})")
        lines.append("")
        lines.extend(f"- `{name}`" for name in names)
        lines.append("")
    if lines:
        lines.pop()
    return lines


def _doc_listing_lines() -> list[str]:
    text = INVENTORY_DOC.read_text(encoding="utf-8")
    begin = text.index(LISTING_BEGIN) + len(LISTING_BEGIN)
    end = text.index(LISTING_END)
    return [line for line in text[begin:end].splitlines() if line.strip()]


def test_jellyfin_inventory_doc_matches_source() -> None:
    assert INVENTORY_DOC.exists(), "jellyfin inventory doc is missing"
    expected = [line for line in render_listing() if line.strip()]
    actual = _doc_listing_lines()
    assert actual == expected, (
        "docs/ARCHITECTURE_PHASE_4_JELLYFIN_INVENTORY.md is stale; regenerate with "
        "`PYTHONPATH=app python3 tests/test_jellyfin_inventory.py --write`"
    )


# The containment contract: jellyfin functions allowed per routes module.
# This is the Phase 4 ratchet — milestones migrate product helpers into
# integrations/jellyfin_service.py and REMOVE names from these sets; nothing
# may be added without a deliberate roadmap decision. Endpoint handlers
# (jellyfin_* route functions) and HTTP-guard/UI-event glue are the intended
# end-state residents; every leading-underscore product helper listed here is
# migration inventory. Phase-start baseline: 117 definitions.
EXPECTED_JELLYFIN_ROUTE_FUNCTIONS: dict[str, set[str]] = {
    # M2 moved the 32 pure parse/normalize/URL helpers, M3 the stream
    # selection/transcode policy and playable-item resolution, M4 the track
    # preference/runtime track switching family, and M5 the progress/stopped
    # hint and snapshot family to integrations/jellyfin_service.py; routes
    # keeps assignment aliases. M6 moved the command ingress (dedupe state and
    # handle_command); _jellyfin_integration_command_impl remains here as the
    # thin adapter wrapper that owns route-side control dispatch and UI-event
    # seams.
    "routes/__init__.py": {
        "_jellyfin_integration_command_impl",
        "_ui_event_push_jellyfin",
    },
    # Static asset endpoints — permanent HTTP surface, not migration targets.
    "routes/assets.py": {"_jellyfin_svg", "pwa_jellyfin_svg"},
    # M7 pruned the dead delegation shims left behind by the migrations; the
    # remaining underscore names are live HTTP-side glue (guards, UI events,
    # request building) or shims still called by endpoint handlers.
    "routes/jellyfin.py": {
        "_extract_jellyfin_audio_stream_index_from_url",
        "_extract_jellyfin_item_id_from_url_raw",
        "_extract_jellyfin_subtitle_stream_index_from_url",
        "_jellyfin_command_req",
        "_jellyfin_integration_command",
        "_jellyfin_integration_command_impl",
        "_jellyfin_progress_snapshot",
        "_jellyfin_runtime_selected_audio_stream",
        "_jellyfin_runtime_selected_subtitle_stream",
        "_jellyfin_should_suppress_duplicate_ui_action",
        "_jellyfin_stopped_snapshot",
        "_require_jellyfin_catalog_ready",
        "_require_jellyfin_catalog_ready_for_playback",
        "_reset_jellyfin_command_state",
        "_ui_event_push_jellyfin",
        "jellyfin_audio_options",
        "jellyfin_audio_select",
        "jellyfin_catalog_cache_clear",
        "jellyfin_home",
        "jellyfin_integration_command",
        "jellyfin_integration_connect",
        "jellyfin_integration_disconnect",
        "jellyfin_integration_heartbeat",
        "jellyfin_integration_progress_snapshot",
        "jellyfin_integration_push",
        "jellyfin_integration_register",
        "jellyfin_integration_status",
        "jellyfin_integration_stopped",
        "jellyfin_integration_stopped_snapshot",
        "jellyfin_item_action",
        "jellyfin_item_adjacent",
        "jellyfin_item_detail",
        "jellyfin_movies",
        "jellyfin_search",
        "jellyfin_subtitle_options",
        "jellyfin_subtitle_select",
        "jellyfin_tv_series",
        "jellyfin_tv_series_episodes",
        "jellyfin_tv_series_play_all",
        "jellyfin_tv_series_seasons",
    },
    "routes/playback.py": {"_jellyfin_emit_stopped_hint"},
}


def test_jellyfin_route_functions_stay_within_pinned_sets() -> None:
    """Each routes module's jellyfin function set matches the pin exactly.

    A new name in a set means Jellyfin product logic landed in the routes
    package — put it in integrations/jellyfin_service.py instead. A missing
    name means a migration landed: tighten the pin here (and regenerate the
    doc) in the same commit.
    """
    actual = {mod: set(names) for mod, names in scan_route_jellyfin_functions().items()}
    assert actual == EXPECTED_JELLYFIN_ROUTE_FUNCTIONS


def _write_doc() -> None:
    text = INVENTORY_DOC.read_text(encoding="utf-8")
    begin = text.index(LISTING_BEGIN) + len(LISTING_BEGIN)
    end = text.index(LISTING_END)
    listing = "\n" + "\n".join(render_listing()) + "\n"
    INVENTORY_DOC.write_text(text[:begin] + listing + text[end:], encoding="utf-8")
    print(f"wrote {INVENTORY_DOC}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write_doc()
    else:
        print("\n".join(render_listing()))
