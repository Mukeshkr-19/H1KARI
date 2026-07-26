"""Pure sandbox policy evaluator for Phase 6 developer mode.

This module performs no subprocess, filesystem, network, or environment
access. It evaluates a caller-supplied ``SandboxCommandRequest`` and returns
a fixed ``SandboxPolicyDecision``.
"""

from __future__ import annotations

import os
import re
from typing import Tuple

from core.phase6_developer.contracts import (
    SandboxCommandRequest,
    SandboxOutcome,
    SandboxPolicyDecision,
    SandboxReason,
)


# --- Allowed execution surface ----------------------------------------------------------


_MAX_TIMEOUT_SECONDS = 3600
_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
_MAX_MEMORY_BYTES = 4 * 1024 * 1024 * 1024

# Exact executable basenames that may be used in the sandbox.
_KNOWN_EXECUTABLES: dict[str, frozenset[str]] = {
    "git": frozenset(
        {
            "status",
            "diff",
            "log",
            "show",
            "branch",
            "switch",
            "checkout",
            "add",
            "commit",
            "stash",
            "restore",
            "pull",
            "push",
            "fetch",
            "remote",
            "config",
            "tag",
            "rm",
            "reset",
            "clean",
            "rebase",
            "merge",
            "cherry-pick",
            "am",
            "blame",
            "grep",
            "ls-files",
            "rev-parse",
        }
    ),
    "python": frozenset(),
    "python3": frozenset(),
    "node": frozenset(),
    "npm": frozenset({"run", "install", "ci", "test"}),
    "pytest": frozenset(),
    "ls": frozenset(),
    "cat": frozenset(),
    "cp": frozenset(),
    "mv": frozenset(),
    "mkdir": frozenset(),
    "rm": frozenset(),
    "echo": frozenset(),
}

_INTERPRETERS: frozenset[str] = frozenset(
    {"python", "python3", "node", "bash", "sh", "zsh", "ruby", "perl"}
)

