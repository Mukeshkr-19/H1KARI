"""Deterministic fail-closed tests for the Phase 3 third-party permission kernel.

These tests never exercise I/O, persistence, network, or external SDKs. They
verify pure authorization behavior, immutability, and privacy-safe representations.
"""

from __future__ import annotations

import pytest

from core.productivity.contracts import ProductivityAction, TargetKind
from core.productivity.tool_permissions import (
    PermissionDecision,
    PermissionManifest,
    PermissionReason,
    PermissionRequest,
    ToolKind,
    ToolPermission,
    build_manifest,
    evaluate,
)


@pytest.fixture
def manifest() -> PermissionManifest:
    return build_manifest(
        {
            "mcp.calendar": {
                "kind": "mcp",
                "targets": ["calendar.google.com", "calendar.office.com"],
            },
            "skill.summarize": {
                "kind": "skill",
                "targets": ["notes", "docs"],
            },
        }
    )


@pytest.fixture
def owner_mcp_request() -> PermissionRequest:
    return PermissionRequest(
        tool_id="mcp.calendar",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="calendar.google.com",
    )


# --- Allow path ---------------------------------------------------------------

def test_allow_exact_mcp(manifest: PermissionManifest, owner_mcp_request: PermissionRequest) -> None:
    decision, reason = evaluate(manifest, owner_mcp_request)
    assert decision is PermissionDecision.ALLOW
    assert reason is PermissionReason.OK


def test_allow_exact_skill(manifest: PermissionManifest) -> None:
    request = PermissionRequest(
        tool_id="skill.summarize",
        kind=ToolKind.SKILL,
        action=ProductivityAction.SKILL_EXECUTE,
        target_kind=TargetKind.SKILL,
        target_value="notes",
    )
    decision, reason = evaluate(manifest, request)
    assert decision is PermissionDecision.ALLOW
    assert reason is PermissionReason.OK


def test_allow_second_declared_target(manifest: PermissionManifest) -> None:
    request = PermissionRequest(
        tool_id="mcp.calendar",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="calendar.office.com",
    )
    decision, reason = evaluate(manifest, request)
    assert decision is PermissionDecision.ALLOW


# --- Default deny -------------------------------------------------------------

def test_deny_undeclared_tool(manifest: PermissionManifest) -> None:
    request = PermissionRequest(
        tool_id="mcp.unknown",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="calendar.google.com",
    )
    decision, reason = evaluate(manifest, request)
    assert decision is PermissionDecision.DENY
    assert reason is PermissionReason.UNDECLARED_TOOL


def test_deny_undeclared_target_value(manifest: PermissionManifest) -> None:
    request = PermissionRequest(
        tool_id="mcp.calendar",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="calendar.evil.com",
    )
    decision, reason = evaluate(manifest, request)
    assert decision is PermissionDecision.DENY
    assert reason is PermissionReason.TARGET_VALUE_MISMATCH


# --- Cross-kind denial --------------------------------------------------------

def test_deny_cross_kind_action(manifest: PermissionManifest) -> None:
    # skill tool requested with MCP action
    request = PermissionRequest(
        tool_id="skill.summarize",
        kind=ToolKind.SKILL,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.SKILL,
        target_value="notes",
    )
    decision, reason = evaluate(manifest, request)
    assert decision is PermissionDecision.DENY
    assert reason is PermissionReason.WRONG_ACTION


def test_deny_cross_kind_request_kind(manifest: PermissionManifest) -> None:
    # declared as mcp but request says skill
    request = PermissionRequest(
        tool_id="mcp.calendar",
        kind=ToolKind.SKILL,
        action=ProductivityAction.SKILL_EXECUTE,
        target_kind=TargetKind.SKILL,
        target_value="notes",
    )
    decision, reason = evaluate(manifest, request)
    assert decision is PermissionDecision.DENY
    assert reason is PermissionReason.WRONG_KIND


def test_deny_wrong_target_kind(manifest: PermissionManifest) -> None:
    request = PermissionRequest(
        tool_id="mcp.calendar",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.SKILL,
        target_value="calendar.google.com",
    )
    decision, reason = evaluate(manifest, request)
    assert decision is PermissionDecision.DENY
    assert reason is PermissionReason.TARGET_KIND_MISMATCH


# --- Malformed manifests (fail closed at construction) ------------------------

