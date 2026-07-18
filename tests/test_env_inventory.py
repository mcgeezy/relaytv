# SPDX-License-Identifier: GPL-3.0-only
"""Guardrail: the app-config environment variable inventory is machine-checked.

The Phase 2 runtime-config work migrates in-process env reads behind a typed
config service. This test keeps ``docs/ENV_INVENTORY.md``
in sync with the source tree so new variables, new runtime writers, or moved
readers are caught in review.

Regenerate the doc table after intentional changes with:

    PYTHONPATH=app python3 tests/test_env_inventory.py --write
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app" / "relaytv_app"
INVENTORY_DOC = REPO_ROOT / "docs" / "ENV_INVENTORY.md"

TABLE_BEGIN = "<!-- BEGIN GENERATED ENV TABLE (tests/test_env_inventory.py) -->"
TABLE_END = "<!-- END GENERATED ENV TABLE -->"

# App-config variables: the settings/env bus Phase 2 is migrating. Platform and
# toolkit variables (DISPLAY, QT_*, XDG_*, ...) are intentionally out of scope.
_CONFIG_VAR = re.compile(
    r"""["'](RELAYTV_[A-Z0-9_]+|YTDLP_[A-Z0-9_]+|USE_INVIDIOUS|INVIDIOUS_BASE|MPV_AUDIO_DEVICE|MPV_EXTRA_ARGS)["']"""
)

# Runtime writes to the process environment (the in-process config bus).
# RuntimeConfig.set_value writes the settings snapshot (mirroring only the
# pinned subprocess contract to os.environ).
_WRITE_PATTERNS = (
    re.compile(r"""os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]\s*=(?!=)"""),
    re.compile(r"""os\.environ\.setdefault\(\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""os\.environ\.pop\(\s*["']([A-Z][A-Z0-9_]*)["']"""),
    re.compile(r"""runtime_config\.set_value\(\s*["']([A-Z][A-Z0-9_]*)["']"""),
)

# Pre-app launcher: mutates its own env dict/os.environ before exec'ing the
# server, so its writes are parent-process input, not runtime bus writes.
_ENTRYPOINT_MODULE = "container_entrypoint.py"
_ENTRYPOINT_WRITE = re.compile(r"""\benv\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]\s*=(?!=)""")

# Modules launched as child processes at runtime; they inherit the server's
# environment at spawn time and define the subprocess mirroring contract.
_CHILD_PROCESS_MODULES = ("qt_shell_app.py", "overlay_app.py")


def _module_name(path: Path) -> str:
    return str(path.relative_to(APP_DIR))


def scan_inventory() -> dict[str, dict[str, list[str]]]:
    """Return {var: {"referenced": [modules], "runtime_writers": [modules]}}."""
    referenced: dict[str, set[str]] = {}
    runtime_writers: dict[str, set[str]] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        module = _module_name(path)
        text = path.read_text(encoding="utf-8")
        for match in _CONFIG_VAR.finditer(text):
            referenced.setdefault(match.group(1), set()).add(module)
        write_patterns = _WRITE_PATTERNS
        if path.name == _ENTRYPOINT_MODULE:
            write_patterns = (*_WRITE_PATTERNS, _ENTRYPOINT_WRITE)
        for pattern in write_patterns:
            for match in pattern.finditer(text):
                name = match.group(1)
                if _CONFIG_VAR.match(f'"{name}"'):
                    runtime_writers.setdefault(name, set()).add(module)
    return {
        name: {
            "referenced": sorted(referenced.get(name, set())),
            "runtime_writers": sorted(runtime_writers.get(name, set())),
        }
        for name in sorted(set(referenced) | set(runtime_writers))
    }


def classify(entry: dict[str, list[str]]) -> str:
    labels: list[str] = []
    writers = [w for w in entry["runtime_writers"] if w != _ENTRYPOINT_MODULE]
    referenced = entry["referenced"]
    if writers:
        labels.append("settings bus")
    if any(m.endswith(_CHILD_PROCESS_MODULES) for m in referenced):
        labels.append("child process input")
    if referenced == [_ENTRYPOINT_MODULE] or (
        entry["runtime_writers"] == [_ENTRYPOINT_MODULE] and set(referenced) <= {_ENTRYPOINT_MODULE}
    ):
        labels.append("pre-app entrypoint")
    elif _ENTRYPOINT_MODULE in referenced:
        labels.append("entrypoint input")
    if not labels:
        labels.append("static env")
    return ", ".join(labels)


