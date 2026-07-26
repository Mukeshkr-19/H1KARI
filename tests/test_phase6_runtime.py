"""Integration tests for the fail-closed Phase 6 runtime facade."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.phase6_developer import (
    GitOperationRequest,
    GitPolicyOutcome,
    GitStateSnapshot,
    RepositoryQuery,
    SandboxCommandRequest,
    SandboxOutcome,
)
from core.phase6_runtime import (
    Phase6FeatureFlags,
    Phase6UnavailableError,
    create_phase6_runtime,
)


def test_runtime_defaults_every_acting_surface_off() -> None:
    runtime = create_phase6_runtime()

    assert runtime.flags == Phase6FeatureFlags()
    for feature in Phase6FeatureFlags.__dataclass_fields__:
        with pytest.raises(Phase6UnavailableError, match="capability unavailable"):
            runtime.require_enabled(feature)


def test_runtime_scans_and_queries_explicit_root(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def stable_symbol():\n    return 1\n", encoding="utf-8")
    runtime = create_phase6_runtime()

    index = runtime.scan_repository(tmp_path)
    result = runtime.query_repository(
        index,
        RepositoryQuery(query_text="stable_symbol", kind="symbol", max_hits=5),
    )

    assert any(hit.symbol == "stable_symbol" for hit in result.hits)


def test_runtime_git_and_sandbox_are_advisory_only(tmp_path: Path) -> None:
    runtime = create_phase6_runtime()
    state = GitStateSnapshot(
        branch="main",
        head="a" * 40,
        has_untracked=False,
        has_unstaged=False,
        has_staged=False,
        has_unmerged=False,
    )

    git_decision = runtime.evaluate_git(GitOperationRequest(("git", "status")), state)
    sandbox_decision = runtime.evaluate_sandbox(
        SandboxCommandRequest(
            argv=("ls", str(tmp_path)),
            read_roots=(str(tmp_path),),
            cwd=str(tmp_path),
        )
    )

    assert git_decision.outcome is GitPolicyOutcome.ALLOW
    assert sandbox_decision.outcome is SandboxOutcome.ALLOW


def test_runtime_refuses_agent_kernel_without_explicit_opt_in() -> None:
    runtime = create_phase6_runtime()

    with pytest.raises(Phase6UnavailableError, match="capability unavailable"):
        runtime.create_bounded_agent_loop(
            planner=object(),
            authorizer=object(),
            auditor=object(),
            executor=object(),
            clock=lambda: 0.0,
            id_factory=lambda: "id",
        )


def test_unknown_feature_fails_closed() -> None:
    runtime = create_phase6_runtime()

    with pytest.raises(Phase6UnavailableError, match="capability unavailable"):
        runtime.require_enabled("not_a_feature")
