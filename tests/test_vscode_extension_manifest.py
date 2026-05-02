from __future__ import annotations

import json
from pathlib import Path

EXTENSION_DIR = Path(__file__).resolve().parents[1] / "vscode-extension"


def _package() -> dict:
    return json.loads((EXTENSION_DIR / "package.json").read_text(encoding="utf-8"))


def test_vscode_extension_activates_and_autostarts():
    pkg = _package()
    assert pkg["version"] != "0.1.0"
    activation_events = set(pkg["activationEvents"])
    assert "onStartupFinished" in activation_events
    assert "onCommand:codegraph.indexWorkspace" in activation_events
    assert "onCommand:codegraph.refresh" in activation_events

    props = pkg["contributes"]["configuration"]["properties"]
    assert pkg["version"] != "0.1.1"
    assert props["codegraph.endpoint"]["default"] == "http://127.0.0.1:8811"
    assert props["codegraph.autoStartServer"]["default"] is True
    assert props["codegraph.autoIndexOnStart"]["default"] is True
    assert props["codegraph.hover.enabled"]["default"] is True


def test_vscode_extension_manifest_commands_match_implementation():
    pkg = _package()
    command_ids = {command["command"] for command in pkg["contributes"]["commands"]}
    assert {
        "codegraph.indexWorkspace",
        "codegraph.refresh",
        "codegraph.showImpactForCurrent",
        "codegraph.openFunction",
    }.issubset(command_ids)

    icon = pkg.get("icon")
    assert icon is None or (EXTENSION_DIR / icon).is_file()


def test_vscode_extension_uses_portable_python_startup():
    source = (EXTENSION_DIR / "src" / "extension.ts").read_text(encoding="utf-8")

    assert ".venv" in source
    assert "Scripts" in source
    assert "bin" in source
    assert "PYTHONUTF8" in source
    assert "PYTHONIOENCODING" in source
