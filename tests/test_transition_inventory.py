# SPDX-License-Identifier: GPL-3.0-only
"""Guardrail: the playback transition writer inventory is machine-checked.

The Phase 3 playback-service work centralizes every write to the playback
transition globals behind explicit service commands. This test keeps
``docs/ARCHITECTURE_PHASE_3_TRANSITION_INVENTORY.md`` in sync with the source
tree and pins the allowed writer modules per global so each migration
milestone tightens the contract explicitly.

Regenerate the doc table after intentional changes with:

    PYTHONPATH=app python3 tests/test_transition_inventory.py --write
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app" / "relaytv_app"
INVENTORY_DOC = REPO_ROOT / "docs" / "ARCHITECTURE_PHASE_3_TRANSITION_INVENTORY.md"

TABLE_BEGIN = "<!-- BEGIN GENERATED TRANSITION TABLE (tests/test_transition_inventory.py) -->"
TABLE_END = "<!-- END GENERATED TRANSITION TABLE -->"

# ``state.py`` owns the globals and their persistence and is excluded from the
# scan by definition; the inventory tracks every OTHER module that writes them.
# The prefix tolerates the aliased import styles used in the tree
# (``state.``, ``_state.``, ``app_state.``).
_STATE = r"\b(?:app_)?_?state\."

# Writer patterns per transition global. Direct assignment (not ==) and the
# state.py setter calls both count as writes: the Phase 3 end state is that
# only the playback service issues either form outside state.py.
_WRITE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "NOW_PLAYING": (
        re.compile(_STATE + r"NOW_PLAYING\s*=(?!=)"),
        re.compile(_STATE + r"set_now_playing\("),
    ),
    "SESSION_STATE": (
        re.compile(_STATE + r"SESSION_STATE\s*=(?!=)"),
        re.compile(_STATE + r"set_session_state\("),
    ),
    "SESSION_POSITION": (
        re.compile(_STATE + r"SESSION_POSITION\s*=(?!=)"),
        re.compile(_STATE + r"set_session_position\("),
    ),
    "AUTO_NEXT_SUPPRESS_UNTIL": (
        re.compile(_STATE + r"AUTO_NEXT_SUPPRESS_UNTIL\s*=(?!=)"),
    ),
    "QUEUE": (
        re.compile(_STATE + r"QUEUE\.(?:clear|append|insert|pop|remove|extend|sort|reverse)\("),
        re.compile(_STATE + r"QUEUE\s*\[[^\]]*\]\s*=(?!=)"),
        re.compile(_STATE + r"QUEUE\s*=(?!=)"),
    ),
    # Any reference counts: the stack is private transition state, and the
    # Phase 3 end state is that no routes module touches it at all.
    "_TEMP_PLAYBACK_STACK": (re.compile(r"_TEMP_PLAYBACK_STACK"),),
}

_OWNER_MODULE = "state.py"


def _module_name(path: Path) -> str:
    return str(path.relative_to(APP_DIR))


def scan_writers() -> dict[str, dict[str, int]]:
    """Return {global: {module: write_site_count}} excluding state.py."""
    out: dict[str, dict[str, int]] = {name: {} for name in _WRITE_PATTERNS}
    for path in sorted(APP_DIR.rglob("*.py")):
        module = _module_name(path)
        if module == _OWNER_MODULE:
            continue
        text = path.read_text(encoding="utf-8")
        for name, patterns in _WRITE_PATTERNS.items():
            count = sum(len(pattern.findall(text)) for pattern in patterns)
            if count:
                out[name][module] = count
    return out


def render_table() -> list[str]:
    writers = scan_writers()
    lines = [
        "| Transition global | Writers outside `state.py` (write sites) |",
        "| --- | --- |",
    ]
    for name in sorted(writers):
        modules = writers[name]
        cell = "<br>".join(f"`{m}` ({modules[m]})" for m in sorted(modules)) or "-"
        lines.append(f"| `{name}` | {cell} |")
    return lines


def _doc_table_lines() -> list[str]:
    text = INVENTORY_DOC.read_text(encoding="utf-8")
    begin = text.index(TABLE_BEGIN) + len(TABLE_BEGIN)
    end = text.index(TABLE_END)
    return [line for line in text[begin:end].splitlines() if line.strip()]


def test_transition_inventory_doc_matches_source() -> None:
    assert INVENTORY_DOC.exists(), "transition inventory doc is missing"
    expected = render_table()
    actual = _doc_table_lines()
    assert actual == expected, (
        "docs/ARCHITECTURE_PHASE_3_TRANSITION_INVENTORY.md is stale; regenerate with "
        "`PYTHONPATH=app python3 tests/test_transition_inventory.py --write`"
    )


# The containment contract: modules allowed to write each transition global.
# This is the Phase 3 ratchet — milestones migrate writers into the playback
# service and REMOVE modules from these sets; nothing may be added without a
# deliberate roadmap decision. Counts are tracked in the generated doc table;
# this pin tracks the module sets, which is what the migration changes.
EXPECTED_TRANSITION_WRITERS: dict[str, set[str]] = {
    "NOW_PLAYING": {
        "player.py",
        "routes/__init__.py",
        "routes/jellyfin.py",
        "routes/playback.py",
    },
    "SESSION_STATE": {
        "player.py",
        "routes/__init__.py",
        "routes/jellyfin.py",
        "routes/playback.py",
    },
    "SESSION_POSITION": {"player.py", "routes/playback.py"},
    # Tightened in M3: all suppression writes go through
    # playback_service.suppress_auto_next / clear_auto_next_suppression.
    "AUTO_NEXT_SUPPRESS_UNTIL": {"playback_service.py"},
    "QUEUE": {
        "playback_service.py",
        "player.py",
        "routes/__init__.py",
        "routes/playback.py",
        "routes/queue.py",
        "routes/uploads.py",
        "upload_store.py",
    },
    "_TEMP_PLAYBACK_STACK": {"routes/__init__.py", "routes/playback.py"},
}


def test_transition_writers_stay_within_pinned_modules() -> None:
    """Each transition global's writer set matches the pinned contract exactly.

    A new module in a set means a transition write bypassed the playback
    service — route it through the service instead. A missing module means a
    migration landed: tighten the pin here (and the doc) in the same commit.
    """
    actual = {name: set(modules) for name, modules in scan_writers().items()}
    assert actual == EXPECTED_TRANSITION_WRITERS


def _write_doc() -> None:
    text = INVENTORY_DOC.read_text(encoding="utf-8")
    begin = text.index(TABLE_BEGIN) + len(TABLE_BEGIN)
    end = text.index(TABLE_END)
    table = "\n" + "\n".join(render_table()) + "\n"
    INVENTORY_DOC.write_text(text[:begin] + table + text[end:], encoding="utf-8")
    print(f"wrote {INVENTORY_DOC}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write_doc()
    else:
        print("\n".join(render_table()))
