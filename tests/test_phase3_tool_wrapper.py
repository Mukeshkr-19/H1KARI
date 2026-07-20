"""Tests for the fail-closed third-party invocation wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.productivity.contracts import ProductivityAction, TargetKind
from core.productivity.tool_permissions import (
    PermissionDecision,
    PermissionManifest,
    PermissionRequest,
    ToolKind,
    ToolPermission,
)
from core.productivity.tool_wrapper import (
    ToolPolicyWrapper,
    ToolRegistration,
    ToolWrapperCode,
    ToolWrapperConfigurationError,
    ToolWrapperResult,
)
from core.productivity.bootstrap import create_tool_policy_wrapper


def _manifest() -> PermissionManifest:
    return PermissionManifest(
        tools=(
            ToolPermission("mcp_alpha", ToolKind.MCP, frozenset({"server-a"})),
            ToolPermission("skill_beta", ToolKind.SKILL, frozenset({"skill-b"})),
        )
    )


def _mcp_request() -> PermissionRequest:
    return PermissionRequest(
        tool_id="mcp_alpha",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="server-a",
    )


def _registrations(mcp, skill=lambda: None) -> tuple[ToolRegistration, ...]:
    return (
        ToolRegistration(
            "mcp_alpha",
            ToolKind.MCP,
            ProductivityAction.MCP_EXECUTE,
            TargetKind.MCP_SERVER,
            "server-a",
            mcp,
        ),
        ToolRegistration(
            "skill_beta",
            ToolKind.SKILL,
            ProductivityAction.SKILL_EXECUTE,
            TargetKind.SKILL,
            "skill-b",
            skill,
        ),
    )


def test_exact_allowed_request_invokes_once_and_discards_result() -> None:
    calls = 0

    def tool() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"private": "result"}

    result = ToolPolicyWrapper(_manifest(), _registrations(tool)).invoke(
        _mcp_request()
    )

    assert result == ToolWrapperResult(ToolWrapperCode.OK)
    assert result.succeeded is True
    assert calls == 1
    assert not hasattr(result, "payload")


def test_permission_evaluation_occurs_immediately_before_invocation(monkeypatch) -> None:
    order: list[str] = []

    def evaluator(manifest, request):
        order.append("evaluate")
        return PermissionDecision.ALLOW, object()

    def tool() -> None:
        order.append("invoke")

    monkeypatch.setattr("core.productivity.tool_wrapper.evaluate", evaluator)
    wrapper = ToolPolicyWrapper(_manifest(), _registrations(tool))

    assert wrapper.invoke(_mcp_request()).code is ToolWrapperCode.OK
    assert order == ["evaluate", "invoke"]


def test_denied_request_never_invokes(monkeypatch) -> None:
    calls = 0

    def tool() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(
        "core.productivity.tool_wrapper.evaluate",
        lambda manifest, request: (PermissionDecision.DENY, object()),
    )
    result = ToolPolicyWrapper(_manifest(), _registrations(tool)).invoke(
        _mcp_request()
    )

    assert result.code is ToolWrapperCode.PERMISSION_DENIED
    assert calls == 0


def test_exact_structural_mismatch_never_reaches_callable() -> None:
    calls = 0

    def tool() -> None:
        nonlocal calls
        calls += 1

    mismatched = PermissionRequest(
        tool_id="mcp_alpha",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="server-b",
    )

    result = ToolPolicyWrapper(_manifest(), _registrations(tool)).invoke(mismatched)
    assert result.code is ToolWrapperCode.BINDING_MISMATCH
    assert calls == 0


def test_callable_exception_is_replaced_with_fixed_failure() -> None:
    def tool() -> None:
        raise RuntimeError("private payload and provider details")

    result = ToolPolicyWrapper(_manifest(), _registrations(tool)).invoke(
        _mcp_request()
    )

    assert result == ToolWrapperResult(ToolWrapperCode.INVOCATION_FAILED)
    assert "private" not in repr(result)
    assert "provider" not in repr(result)


def test_disabled_wrapper_denies_without_manifest_or_registration() -> None:
    wrappers = (ToolPolicyWrapper(), ToolPolicyWrapper.disabled())

    for wrapper in wrappers:
        assert wrapper.invoke(_mcp_request()) == ToolWrapperResult(
            ToolWrapperCode.DISABLED
        )
        assert wrapper.invoke(object()) == ToolWrapperResult(ToolWrapperCode.DISABLED)


def test_production_factory_defaults_to_deny_all() -> None:
    wrapper = create_tool_policy_wrapper()
    assert wrapper.invoke(_mcp_request()) == ToolWrapperResult(
        ToolWrapperCode.DISABLED
    )


def test_disabled_constructor_rejects_registrations_without_manifest() -> None:
    with pytest.raises(ToolWrapperConfigurationError):
        ToolPolicyWrapper(None, _registrations(lambda: None))


@pytest.mark.parametrize(
    "registrations",
    [
        (),
        _registrations(lambda: None)[:1],
        _registrations(lambda: None) + _registrations(lambda: None)[:1],
    ],
)
def test_missing_or_duplicate_registration_is_rejected(registrations) -> None:
    with pytest.raises(ToolWrapperConfigurationError) as exc_info:
        ToolPolicyWrapper(_manifest(), registrations)
    assert str(exc_info.value) == "tool wrapper configuration invalid"


@pytest.mark.parametrize(
    "registration",
    [
        ToolRegistration(
            "mcp_alpha",
            ToolKind.SKILL,
            ProductivityAction.SKILL_EXECUTE,
            TargetKind.SKILL,
            "server-a",
            lambda: None,
        ),
        ToolRegistration(
            "mcp_alpha",
            ToolKind.MCP,
            ProductivityAction.MCP_EXECUTE,
            TargetKind.MCP_SERVER,
            "server-b",
            lambda: None,
        ),
    ],
)
def test_mismatched_registration_is_rejected(registration) -> None:
    registrations = (registration, _registrations(lambda: None)[1])
    with pytest.raises(ToolWrapperConfigurationError):
        ToolPolicyWrapper(_manifest(), registrations)


def test_invalid_request_returns_typed_failure() -> None:
    wrapper = ToolPolicyWrapper(_manifest(), _registrations(lambda: None))
    assert wrapper.invoke("not a request") == ToolWrapperResult(
        ToolWrapperCode.INVALID_REQUEST
    )


def test_registration_repr_excludes_identifiers_targets_and_callable() -> None:
    registration = _registrations(lambda: None)[0]
    text = repr(registration)
    assert "mcp_alpha" not in text
    assert "server-a" not in text
    assert "lambda" not in text


def test_source_has_no_direct_side_effect_facilities() -> None:
    source = (
        Path(__file__).parents[1] / "core" / "productivity" / "tool_wrapper.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "import logging",
        "import socket",
        "import subprocess",
        "import requests",
        "from urllib",
        "open(",
        "print(",
    ):
        assert forbidden not in source