def test_malformed_manifest_bad_kind() -> None:
    with pytest.raises(ValueError):
        build_manifest({"mcp.x": {"kind": "shell", "targets": ["a"]}})


def test_malformed_manifest_missing_targets() -> None:
    with pytest.raises(ValueError):
        build_manifest({"mcp.x": {"kind": "mcp"}})


def test_malformed_manifest_non_string_target() -> None:
    with pytest.raises(ValueError):
        build_manifest({"mcp.x": {"kind": "mcp", "targets": [123]}})


def test_empty_manifest_rejected() -> None:
    with pytest.raises(ValueError):
        build_manifest({})


def test_toolpermission_empty_targets_rejected() -> None:
    with pytest.raises(ValueError):
        ToolPermission(tool_id="mcp.x", kind=ToolKind.MCP, targets=frozenset())


def test_duplicate_tool_id_rejected() -> None:
    with pytest.raises(ValueError):
        PermissionManifest(
            tools=(
                ToolPermission(
                    tool_id="mcp.x", kind=ToolKind.MCP, targets=frozenset(["a"])
                ),
                ToolPermission(
                    tool_id="mcp.x", kind=ToolKind.MCP, targets=frozenset(["b"])
                ),
            )
        )


def test_oversized_target_value_rejected() -> None:
    big = "a" * 5000
    with pytest.raises(ValueError):
        ToolPermission(tool_id="mcp.x", kind=ToolKind.MCP, targets=frozenset([big]))


def test_control_char_target_rejected() -> None:
    with pytest.raises(ValueError):
        ToolPermission(tool_id="mcp.x", kind=ToolKind.MCP, targets=frozenset(["bad\nvalue"]))


def test_nul_target_rejected() -> None:
    with pytest.raises(ValueError):
        ToolPermission(tool_id="mcp.x", kind=ToolKind.MCP, targets=frozenset(["bad\x00value"]))


# --- Shared prefixes ----------------------------------------------------------

def test_shared_prefix_not_substring_match() -> None:
    manifest = build_manifest(
        {"mcp.x": {"kind": "mcp", "targets": ["api.example.com"]}}
    )
    # request with a longer value sharing the prefix must NOT match
    request = PermissionRequest(
        tool_id="mcp.x",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="api.example.com.evil.com",
    )
    decision, reason = evaluate(manifest, request)
    assert decision is PermissionDecision.DENY
    assert reason is PermissionReason.TARGET_VALUE_MISMATCH


def test_shared_prefix_distinct_tool_ids() -> None:
    manifest = build_manifest(
        {
            "mcp.service": {"kind": "mcp", "targets": ["srv"]},
            "mcp.service.admin": {"kind": "mcp", "targets": ["srv"]},
        }
    )
    # the longer id must not be granted via the shorter id
    request = PermissionRequest(
        tool_id="mcp.service",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="srv",
    )
    decision, _ = evaluate(manifest, request)
    assert decision is PermissionDecision.ALLOW
    other = PermissionRequest(
        tool_id="mcp.service.admin",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="srv",
    )
    decision2, _ = evaluate(manifest, other)
    assert decision2 is PermissionDecision.ALLOW


# --- Case differences ---------------------------------------------------------

def test_case_difference_tool_id_denied() -> None:
    manifest = build_manifest(
        {"mcp.calendar": {"kind": "mcp", "targets": ["Calendar.Google.com"]}}
    )
    # canonical tool ids are lowercase; an uppercase request id is rejected at
    # construction (fail-closed) and can never reach evaluate().
    with pytest.raises(ValueError):
        PermissionRequest(
            tool_id="MCP.Calendar",
            kind=ToolKind.MCP,
            action=ProductivityAction.MCP_EXECUTE,
            target_kind=TargetKind.MCP_SERVER,
            target_value="Calendar.Google.com",
        )


def test_case_difference_target_value_denied() -> None:
    manifest = build_manifest(
        {"mcp.x": {"kind": "mcp", "targets": ["calendar.google.com"]}}
    )
    request = PermissionRequest(
        tool_id="mcp.x",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="Calendar.Google.com",
    )
    decision, reason = evaluate(manifest, request)
    assert decision is PermissionDecision.DENY
    assert reason is PermissionReason.TARGET_VALUE_MISMATCH


# --- Wildcards ----------------------------------------------------------------

