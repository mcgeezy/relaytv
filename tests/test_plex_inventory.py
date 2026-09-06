# SPDX-License-Identifier: GPL-3.0-only
"""Machine-checked Plex route containment and module inventory."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app" / "relaytv_app"
ROUTES_DIR = APP_DIR / "routes"
INTEGRATIONS_DIR = APP_DIR / "integrations"
INVENTORY_DOC = REPO_ROOT / "docs" / "PLEX_INVENTORY.md"
LISTING_BEGIN = "<!-- BEGIN GENERATED PLEX ROUTE LISTING (tests/test_plex_inventory.py) -->"
LISTING_END = "<!-- END GENERATED PLEX ROUTE LISTING -->"
_DEF_RE = re.compile(r"^(?:async )?def (\w*[Pp]lex\w*)\(", re.M)


def _module_name(path: Path) -> str:
    return str(path.relative_to(APP_DIR))


def scan_route_plex_functions() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted(ROUTES_DIR.rglob("*.py")):
        names = sorted(_DEF_RE.findall(path.read_text(encoding="utf-8")))
        if names:
            out[_module_name(path)] = names
    return out


def render_listing() -> list[str]:
    lines: list[str] = []
    for module, names in sorted(scan_route_plex_functions().items()):
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


def test_plex_inventory_doc_matches_source() -> None:
    expected = [line for line in render_listing() if line.strip()]
    assert _doc_listing_lines() == expected, (
        "docs/PLEX_INVENTORY.md is stale; regenerate with "
        "`PYTHONPATH=app python3 tests/test_plex_inventory.py --write`"
    )


EXPECTED_PLEX_ROUTE_FUNCTIONS = {
    "routes/plex.py": {
        "plex_artwork",
        "plex_auth_cancel",
        "plex_auth_poll",
        "plex_auth_start",
        "plex_disconnect",
        "plex_home",
        "plex_integration_status",
        "plex_integration_test",
        "plex_item_children",
        "plex_item_detail",
        "plex_libraries",
        "plex_library_items",
        "plex_search",
        "plex_server_select",
        "plex_servers",
    }
}


def test_plex_route_functions_stay_within_pinned_sets() -> None:
    actual = {module: set(names) for module, names in scan_route_plex_functions().items()}
    assert actual == EXPECTED_PLEX_ROUTE_FUNCTIONS


def test_plex_integrations_do_not_import_routes() -> None:
    for path in sorted(INTEGRATIONS_DIR.glob("plex_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "routes" not in str(node.module or "").split(".")
            elif isinstance(node, ast.Import):
                assert all("routes" not in alias.name.split(".") for alias in node.names)


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
