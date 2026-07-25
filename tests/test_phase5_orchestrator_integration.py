"""Phase 5 must not create a parallel orchestrator runtime."""

from __future__ import annotations

import ast
from pathlib import Path


def test_orchestrator_has_no_phase5_execution_path():
    source = Path("core/orchestrator.py").read_text(encoding="utf-8")
    assert "Phase5CapabilityService" not in source
    assert "phase5_execute" not in source
    assert "Phase5RuntimeService boundary" in source


def test_orchestrator_ast_has_no_phase5_imports():
    tree = ast.parse(Path("core/orchestrator.py").read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not any(name.startswith("core.phase5") for name in imported)


def test_server_owns_phase5_boundary():
    source = Path("core/server.py").read_text(encoding="utf-8")
    assert "_handle_phase5_control" in source
    assert "create_phase5_subsystem" in source
