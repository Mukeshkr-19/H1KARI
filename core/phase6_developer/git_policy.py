"""Pure Git policy planner for Phase 6 developer mode.

This module performs no subprocess, no shell execution, and no Git command
invocation. It evaluates caller-supplied ``GitOperationRequest`` and
``GitStateSnapshot`` and returns a fixed ``GitPolicyDecision``.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Set, Tuple

from core.phase6_developer.contracts import (
    GitOperationClass,
    GitOperationRequest,
    GitPolicyDecision,
    GitPolicyOutcome,
    GitPolicyReason,
    GitStateSnapshot,
)


# --- Shell / metacharacter guards -------------------------------------------------------


_SHELL_METACHAR_RE = re.compile(r"[;&|<>$`'\"\\*?\[\]{}()\n\r\x00-\x08\x0e-\x1f\x7f]")
_ENV_VAR_RE = re.compile(r"\$\{?[^\s]+\}?")


# --- Classification helpers --------------------------------------------------------------


def _has_shell_metacharacters(argv: Tuple[str, ...]) -> bool:
    for arg in argv:
        if _SHELL_METACHAR_RE.search(arg):
            return True
        if "\n" in arg or "\r" in arg:
            return True
    return False


def _has_unresolved_variables(argv: Tuple[str, ...]) -> bool:
    for arg in argv:
        if _ENV_VAR_RE.search(arg):
            return True
        if arg.startswith("$"):
            return True
    return False


def _has_glob(argv: Tuple[str, ...]) -> bool:
    for arg in argv:
        if any(ch in arg for ch in "*?["):
            return True
    return False


def _is_broad_root(path: str) -> bool:
    resolved = os.path.expanduser(path)
    if resolved in {"/", "/.", "~", os.path.expanduser("~"), "."}:
        return True
    return False


def _extract_paths(argv: Tuple[str, ...]) -> Set[str]:
    paths: Set[str] = set()
    for arg in argv:
        if arg.startswith("-"):
            continue
        if arg.startswith("/") or arg.startswith("~"):
            paths.add(arg)
    return paths


# --- Operation classification -----------------------------------------------------------


def classify_git_operation(argv: Tuple[str, ...]) -> GitOperationClass:
    """Return the risk class of a Git operation without executing anything.

    Unknown commands return ``GitOperationClass.UNKNOWN``.
    """
    if len(argv) < 2:
        return GitOperationClass.UNKNOWN

    subcommand = argv[1]
    flags = set(argv[2:])

    read_only_subcommands = {
        "status",
        "diff",
        "log",
        "show",
        "ls-files",
        "rev-parse",
        "blame",
        "grep",
        "diff-index",
    }

    if subcommand in read_only_subcommands:
        return GitOperationClass.READ_ONLY

    if subcommand == "branch":
        if "-d" in flags or "-D" in flags or "--delete" in flags:
            return GitOperationClass.DESTRUCTIVE
        if "-m" in flags or "-M" in flags or "--move" in flags:
            return GitOperationClass.REVERSIBLE_MUTATION
        if "--set-upstream" in flags or "-u" in flags or "--unset-upstream" in flags:
            return GitOperationClass.REVERSIBLE_MUTATION
        # "git branch" with no branch name is read-only listing
        if len(argv) == 2:
            return GitOperationClass.READ_ONLY
        # branch creation or other branch mutations
        return GitOperationClass.REVERSIBLE_MUTATION

    if subcommand == "tag":
        if "-d" in flags or "--delete" in flags:
            return GitOperationClass.DESTRUCTIVE
        if "-a" in flags or "-s" in flags or "-f" in flags or "--force" in flags:
            return GitOperationClass.REVERSIBLE_MUTATION
        # Listing is read-only; any positional tag name creates or updates a tag.
        return GitOperationClass.READ_ONLY if len(argv) == 2 else GitOperationClass.REVERSIBLE_MUTATION

    if subcommand == "remote":
        if "-v" in flags or "--verbose" in flags or "show" in flags:
            return GitOperationClass.READ_ONLY
        return GitOperationClass.REMOTE_MUTATION

    if subcommand == "config":
        if "--list" in flags or "--get" in flags or "--get-all" in flags:
            return GitOperationClass.READ_ONLY
        return GitOperationClass.DESTRUCTIVE

    if subcommand in {"add", "checkout", "switch", "stash", "restore"}:
        return GitOperationClass.REVERSIBLE_MUTATION

    if subcommand in {"rm"}:
        return GitOperationClass.DESTRUCTIVE

    if subcommand in {"commit", "merge", "rebase", "am", "cherry-pick"}:
        return GitOperationClass.HISTORY_MUTATION

    if subcommand in {"reset", "clean"}:
        return GitOperationClass.DESTRUCTIVE

    if subcommand in {"push", "pull", "fetch"}:
        return GitOperationClass.REMOTE_MUTATION

    return GitOperationClass.UNKNOWN


# --- Public API -------------------------------------------------------------------------


def evaluate_git_policy(
    request: GitOperationRequest,
    state: GitStateSnapshot,
) -> GitPolicyDecision:
    """Return a deterministic policy decision for a proposed Git operation.

    No Git command is executed. The decision is based solely on the supplied
    argument vector and the caller-provided repository state snapshot.
    """
    if not isinstance(request, GitOperationRequest) or not isinstance(state, GitStateSnapshot):
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.DENY,
            classification=GitOperationClass.UNKNOWN,
            reason=GitPolicyReason.INVALID_REQUEST,
        )

    argv = request.argv

    if not argv or argv[0] != "git":
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.DENY,
            classification=GitOperationClass.UNKNOWN,
            reason=GitPolicyReason.INVALID_REQUEST,
        )

    if len(argv) < 2:
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.DENY,
            classification=GitOperationClass.UNKNOWN,
            reason=GitPolicyReason.SUBCOMMAND_REQUIRED,
        )

    if _has_unresolved_variables(argv):
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.DENY,
            classification=GitOperationClass.UNKNOWN,
            reason=GitPolicyReason.UNRESOLVED_VARIABLE,
        )

    if _has_glob(argv):
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.DENY,
            classification=GitOperationClass.UNKNOWN,
            reason=GitPolicyReason.UNRESOLVED_VARIABLE,
        )

    if _has_shell_metacharacters(argv):
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.DENY,
            classification=GitOperationClass.UNKNOWN,
            reason=GitPolicyReason.SHELL_METACHARACTERS,
        )

    classification = classify_git_operation(argv)

    if classification is GitOperationClass.UNKNOWN:
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.DENY,
            classification=classification,
            reason=GitPolicyReason.UNKNOWN_OPERATION,
        )

    subcommand = argv[1]
    flags = set(argv[2:])

    # Force push detection
    force_push = subcommand == "push" and (
        "--force" in flags
        or "-f" in flags
        or any(flag.startswith("--force-with-lease") for flag in flags)
    )
    if force_push:
        classification = GitOperationClass.REMOTE_MUTATION

    # History rewriting detection
    history_rewrite = (
        subcommand in {"rebase", "am"}
        or (subcommand == "commit" and "--amend" in flags)
    )
    if history_rewrite:
        classification = GitOperationClass.HISTORY_MUTATION

    # Dirty worktree conflict surfacing for history/dangerous operations
    if state.is_dirty() and classification in {
        GitOperationClass.HISTORY_MUTATION,
        GitOperationClass.DESTRUCTIVE,
        GitOperationClass.REMOTE_MUTATION,
    }:
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.REQUIRE_APPROVAL,
            classification=classification,
            reason=GitPolicyReason.DIRTY_WORKTREE,
        )

    # Broad root rejection for destructive operations with explicit paths
    if classification in {GitOperationClass.DESTRUCTIVE, GitOperationClass.HISTORY_MUTATION}:
        for path in _extract_paths(argv):
            if _is_broad_root(path):
                return GitPolicyDecision(
                    outcome=GitPolicyOutcome.DENY,
                    classification=classification,
                    reason=GitPolicyReason.BROAD_ROOT_TARGET,
                )

    if classification is GitOperationClass.READ_ONLY:
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.ALLOW,
            classification=classification,
            reason=GitPolicyReason.READ_ONLY,
        )

    if classification in {
        GitOperationClass.REVERSIBLE_MUTATION,
        GitOperationClass.HISTORY_MUTATION,
    }:
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.REQUIRE_APPROVAL,
            classification=classification,
            reason=GitPolicyReason.APPROVAL_REQUIRED,
        )

    if classification is GitOperationClass.DESTRUCTIVE:
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.REQUIRE_APPROVAL,
            classification=classification,
            reason=GitPolicyReason.DESTRUCTIVE_OPERATION,
        )

    if classification is GitOperationClass.REMOTE_MUTATION:
        reason = GitPolicyReason.FORCE_PUSH if force_push else GitPolicyReason.REMOTE_OPERATION
        return GitPolicyDecision(
            outcome=GitPolicyOutcome.REQUIRE_APPROVAL,
            classification=classification,
            reason=reason,
        )

    return GitPolicyDecision(
        outcome=GitPolicyOutcome.DENY,
        classification=GitOperationClass.UNKNOWN,
        reason=GitPolicyReason.UNKNOWN_OPERATION,
    )
