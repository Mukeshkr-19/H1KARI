"""Tests for the Phase 6 pure Git policy planner."""

from __future__ import annotations

import pytest

from core.phase6_developer.contracts import (
    GitOperationClass,
    GitOperationRequest,
    GitPolicyOutcome,
    GitPolicyReason,
    GitStateSnapshot,
)
from core.phase6_developer.git_policy import classify_git_operation, evaluate_git_policy


# --- Classification matrix ----------------------------------------------------------------


def test_read_only_operations() -> None:
    for argv in (
        ("git", "status"),
        ("git", "diff"),
        ("git", "log"),
        ("git", "show", "HEAD"),
        ("git", "branch"),
        ("git", "ls-files"),
        ("git", "rev-parse", "HEAD"),
    ):
        assert classify_git_operation(argv) is GitOperationClass.READ_ONLY


def test_reversible_mutations() -> None:
    for argv in (
        ("git", "add", "."),
        ("git", "checkout", "-b", "feature"),
        ("git", "switch", "main"),
        ("git", "stash"),
        ("git", "restore", "file.py"),
        ("git", "tag", "v1.0.0"),
    ):
        assert classify_git_operation(argv) is GitOperationClass.REVERSIBLE_MUTATION


def test_history_mutations() -> None:
    for argv in (
        ("git", "commit", "-m", "x"),
        ("git", "merge", "main"),
        ("git", "rebase", "main"),
        ("git", "cherry-pick", "abc"),
    ):
        assert classify_git_operation(argv) is GitOperationClass.HISTORY_MUTATION


def test_destructive_operations() -> None:
    for argv in (
        ("git", "reset", "--hard", "HEAD~1"),
        ("git", "clean", "-fd"),
        ("git", "rm", "file.py"),
        ("git", "branch", "-d", "feature"),
        ("git", "branch", "-D", "feature"),
        ("git", "config", "user.name", "x"),
    ):
        assert classify_git_operation(argv) is GitOperationClass.DESTRUCTIVE


def test_remote_operations() -> None:
    for argv in (
        ("git", "push"),
        ("git", "push", "origin", "main"),
        ("git", "pull"),
        ("git", "fetch"),
    ):
        assert classify_git_operation(argv) is GitOperationClass.REMOTE_MUTATION


def test_force_push_separately_classified() -> None:
    argv = ("git", "push", "--force", "origin", "main")
    decision = evaluate_git_policy(
        GitOperationRequest(argv=argv),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    assert decision.classification is GitOperationClass.REMOTE_MUTATION
    assert decision.outcome is GitPolicyOutcome.REQUIRE_APPROVAL
    assert decision.reason is GitPolicyReason.FORCE_PUSH


def test_force_with_lease_is_force_push() -> None:
    decision = evaluate_git_policy(
        GitOperationRequest(argv=("git", "push", "--force-with-lease", "origin", "main")),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    assert decision.reason is GitPolicyReason.FORCE_PUSH


def test_rebase_separately_classified() -> None:
    argv = ("git", "rebase", "main")
    decision = evaluate_git_policy(
        GitOperationRequest(argv=argv),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    assert decision.classification is GitOperationClass.HISTORY_MUTATION
    assert decision.reason is GitPolicyReason.APPROVAL_REQUIRED


# --- Fail-closed behavior -----------------------------------------------------------------


def test_unknown_operation_denied() -> None:
    decision = evaluate_git_policy(
        GitOperationRequest(argv=("git", "magic")),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    assert decision.outcome is GitPolicyOutcome.DENY
    assert decision.reason is GitPolicyReason.UNKNOWN_OPERATION


def test_missing_subcommand_denied() -> None:
    decision = evaluate_git_policy(
        GitOperationRequest(argv=("git",)),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    assert decision.outcome is GitPolicyOutcome.DENY
    assert decision.reason is GitPolicyReason.SUBCOMMAND_REQUIRED


def test_non_git_executable_denied() -> None:
    decision = evaluate_git_policy(
        GitOperationRequest(argv=("hg", "status")),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    assert decision.outcome is GitPolicyOutcome.DENY
    assert decision.reason is GitPolicyReason.INVALID_REQUEST


# --- Shell metacharacter rejection --------------------------------------------------------


def test_shell_metacharacters_rejected() -> None:
    for argv in (
        ("git", "status", ";", "rm", "-rf", "/"),
        ("git", "log", "|", "grep", "x"),
        ("git", "status", "&&", "echo", "pwned"),
    ):
        decision = evaluate_git_policy(
            GitOperationRequest(argv=argv),
            GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
        )
        assert decision.outcome is GitPolicyOutcome.DENY
        assert decision.reason is GitPolicyReason.SHELL_METACHARACTERS


def test_unresolved_variable_rejected() -> None:
    decision = evaluate_git_policy(
        GitOperationRequest(argv=("git", "show", "$COMMIT")),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    assert decision.outcome is GitPolicyOutcome.DENY
    assert decision.reason is GitPolicyReason.UNRESOLVED_VARIABLE


def test_glob_rejected() -> None:
    decision = evaluate_git_policy(
        GitOperationRequest(argv=("git", "add", "*.py")),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    assert decision.outcome is GitPolicyOutcome.DENY
    assert decision.reason is GitPolicyReason.UNRESOLVED_VARIABLE


# --- Dirty worktree / broad roots ---------------------------------------------------------


def test_dirty_worktree_surfaces_for_history_mutation() -> None:
    state = GitStateSnapshot(
        branch="main",
        head="abc",
        has_untracked=False,
        has_unstaged=True,
        has_staged=False,
        has_unmerged=False,
    )
    decision = evaluate_git_policy(
        GitOperationRequest(argv=("git", "commit", "-m", "x")),
        state,
    )
    assert decision.outcome is GitPolicyOutcome.REQUIRE_APPROVAL
    assert decision.reason is GitPolicyReason.DIRTY_WORKTREE


def test_broad_root_target_rejected_for_destructive() -> None:
    decision = evaluate_git_policy(
        GitOperationRequest(argv=("git", "clean", "-fd", "/")),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    assert decision.outcome is GitPolicyOutcome.DENY
    assert decision.reason is GitPolicyReason.BROAD_ROOT_TARGET


# --- Read-only allow ----------------------------------------------------------------------


def test_read_only_allowed() -> None:
    decision = evaluate_git_policy(
        GitOperationRequest(argv=("git", "status")),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    assert decision.outcome is GitPolicyOutcome.ALLOW
    assert decision.classification is GitOperationClass.READ_ONLY
    assert decision.reason is GitPolicyReason.READ_ONLY


# --- Content-free repr --------------------------------------------------------------------


def test_git_policy_decision_repr_no_argv() -> None:
    decision = evaluate_git_policy(
        GitOperationRequest(argv=("git", "status")),
        GitStateSnapshot(branch="main", head="abc", has_untracked=False, has_unstaged=False, has_staged=False, has_unmerged=False),
    )
    rep = repr(decision)
    assert "status" not in rep
    assert "allow" in rep