def render_table() -> list[str]:
    inventory = scan_inventory()
    lines = [
        "| Variable | Referenced in | Runtime writers | Classification |",
        "| --- | --- | --- | --- |",
    ]
    for name, entry in inventory.items():
        referenced = "<br>".join(f"`{m}`" for m in entry["referenced"]) or "-"
        writers = "<br>".join(f"`{m}`" for m in entry["runtime_writers"]) or "-"
        lines.append(f"| `{name}` | {referenced} | {writers} | {classify(entry)} |")
    return lines


def _doc_table_lines() -> list[str]:
    text = INVENTORY_DOC.read_text(encoding="utf-8")
    begin = text.index(TABLE_BEGIN) + len(TABLE_BEGIN)
    end = text.index(TABLE_END)
    return [line for line in text[begin:end].splitlines() if line.strip()]


def test_env_inventory_doc_matches_source() -> None:
    assert INVENTORY_DOC.exists(), "env inventory doc is missing"
    expected = render_table()
    actual = _doc_table_lines()
    assert actual == expected, (
        "docs/ENV_INVENTORY.md is stale; regenerate with "
        "`PYTHONPATH=app python3 tests/test_env_inventory.py --write`"
    )


# The Phase 2 subprocess mirroring contract: settings-bus variables that are
# also referenced by a runtime child process module. RELAYTV_DEVICE_NAME is a
# legacy fallback only -- qt_shell_app prefers persisted settings and reads the
# variable just when settings are unavailable.
EXPECTED_SETTINGS_BUS_CHILD_READS = {"RELAYTV_DEVICE_NAME"}


def test_runtime_env_bus_child_process_contract_is_pinned() -> None:
    """The settings-bus/child-process overlap is exactly the known contract.

    This is the Phase 2 M5 containment precondition: apart from the pinned
    fallback reads, the settings-to-env sync is consumed entirely in-process,
    so env mirroring for children only needs to cover operator-provided
    variables. If this fails, a settings-bus variable gained or lost a child
    process reader; update the mirroring contract in
    docs/ENV_INVENTORY.md before changing env writes.
    """
    actual = set()
    for name, entry in scan_inventory().items():
        writers = [w for w in entry["runtime_writers"] if w != _ENTRYPOINT_MODULE]
        if not writers:
            continue
        if any(m.endswith(_CHILD_PROCESS_MODULES) for m in entry["referenced"]):
            actual.add(name)
    assert actual == EXPECTED_SETTINGS_BUS_CHILD_READS


# M4 completion guardrail: modules allowed to read settings-bus variables
# straight from the environment.
# - state.py: the settings-defaults path; operator env is the intended default
#   source for keys missing from the persisted settings file, and applied
#   settings always exist in the file, so these reads survive M5 unchanged.
# - qt_shell_app.py / overlay_app.py: runtime child processes reading their
#   own inherited environment.
# - container_entrypoint.py: pre-app launcher.
# - config.py: owns the bus.
_SETTINGS_BUS_ENV_READERS_ALLOWED = {
    "state.py",
    "qt_shell_app.py",
    "overlay_app.py",
    "container_entrypoint.py",
    "config.py",
}

_ENV_READ = re.compile(
    r"""(?:os\.getenv|os\.environ\.get|_env_bool|_env_choice)\(\s*["']([A-Z][A-Z0-9_]*)["']"""
)


def test_settings_bus_env_reads_stay_in_allowed_modules() -> None:
    """In-process consumers read settings-bus config via RuntimeConfig snapshots.

    Direct env reads of settings-bus variables are only allowed in the
    documented modules above. A failure means a new consumer bypassed the
    runtime config service; migrate it to ``runtime_config.snapshot()`` reads.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "app"))
    from relaytv_app.config import SETTINGS_BUS_VARS

    violations: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        module = _module_name(path)
        if module in _SETTINGS_BUS_ENV_READERS_ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _ENV_READ.finditer(line):
                if match.group(1) in SETTINGS_BUS_VARS:
                    violations.append(f"{module}:{lineno}: {match.group(1)}")
    assert violations == []


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
