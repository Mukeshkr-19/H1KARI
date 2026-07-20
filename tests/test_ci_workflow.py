"""Deterministic workflow source contract test for .github/workflows/ci.yml."""

from __future__ import annotations

import pathlib
import pytest

try:
    import yaml
except ImportError:
    yaml = None


def test_ci_workflow_source_contracts():
    workflow_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "ci.yml"
    )
    assert workflow_path.exists(), "ci.yml must exist"
    content = workflow_path.read_text(encoding="utf-8")

    # If PyYAML is available, validate syntax
    if yaml is not None:
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)
        assert parsed.get("permissions") == {"contents": "read"}
        jobs = parsed.get("jobs", {})
        assert "python" in jobs
        assert "frontend" in jobs

    # Verify action revisions pinned by SHA
    assert "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10" in content
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1" in content
    assert "actions/setup-node@a0853c24544627f65ddf259abe73b1d18a591444" in content

    # Verify least-privilege permissions
    assert "permissions:\n  contents: read" in content or "contents: read" in content

    # Verify required deterministic gates
    assert "python -m pip check" in content
    assert "python hikari.py --doctor" in content
    assert "python -m compileall -q core hikari.py tests" in content
    assert "git diff --check" in content
    assert "git diff --exit-code" in content
    assert content.count('test -z "$(git status --porcelain)"') == 2

    # Verify existing gates are preserved
    assert "python scripts/frontend_third_party.py --check" in content
    assert "python -m pip install --requirement requirements-dev-macos-arm64-py312.lock" in content
    assert "python -m pytest tests -q" in content
    assert "python hikari.py --voice-status" in content
    assert "python tests/privacy_scan.py" in content
    assert "python scripts/public_artifact_scan.py" in content
    assert "python -m pytest tests/test_protocol_v1.py -q" in content
    assert "npm ci" in content
    assert "npm audit --audit-level=moderate" in content
    assert "npm run test:unit" in content
    assert "npm run lint" in content
    assert "npm run build" in content
