"""Tests for the Phase 6 pure sandbox policy evaluator."""

from __future__ import annotations

import pytest

from core.phase6_developer.contracts import (
    SandboxCommandRequest,
    SandboxOutcome,
    SandboxReason,
)
from core.phase6_developer.sandbox import evaluate_sandbox_policy


def test_allowed_simple_command() -> None:
    request = SandboxCommandRequest(
        argv=("ls", "-la"), read_roots=("/project",), cwd="/project"
    )
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.ALLOW
    assert decision.reason is SandboxReason.ALLOWED


def test_unknown_executable_denied() -> None:
    request = SandboxCommandRequest(argv=("unknown_binary", "--flag"))
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.UNKNOWN_EXECUTABLE


def test_subcommand_denied() -> None:
    request = SandboxCommandRequest(argv=("npm", "publish"))
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.SUBCOMMAND_DENIED


def test_shell_metacharacters_denied() -> None:
    for argv in (
        ("ls", ";", "rm", "-rf", "/"),
        ("echo", "hello", "|", "cat"),
        ("echo", "$(whoami)"),
    ):
        request = SandboxCommandRequest(argv=argv)
        decision = evaluate_sandbox_policy(request)
        assert decision.outcome is SandboxOutcome.DENY
        assert decision.reason is SandboxReason.SHELL_METACHARACTERS


def test_interpreter_without_script_denied() -> None:
    request = SandboxCommandRequest(argv=("python3", "-c", "print(1)"))
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.INTERPRETER_WITHOUT_SCRIPT


def test_interpreter_with_script_outside_read_roots_denied() -> None:
    request = SandboxCommandRequest(
        argv=("python3", "/outside/script.py"),
        read_roots=("/project",),
    )
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.SCRIPT_NOT_IN_READ_ROOTS


def test_interpreter_with_script_inside_read_roots_allowed() -> None:
    request = SandboxCommandRequest(
        argv=("python3", "/project/script.py"),
        read_roots=("/project",),
        reviewed_scripts=("/project/script.py",),
    )
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.ALLOW


def test_absolute_path_outside_roots_denied() -> None:
    request = SandboxCommandRequest(
        argv=("cat", "/etc/passwd"),
        read_roots=("/project",),
    )
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.PATH_NOT_IN_SCOPE


def test_path_escape_with_dotdot_denied() -> None:
    request = SandboxCommandRequest(
        argv=("cat", "/project/../etc/passwd"),
        read_roots=("/project",),
    )
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.PATH_ESCAPE


def test_network_denied_by_default() -> None:
    request = SandboxCommandRequest(argv=("curl", "http://example.com"))
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.NETWORK_DENIED


def test_network_allowed_when_declared() -> None:
    request = SandboxCommandRequest(
        argv=("curl", "http://example.com"),
        network_allowed=True,
    )
    decision = evaluate_sandbox_policy(request)
    # curl is not in the known executable allowlist, so still denied
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.UNKNOWN_EXECUTABLE


def test_environment_variable_assignment_not_in_allowlist_denied() -> None:
    request = SandboxCommandRequest(argv=("MY_VAR=value", "echo", "x"))
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.ENVIRONMENT_NOT_ALLOWED


def test_environment_variable_assignment_in_allowlist_allowed() -> None:
    request = SandboxCommandRequest(
        argv=("MY_VAR=value", "echo", "x"),
        env_allowlist=("MY_VAR",),
    )
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.ALLOW


def test_timeout_bounds() -> None:
    request = SandboxCommandRequest(argv=("echo", "x"), timeout_seconds=99999)
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.TIMEOUT_EXCEEDED


def test_output_bounds() -> None:
    request = SandboxCommandRequest(argv=("echo", "x"), max_output_bytes=999_999_999)
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.OUTPUT_LIMIT_EXCEEDED


def test_memory_bounds() -> None:
    request = SandboxCommandRequest(argv=("echo", "x"), max_memory_bytes=999_999_999_999)
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.DENY
    assert decision.reason is SandboxReason.MEMORY_LIMIT_EXCEEDED


def test_argv_tuple_only_no_shell_string() -> None:
    request = SandboxCommandRequest(argv=("echo", "hello world"))
    decision = evaluate_sandbox_policy(request)
    assert decision.outcome is SandboxOutcome.ALLOW


def test_content_free_repr() -> None:
    request = SandboxCommandRequest(argv=("unknown", "--secret", "token"))
    decision = evaluate_sandbox_policy(request)
    rep = repr(decision)
    assert "unknown" not in rep
    assert "secret" not in rep
    assert "token" not in rep


def test_mutating_commands_need_separate_policy() -> None:
    for argv in (("rm", "-rf", "."), ("git", "reset", "--hard")):
        decision = evaluate_sandbox_policy(SandboxCommandRequest(argv=argv))
        assert decision.outcome is SandboxOutcome.DENY
        assert decision.reason is SandboxReason.MUTATION_REQUIRES_SEPARATE_POLICY


def test_network_capable_subcommands_denied_when_network_off() -> None:
    for argv in (("npm", "install", "pkg"), ("git", "push", "origin", "main")):
        decision = evaluate_sandbox_policy(SandboxCommandRequest(argv=argv))
        assert decision.outcome is SandboxOutcome.DENY
        assert decision.reason is SandboxReason.NETWORK_DENIED


def test_interpreter_requires_exact_reviewed_script() -> None:
    decision = evaluate_sandbox_policy(
        SandboxCommandRequest(
            argv=("python3", "/project/script.py"),
            read_roots=("/project",),
        )
    )
    assert decision.reason is SandboxReason.REVIEWED_SCRIPT_REQUIRED