_NETWORK_EXECUTABLES: frozenset[str] = frozenset(
    {"curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "netcat", "telnet"}
)

_GIT_READ_ONLY = frozenset(
    {"status", "diff", "log", "show", "blame", "grep", "ls-files", "rev-parse"}
)
_MUTATING_EXECUTABLES = frozenset({"cp", "mv", "mkdir", "rm"})

# Shell operators, redirections, globs, command substitution, unresolved vars.
_SHELL_METACHAR_RE = re.compile(r"[;&|<>$`'\"\\*?\[\]{}()\n\r\x00-\x08\x0e-\x1f\x7f]")
_ENV_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=.*$")
_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


# --- Path containment -------------------------------------------------------------------


def _common_prefix(resolved: str, root: str) -> bool:
    try:
        return os.path.commonpath([resolved, root]) == root
    except ValueError:
        return False


def _is_within_any(path: str, roots: Tuple[str, ...]) -> bool:
    resolved_path = os.path.realpath(os.path.expanduser(path))
    for root in roots:
        resolved_root = os.path.realpath(os.path.expanduser(root))
        if _common_prefix(resolved_path, resolved_root):
            return True
    return False


# --- Validation helpers -----------------------------------------------------------------


def _has_shell_metacharacters(argv: Tuple[str, ...]) -> bool:
    for arg in argv:
        if _SHELL_METACHAR_RE.search(arg):
            return True
        if "\n" in arg or "\r" in arg:
            return True
    return False


def _absolute_paths(argv: Tuple[str, ...]) -> set[str]:
    paths: set[str] = set()
    for arg in argv:
        # Handle both bare paths and --flag=/path style values.
        for part in arg.split("=", 1):
            if part.startswith("/") or part.startswith("~"):
                paths.add(part)
    return paths


def _is_interpreter_abuse(argv: Tuple[str, ...]) -> bool:
    if len(argv) < 2:
        return True
    flag_arg = argv[1]
    # Block inline code execution flags
    if flag_arg in {"-c", "-", "-m", "-e", "--eval"}:
        return True
    # Block relative or missing script targets
    if not flag_arg.startswith("/"):
        return True
    return False


def _strip_env_assignments(
    argv: Tuple[str, ...],
    env_allowlist: Tuple[str, ...],
) -> Tuple[Tuple[str, ...], SandboxPolicyDecision | None]:
    """Return (remaining_argv, optional_error_decision).

    Leading ``KEY=value`` assignments are validated against the allowlist.
    """
    env_allowed = set(env_allowlist)
    stripped: list[str] = []
    for arg in argv:
        match = _ENV_ASSIGNMENT_RE.match(arg)
        if match:
            var_name = match.group(1)
            if var_name not in env_allowed:
                return (), SandboxPolicyDecision(
                    outcome=SandboxOutcome.DENY,
                    reason=SandboxReason.ENVIRONMENT_NOT_ALLOWED,
                )
            continue
        stripped.append(arg)
    return tuple(stripped), None


# --- Public API -------------------------------------------------------------------------


def evaluate_sandbox_policy(request: SandboxCommandRequest) -> SandboxPolicyDecision:
    """Return a deterministic sandbox policy decision for a proposed command.

    No command is executed. The decision is based solely on the supplied argv,
    declared roots, and bounds.
    """
    if not isinstance(request, SandboxCommandRequest):
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.INVALID_REQUEST,
        )

    argv, error = _strip_env_assignments(request.argv, request.env_allowlist)
    if error is not None:
        return error

    if not argv:
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.UNKNOWN_EXECUTABLE,
        )

    executable = os.path.basename(argv[0])
    if not executable:
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.UNKNOWN_EXECUTABLE,
        )

    # Network restriction before other checks so network tools are denied even
    # if the command otherwise looks safe.
    if not request.network_allowed:
        if executable in _NETWORK_EXECUTABLES:
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.NETWORK_DENIED,
            )
        for arg in argv:
            if _URL_RE.match(arg):
                return SandboxPolicyDecision(
                    outcome=SandboxOutcome.DENY,
                    reason=SandboxReason.NETWORK_DENIED,
                )

    # Interpreter abuse prevention (must run before shell metacharacter checks
    # because inline code like ``python3 -c 'print(1)'`` contains parentheses).
    if executable in _INTERPRETERS:
        if _is_interpreter_abuse(argv):
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.INTERPRETER_WITHOUT_SCRIPT,
            )
        script_path = argv[1]
        if not _is_within_any(script_path, request.read_roots):
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.SCRIPT_NOT_IN_READ_ROOTS,
            )
        reviewed = {
            os.path.realpath(os.path.expanduser(path)) for path in request.reviewed_scripts
        }
        if os.path.realpath(script_path) not in reviewed:
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.REVIEWED_SCRIPT_REQUIRED,
            )

    if _has_shell_metacharacters(argv):
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.SHELL_METACHARACTERS,
        )

    if executable not in _KNOWN_EXECUTABLES:
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.UNKNOWN_EXECUTABLE,
        )

    # This evaluator never grants mutation authority by itself. Git mutations
    # must also pass the dedicated Git policy and action approval layers.
    if executable in _MUTATING_EXECUTABLES:
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.MUTATION_REQUIRES_SEPARATE_POLICY,
        )
    if executable == "git" and (len(argv) < 2 or argv[1] not in _GIT_READ_ONLY):
        if not request.network_allowed and len(argv) > 1 and argv[1] in {"push", "pull", "fetch"}:
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.NETWORK_DENIED,
            )
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.MUTATION_REQUIRES_SEPARATE_POLICY,
        )
    if executable == "npm":
        if len(argv) < 2 or argv[1] not in _KNOWN_EXECUTABLES["npm"]:
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.SUBCOMMAND_DENIED,
            )
        if not request.network_allowed and len(argv) > 1 and argv[1] in {"install", "ci"}:
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.NETWORK_DENIED,
            )
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.MUTATION_REQUIRES_SEPARATE_POLICY,
        )

    allowed_subcommands = _KNOWN_EXECUTABLES[executable]
    if allowed_subcommands:
        if len(argv) < 2 or argv[1] not in allowed_subcommands:
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.SUBCOMMAND_DENIED,
            )

    if executable in {"ls", "cat"}:
        if any(".." in path for path in _absolute_paths(argv)):
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.PATH_ESCAPE,
            )
        if not request.read_roots:
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.PATH_NOT_IN_SCOPE,
            )
        if request.cwd is None or not _is_within_any(request.cwd, request.read_roots):
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.PATH_NOT_IN_SCOPE,
            )

    # Path escape / containment for all absolute paths
    for path in _absolute_paths(argv):
        if ".." in path:
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.PATH_ESCAPE,
            )
        if not _is_within_any(path, request.read_roots + request.write_roots):
            return SandboxPolicyDecision(
                outcome=SandboxOutcome.DENY,
                reason=SandboxReason.PATH_NOT_IN_SCOPE,
            )

    # Resource bounds
    if request.timeout_seconds is not None and request.timeout_seconds > _MAX_TIMEOUT_SECONDS:
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.TIMEOUT_EXCEEDED,
        )
    if request.max_output_bytes is not None and request.max_output_bytes > _MAX_OUTPUT_BYTES:
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.OUTPUT_LIMIT_EXCEEDED,
        )
    if request.max_memory_bytes is not None and request.max_memory_bytes > _MAX_MEMORY_BYTES:
        return SandboxPolicyDecision(
            outcome=SandboxOutcome.DENY,
            reason=SandboxReason.MEMORY_LIMIT_EXCEEDED,
        )

    return SandboxPolicyDecision(
        outcome=SandboxOutcome.ALLOW,
        reason=SandboxReason.ALLOWED,
    )
