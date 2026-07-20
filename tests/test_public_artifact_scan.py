"""Deterministic checks for public conversation and patch artifact scanning."""

from __future__ import annotations

from pathlib import Path

from scripts.public_artifact_scan import artifact_rules, find_artifact_violations
from tests.privacy_scan import REPO_ROOT, collect_public_source_files


def test_public_source_has_no_conversation_or_patch_artifacts() -> None:
    assert find_artifact_violations(collect_public_source_files()) == []


def test_rules_detect_bounded_artifact_categories(tmp_path: Path) -> None:
    samples = (
        "Generated" + "-by external system",
        "copied" + " prompt follows",
        "cache/agent" + "-tools/result.txt",
        "*** Begin" + " Patch",
        "tool_call" + "_output",
        "assistant" + " to=terminal",
        "orchestration" + " details",
    )
    for index, sample in enumerate(samples):
        path = tmp_path / f"sample-{index}.txt"
        path.write_text(sample, encoding="utf-8")
        assert find_artifact_violations([path], artifact_rules())


def test_scan_includes_workflow_and_new_phase_files() -> None:
    rels = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in collect_public_source_files()
    }
    assert ".github/workflows/ci.yml" in rels
    assert any(rel.startswith("core/jobs/") for rel in rels)
    assert any(rel.startswith("core/productivity/") for rel in rels)
