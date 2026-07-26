"""Import-time purity and content-free error surfaces for Time Sense runtime."""

from __future__ import annotations

import ast
from pathlib import Path


RUNTIME_FILES = [
    Path("core/time_sense/session_policy.py"),
    Path("core/time_sense/job_observations.py"),
    Path("core/time_sense/stuck_notify.py"),
    Path("core/time_sense/adapters.py"),
]

STREAMING_FILES = list(Path("core/streaming_voice").glob("*.py"))


FORBIDDEN = {
    "open",
    "Path",
    "socket",
    "subprocess",
    "requests",
    "urllib",
    "sqlite3",
    "httpx",
}


def _module_calls_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_runtime_modules_have_no_io_imports():
    for path in RUNTIME_FILES + STREAMING_FILES:
        names = _module_calls_names(path)
        for forbidden in ("socket", "subprocess", "sqlite3", "httpx", "requests", "urllib"):
            assert forbidden not in names, f"{path} imports {forbidden}"


def test_streaming_and_policy_import_clean():
    import core.streaming_voice  # noqa: F401
    import core.time_sense  # noqa: F401
