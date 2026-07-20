"""Pure, fail-closed Phase 3 third-party permission kernel.

This module is intentionally side-effect free. It performs no I/O, persistence,
environment reads, network access, subprocess execution, or external SDK imports.
It only evaluates declared, immutable permission manifests against a request and
returns a fixed decision enum with a fixed reason code.

The kernel governs two tool kinds only:

- ``mcp``  -> allowed action is ``ProductivityAction.MCP_EXECUTE``
- ``skill`` -> allowed action is ``ProductivityAction.SKILL_EXECUTE``

Every manifest declares exact allowed target values. There is no wildcard,
prefix, substring, implicit-default, normalization, truncation, or case-folding
behavior. Any malformed, empty, duplicate, oversized, control-character,
Unicode-confusable, or overlong declaration fails closed at construction time,
so an invalid manifest can never reach the evaluator.

Privacy: the evaluator never returns or logs raw target values, actor/session
IDs, prompts, payloads, secrets, provider details, or exception text. Decision
and reason codes are fixed enumerations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import FrozenSet, Mapping, Optional, Tuple

from core.productivity.contracts import ProductivityAction, TargetKind


class ToolKind(StrEnum):
    """The only two supported third-party tool kinds."""

    MCP = "mcp"
    SKILL = "skill"


class PermissionDecision(StrEnum):
    """Fixed authorization outcome. Never carries payload or identifiers."""

    ALLOW = "allow"
    DENY = "deny"


class PermissionReason(StrEnum):
    """Fixed, non-attributable reason codes.

    Reason codes never embed raw input, identifiers, or exception text.
    """

    OK = "ok"
    UNDECLARED_TOOL = "undeclared_tool"
    WRONG_KIND = "wrong_kind"
    WRONG_ACTION = "wrong_action"
    TARGET_KIND_MISMATCH = "target_kind_mismatch"
    TARGET_VALUE_MISMATCH = "target_value_mismatch"
    MALFORMED_MANIFEST = "malformed_manifest"
    EMPTY_MANIFEST = "empty_manifest"


# Canonical opaque-ID contract (matches core.productivity.contracts._IDENTIFIER_RE).
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")

# Maximum number of declared targets per tool (bounded to prevent oversized manifests).
_MAX_TARGETS_PER_TOOL = 256

# Maximum length of a declared target value (bounded to prevent overlong declarations).
_MAX_TARGET_VALUE_LENGTH = 4096

# Allowed action per tool kind.
_KIND_ACTION: Mapping[ToolKind, ProductivityAction] = {
    ToolKind.MCP: ProductivityAction.MCP_EXECUTE,
    ToolKind.SKILL: ProductivityAction.SKILL_EXECUTE,
}

# Target kind associated with each tool kind.
_KIND_TARGET_KIND: Mapping[ToolKind, TargetKind] = {
    ToolKind.MCP: TargetKind.MCP_SERVER,
    ToolKind.SKILL: TargetKind.SKILL,
}


def _has_control_chars(value: str) -> bool:
    """Return True if ``value`` contains ASCII control characters (incl. DEL)."""
    for char in value:
        code = ord(char)
        if code < 32 or code == 127:
            return True
    return False


def _is_confusable(value: str) -> bool:
    """Reject characters outside printable ASCII to avoid Unicode-confusable abuse.

    The canonical opaque-ID contract already restricts tool IDs to ``[a-z0-9_.-]``,
    but declared target values are free-form. To keep the kernel fail-closed against
    Unicode-confusable spoofing, target values must be printable ASCII only.
    """
    for char in value:
        code = ord(char)
        if code < 32 or code > 126:
            return True
    return False


def _validate_tool_id(tool_id: object) -> str:
    """Return a validated lowercase opaque tool ID or raise ValueError (fail-closed)."""
    if not isinstance(tool_id, str) or not _IDENTIFIER_RE.fullmatch(tool_id):
        raise ValueError("invalid tool id")
    return tool_id


def _validate_target_value(value: object) -> str:
    """Return a validated exact target value or raise ValueError (fail-closed)."""
    if not isinstance(value, str):
        raise ValueError("invalid target value")
    if not value:
        raise ValueError("empty target value")
    if len(value) > _MAX_TARGET_VALUE_LENGTH:
        raise ValueError("oversized target value")
    if "\x00" in value:
        raise ValueError("nul in target value")
    if _has_control_chars(value):
        raise ValueError("control char in target value")
    if _is_confusable(value):
        raise ValueError("non-ascii target value")
    if any(marker in value for marker in ("*", "?", "[", "]")):
        raise ValueError("wildcard target value")
    return value


@dataclass(frozen=True)
class ToolPermission:
    """Immutable declared permission for a single tool.

    ``targets`` is a frozen set of exact allowed target values. No wildcard,
    prefix, or substring semantics exist. Equality and hashing are structural.
    """

    tool_id: str
    kind: ToolKind
    targets: FrozenSet[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_id", _validate_tool_id(self.tool_id))
        if not isinstance(self.kind, ToolKind):
            raise ValueError("invalid tool kind")
        if not isinstance(self.targets, frozenset):
            raise ValueError("targets must be a frozenset")
        if not self.targets:
            raise ValueError("empty targets")
        if len(self.targets) > _MAX_TARGETS_PER_TOOL:
            raise ValueError("too many targets")
        validated: set[str] = set()
        for target in self.targets:
            validated.add(_validate_target_value(target))
        object.__setattr__(self, "targets", frozenset(validated))

    def __repr__(self) -> str:
        # Privacy: never expose target values or tool_id in repr beyond kind/count.
        return (
            f"ToolPermission(kind={self.kind.value!r}, "
            f"target_count={len(self.targets)})"
        )


@dataclass(frozen=True)
class PermissionManifest:
    """Immutable, deep-frozen collection of declared tool permissions.

    Construction fails closed on any malformed, duplicate, empty, oversized,
    control-character, Unicode-confusable, or overlong declaration. Once built,
    the manifest is immutable and safe to share across threads/sessions.
    """

    tools: Tuple[ToolPermission, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.tools, tuple):
            raise ValueError("tools must be a tuple")
        if not self.tools:
            raise ValueError("empty manifest")
        if len(self.tools) > _MAX_TARGETS_PER_TOOL:
            raise ValueError("too many tools")
        seen_ids: set[str] = set()
        for tool in self.tools:
            if not isinstance(tool, ToolPermission):
                raise ValueError("tools must contain ToolPermission instances")
            if tool.tool_id in seen_ids:
                raise ValueError("duplicate tool id")
            seen_ids.add(tool.tool_id)

    def __repr__(self) -> str:
        return f"PermissionManifest(tool_count={len(self.tools)})"

    def lookup(self, tool_id: str) -> Optional[ToolPermission]:
        """Return the declared ``ToolPermission`` for ``tool_id`` or ``None``.

        Lookup is exact-match only; no normalization or case folding is performed.
        """
        for tool in self.tools:
            if tool.tool_id == tool_id:
                return tool
        return None


@dataclass(frozen=True)
class PermissionRequest:
    """Immutable authorization request.

    Contains only the structural fields needed for exact-match evaluation. No
    actor/session IDs, prompts, payloads, or secrets are carried here.
    """

    tool_id: str
    kind: ToolKind
    action: ProductivityAction
    target_kind: TargetKind
    target_value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_id", _validate_tool_id(self.tool_id))
        if not isinstance(self.kind, ToolKind):
            raise ValueError("invalid tool kind")
        if not isinstance(self.action, ProductivityAction):
            raise ValueError("invalid action")
        if not isinstance(self.target_kind, TargetKind):
            raise ValueError("invalid target kind")
        object.__setattr__(self, "target_value", _validate_target_value(self.target_value))

    def __repr__(self) -> str:
        return (
            f"PermissionRequest(kind={self.kind.value!r}, "
            f"action={self.action.value!r}, "
            f"target_kind={self.target_kind.value!r})"
        )


def evaluate(manifest: PermissionManifest, request: PermissionRequest) -> Tuple[PermissionDecision, PermissionReason]:
    """Pure, fail-closed authorization evaluator.

    Returns a fixed ``(PermissionDecision, PermissionReason)`` tuple. No I/O, no
    logging of raw values, no exception text leakage. Every path is deterministic.

    Denial precedence (all fail closed):
      1. undeclared tool id            -> DENY / UNDECLARED_TOOL
      2. declared kind != request kind -> DENY / WRONG_KIND
      3. action != kind's allowed act. -> DENY / WRONG_ACTION
      4. target kind mismatch          -> DENY / TARGET_KIND_MISMATCH
      5. target value not exact member -> DENY / TARGET_VALUE_MISMATCH
    """
    tool = manifest.lookup(request.tool_id)
    if tool is None:
        return PermissionDecision.DENY, PermissionReason.UNDECLARED_TOOL

    if tool.kind is not request.kind:
        return PermissionDecision.DENY, PermissionReason.WRONG_KIND

    allowed_action = _KIND_ACTION.get(request.kind)
    if allowed_action is None or request.action is not allowed_action:
        return PermissionDecision.DENY, PermissionReason.WRONG_ACTION

    expected_target_kind = _KIND_TARGET_KIND.get(request.kind)
    if expected_target_kind is None or request.target_kind is not expected_target_kind:
        return PermissionDecision.DENY, PermissionReason.TARGET_KIND_MISMATCH

    # Exact membership only. No prefix/substring/wildcard/normalization.
    if request.target_value not in tool.targets:
        return PermissionDecision.DENY, PermissionReason.TARGET_VALUE_MISMATCH

    return PermissionDecision.ALLOW, PermissionReason.OK


def build_manifest(declarations: Mapping[str, Mapping[str, object]]) -> PermissionManifest:
    """Construct an immutable ``PermissionManifest`` from raw declarations.

    ``declarations`` maps a tool id to a mapping with keys:
      - ``kind``: ``"mcp"`` or ``"skill"``
      - ``targets``: iterable of exact allowed target values (strings)

    Any malformed input raises ``ValueError``, failing closed. Callers must treat
    a raised exception as "do not grant any permission".
    """
    if not isinstance(declarations, Mapping) or not declarations:
        raise ValueError("empty manifest")

    tools: list[ToolPermission] = []
    for tool_id, spec in declarations.items():
        if not isinstance(spec, Mapping):
            raise ValueError("invalid tool spec")
        if set(spec) != {"kind", "targets"}:
            raise ValueError("invalid tool fields")
        kind_raw = spec.get("kind")
        if kind_raw not in ("mcp", "skill"):
            raise ValueError("invalid tool kind")
        kind = ToolKind(kind_raw)
        targets_raw = spec.get("targets")
        if not isinstance(targets_raw, (list, tuple, set, frozenset)):
            raise ValueError("invalid targets")
        validated_targets = tuple(_validate_target_value(t) for t in targets_raw)
        targets = frozenset(validated_targets)
        if len(targets) != len(validated_targets):
            raise ValueError("duplicate targets")
        tools.append(ToolPermission(tool_id=tool_id, kind=kind, targets=targets))

    return PermissionManifest(tools=tuple(tools))
