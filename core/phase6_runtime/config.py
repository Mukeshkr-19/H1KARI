"""Configuration object for Phase6Subsystem."""

from __future__ import annotations

import math
from dataclasses import dataclass


import re

_TOOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def _validate_bool(name: str, value: bool) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")


def _validate_bounded_int(name: str, value: int, min_val: int, max_val: int) -> None:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    if value < min_val or value > max_val:
        raise ValueError(f"{name} out of bounds [{min_val}, {max_val}]")


def _validate_bounded_float(name: str, value: float, min_val: float, max_val: float) -> None:
    if not isinstance(value, (int, float)) or type(value) is bool:
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < min_val or value > max_val:
        raise ValueError(f"{name} out of bounds [{min_val}, {max_val}]")


def _validate_tool_identifier(tool: str) -> None:
    if type(tool) is not str:
        raise ValueError("allowed_agent_tools items must be strings")
    if len(tool) == 0 or len(tool) > 64:
        raise ValueError("tool identifier length out of bounds")
    if not _TOOL_ID_RE.fullmatch(tool):
        raise ValueError("invalid tool identifier pattern")
    if "*" in tool:
        raise ValueError("wildcards prohibited in tool identifier")
    for char in tool:
        code = ord(char)
        if code < 32 or code == 0x7F or code in (0x200E, 0x200F) or (0x202A <= code <= 0x202E):
            raise ValueError("tool identifier contains control or format characters")


@dataclass(frozen=True)
class Phase6SubsystemConfig:
    """Strict configuration for Phase6Subsystem. Disabled by default."""

    enabled: bool = False
    home_assistant_enabled: bool = False
    encrypted_sync_enabled: bool = False
    remote_workers_enabled: bool = False
    skill_staging_enabled: bool = False
    measured_routing_enabled: bool = False
    time_sense_enabled: bool = False
    repo_intel_enabled: bool = False
    max_pending_proposals: int = 32
    max_pending_runs: int = 16
    proposal_ttl_seconds: float = 300.0
    allowed_agent_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_bool("enabled", self.enabled)
        _validate_bool("home_assistant_enabled", self.home_assistant_enabled)
        _validate_bool("encrypted_sync_enabled", self.encrypted_sync_enabled)
        _validate_bool("remote_workers_enabled", self.remote_workers_enabled)
        _validate_bool("skill_staging_enabled", self.skill_staging_enabled)
        _validate_bool("measured_routing_enabled", self.measured_routing_enabled)
        _validate_bool("time_sense_enabled", self.time_sense_enabled)
        _validate_bool("repo_intel_enabled", self.repo_intel_enabled)

        if type(self.allowed_agent_tools) is not tuple:
            raise ValueError("allowed_agent_tools must be a tuple")
        if len(self.allowed_agent_tools) > 32:
            raise ValueError("allowed_agent_tools exceeds maximum bound of 32")
        seen: set[str] = set()
        for tool in self.allowed_agent_tools:
            _validate_tool_identifier(tool)
            if tool in seen:
                raise ValueError("allowed_agent_tools contains duplicate tool entries")
            seen.add(tool)

        _validate_bounded_int("max_pending_proposals", self.max_pending_proposals, 1, 256)
        _validate_bounded_int("max_pending_runs", self.max_pending_runs, 1, 128)
        _validate_bounded_float("proposal_ttl_seconds", float(self.proposal_ttl_seconds), 1.0, 3600.0)

    def __repr__(self) -> str:
        return f"Phase6SubsystemConfig(enabled={self.enabled})"
