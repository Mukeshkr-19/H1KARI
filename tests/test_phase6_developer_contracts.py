"""Tests for core.phase6_developer contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.phase6_developer.contracts import (
    ChangeIntent,
    FileKind,
    FileSnapshot,
    GitOperationClass,
    GitOperationRequest,
    GitPolicyDecision,
    GitPolicyOutcome,
    GitPolicyReason,
    GitStateSnapshot,
    ImportRecord,
    ReferenceRecord,
    RelationshipEdge,
    RelationshipType,
    RepositoryHit,
    RepositoryIndex,
    RepositoryPolicy,
    RepositoryQuery,
    RepositoryQueryResult,
    RepositoryReason,
    RepositoryRoot,
    SandboxCommandRequest,
    SandboxOutcome,
    SandboxPolicyDecision,
    SandboxReason,
    ScoreBreakdown,
    SymbolRecord,
)


# --- RepositoryRoot -------------------------------------------------------------------


def test_repository_root_requires_absolute_directory(tmp_path: Path) -> None:
    assert RepositoryRoot(tmp_path).path == tmp_path.resolve()


def test_repository_root_rejects_non_directory(tmp_path: Path) -> None:
    non_dir = tmp_path / "not_a_dir"
    with pytest.raises(ValueError):
        RepositoryRoot(non_dir)


# --- RepositoryPolicy -------------------------------------------------------------------


def test_repository_policy_defaults() -> None:
    policy = RepositoryPolicy()
    assert policy.max_files == 10_000
    assert policy.max_file_bytes == 5_000_000
    assert policy.max_total_bytes == 100_000_000
    assert not policy.follow_symlinks


def test_repository_policy_bounds_validation() -> None:
    with pytest.raises(ValueError):
        RepositoryPolicy(max_files=0)
    with pytest.raises(ValueError):
        RepositoryPolicy(max_file_bytes=-1)
    with pytest.raises(ValueError):
        RepositoryPolicy(max_total_bytes=-1)
    with pytest.raises(ValueError):
        RepositoryPolicy(max_depth=0)
    with pytest.raises(ValueError):
        RepositoryPolicy(max_line_length=0)
    with pytest.raises(ValueError):
        RepositoryPolicy(max_files=10_001)
    with pytest.raises(ValueError):
        RepositoryQuery("x", max_hits=101)


def test_repository_policy_repr_is_content_free() -> None:
    policy = RepositoryPolicy()
    rep = repr(policy)
    assert "ignore_patterns" not in rep
    assert "max_files" in rep


# --- FileSnapshot -----------------------------------------------------------------------


def test_filesnapshot_excerpt_bound() -> None:
    lines = tuple("line" for _ in range(20))
    fs = FileSnapshot(
        relative_path="x/y.py",
        kind=FileKind.PYTHON,
        size_bytes=10,
        is_binary=False,
        lines=lines[:12],
        line_count=20,
        sha256_hex="a" * 64,
    )
    assert fs.line_count == 20
    assert len(fs.lines) <= 12


def test_filesnapshot_repr_is_content_free() -> None:
    fs = FileSnapshot(
        relative_path="secret.py",
        kind=FileKind.PYTHON,
        size_bytes=10,
        is_binary=False,
        lines=("def foo():", "    pass"),
        line_count=2,
        sha256_hex="a" * 64,
    )
    rep = repr(fs)
    assert "secret" not in rep
    assert "line_count" in rep


# --- SymbolRecord, ImportRecord, ReferenceRecord, RelationshipEdge ------------------------


def test_symbol_record_validation() -> None:
    with pytest.raises(ValueError):
        SymbolRecord("", "function", "x.py", 1, 1)


def test_import_record_validation() -> None:
    with pytest.raises(ValueError):
        ImportRecord("os", "path", True, "x.py", 0)


def test_reference_record_validation() -> None:
    with pytest.raises(ValueError):
        ReferenceRecord("a", "b", RelationshipType.CALLS, "x.py", -1)


def test_relationship_edge_validation() -> None:
    with pytest.raises(ValueError):
        RelationshipEdge("", "b", RelationshipType.DEFINES, "x.py:1")


# --- RepositoryIndex ----------------------------------------------------------------------


def test_repository_index_validation() -> None:
    with pytest.raises(ValueError):
        RepositoryIndex(
            files=("not_a_file",),  # type: ignore[arg-type]
            symbols=(),
            imports=(),
            references=(),
            edges=(),
            total_bytes=0,
        )


def test_repository_index_repr_is_content_free() -> None:
    index = RepositoryIndex(
        files=(),
        symbols=(),
        imports=(),
        references=(),
        edges=(),
        total_bytes=0,
    )
    rep = repr(index)
    assert "files=" in rep
    assert "total=" not in rep.lower()


# --- RepositoryQuery and RepositoryHit --------------------------------------------------


def test_repository_query_validation() -> None:
    with pytest.raises(ValueError):
        RepositoryQuery("", max_hits=0)


def test_repository_hit_validation() -> None:
    with pytest.raises(ValueError):
        RepositoryHit(
            relative_path="x.py",
            symbol="foo",
            line_start=1,
            line_end=0,
            excerpt=(),
            score=1.0,
            score_breakdown=ScoreBreakdown(),
            provenance="x.py:1",
        )


def test_score_breakdown_total() -> None:
    score = ScoreBreakdown(exact_match=1, token_match=2, substring_match=1, relationship_bonus=1)
    assert score.total() == pytest.approx(1.0 + 1.0 + 0.2 + 0.3)


# --- Git policy contracts ----------------------------------------------------------------


def test_git_operation_request_validation() -> None:
    # Non-git argv is accepted structurally; policy denies it deterministically.
    request = GitOperationRequest(argv=("not-git", "status"))
    assert request.argv == ("not-git", "status")
    with pytest.raises(ValueError):
        GitOperationRequest(argv=())
    assert "missing" in repr(GitOperationRequest(argv=("git",)))


def test_git_state_snapshot_dirty() -> None:
    state = GitStateSnapshot(
        branch="main",
        head="abc123",
        has_untracked=False,
        has_unstaged=True,
        has_staged=False,
        has_unmerged=False,
    )
    assert state.is_dirty()
    untracked = GitStateSnapshot(
        branch="main", head="abc123", has_untracked=True,
        has_unstaged=False, has_staged=False, has_unmerged=False,
    )
    assert untracked.is_dirty()


def test_git_policy_decision_validation() -> None:
    with pytest.raises(ValueError):
        GitPolicyDecision(
            outcome=GitPolicyOutcome.ALLOW,
            classification=GitOperationClass.READ_ONLY,
            reason="not_an_enum",  # type: ignore[arg-type]
        )


def test_git_policy_decision_repr_is_content_free() -> None:
    decision = GitPolicyDecision(
        outcome=GitPolicyOutcome.ALLOW,
        classification=GitOperationClass.READ_ONLY,
        reason=GitPolicyReason.READ_ONLY,
    )
    rep = repr(decision)
    assert "allow" in rep
    assert "read_only" in rep


# --- Sandbox contracts --------------------------------------------------------------------


def test_sandbox_command_request_validation() -> None:
    with pytest.raises(ValueError):
        SandboxCommandRequest(argv=())
    with pytest.raises(ValueError):
        SandboxCommandRequest(argv=("ls",), timeout_seconds=0)


def test_sandbox_policy_decision_validation() -> None:
    with pytest.raises(ValueError):
        SandboxPolicyDecision(
            outcome=SandboxOutcome.ALLOW,
            reason="not_an_enum",  # type: ignore[arg-type]
        )


def test_sandbox_policy_decision_repr_is_content_free() -> None:
    decision = SandboxPolicyDecision(
        outcome=SandboxOutcome.DENY,
        reason=SandboxReason.UNKNOWN_EXECUTABLE,
    )
    rep = repr(decision)
    assert "deny" in rep
    assert "argv" not in rep


# --- ChangeIntent -------------------------------------------------------------------------


def test_change_intent_validation() -> None:
    with pytest.raises(ValueError):
        ChangeIntent("", "x.py", "description")