def test_wildcard_target_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_manifest({"mcp.x": {"kind": "mcp", "targets": ["*"]}})


def test_prefix_wildcard_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_manifest({"mcp.x": {"kind": "mcp", "targets": ["api.*"]}})


def test_duplicate_targets_and_unknown_manifest_fields_are_rejected() -> None:
    with pytest.raises(ValueError):
        build_manifest({"mcp.x": {"kind": "mcp", "targets": ["a", "a"]}})
    with pytest.raises(ValueError):
        build_manifest(
            {"mcp.x": {"kind": "mcp", "targets": ["a"], "extra": True}}
        )


# --- Unicode / confusable input ----------------------------------------------

def test_unicode_target_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        ToolPermission(
            tool_id="mcp.x",
            kind=ToolKind.MCP,
            targets=frozenset(["саlendаr.com"]),  # confusable cyrillic
        )


def test_unicode_tool_id_rejected() -> None:
    with pytest.raises(ValueError):
        ToolPermission(
            tool_id="mcp.саlendаr",
            kind=ToolKind.MCP,
            targets=frozenset(["a"]),
        )


def test_unicode_request_target_denied() -> None:
    manifest = build_manifest(
        {"mcp.x": {"kind": "mcp", "targets": ["calendar.google.com"]}}
    )
    # A non-ASCII target value is rejected at construction (fail-closed) and can
    # never reach evaluate().
    with pytest.raises(ValueError):
        PermissionRequest(
            tool_id="mcp.x",
            kind=ToolKind.MCP,
            action=ProductivityAction.MCP_EXECUTE,
            target_kind=TargetKind.MCP_SERVER,
            target_value="саlendаr.com",
        )


# --- Immutability / deep-freeze ----------------------------------------------

def test_manifest_is_immutable() -> None:
    manifest = build_manifest(
        {"mcp.x": {"kind": "mcp", "targets": ["a"]}}
    )
    with pytest.raises(Exception):
        manifest.tools = ()  # type: ignore[misc]


def test_toolpermission_is_immutable() -> None:
    tool = ToolPermission(tool_id="mcp.x", kind=ToolKind.MCP, targets=frozenset(["a"]))
    with pytest.raises(Exception):
        tool.targets = frozenset(["b"])  # type: ignore[misc]


def test_targets_frozenset_is_hashable_and_immutable() -> None:
    tool = ToolPermission(tool_id="mcp.x", kind=ToolKind.MCP, targets=frozenset(["a", "b"]))
    assert isinstance(tool.targets, frozenset)
    # membership is exact and stable
    assert "a" in tool.targets
    assert "c" not in tool.targets


def test_manifest_lookup_is_exact() -> None:
    manifest = build_manifest(
        {"mcp.x": {"kind": "mcp", "targets": ["a"]}}
    )
    assert manifest.lookup("mcp.x") is not None
    assert manifest.lookup("MCP.X") is None
    assert manifest.lookup("mcp.y") is None


# --- Privacy-safe representations --------------------------------------------

def test_repr_excludes_raw_values() -> None:
    tool = ToolPermission(
        tool_id="mcp.secret", kind=ToolKind.MCP, targets=frozenset(["secret-internal-host"])
    )
    rep = repr(tool)
    assert "secret-internal-host" not in rep
    assert "mcp.secret" not in rep
    assert "target_count" in rep


def test_manifest_repr_excludes_raw_values() -> None:
    manifest = build_manifest(
        {"mcp.secret": {"kind": "mcp", "targets": ["secret-internal-host"]}}
    )
    rep = repr(manifest)
    assert "secret-internal-host" not in rep
    assert "mcp.secret" not in rep


def test_request_repr_excludes_raw_values() -> None:
    request = PermissionRequest(
        tool_id="mcp.secret",
        kind=ToolKind.MCP,
        action=ProductivityAction.MCP_EXECUTE,
        target_kind=TargetKind.MCP_SERVER,
        target_value="secret-internal-host",
    )
    rep = repr(request)
    assert "secret-internal-host" not in rep
    assert "mcp.secret" not in rep


def test_decision_and_reason_are_fixed_enums() -> None:
    assert set(PermissionDecision) == {PermissionDecision.ALLOW, PermissionDecision.DENY}
    assert PermissionReason.OK.value == "ok"
    assert PermissionReason.UNDECLARED_TOOL.value == "undeclared_tool"
