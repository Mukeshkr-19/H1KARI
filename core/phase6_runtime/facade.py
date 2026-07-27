"""Fail-closed composition facade for HIKARI Phase 6 foundations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.phase6_agent import BoundedAgentLoop
from core.phase6_developer import (
    GitOperationRequest,
    GitPolicyDecision,
    GitStateSnapshot,
    RepositoryIndex,
    RepositoryPolicy,
    RepositoryQuery,
    RepositoryQueryResult,
    RepositoryRoot,
    SandboxCommandRequest,
    SandboxPolicyDecision,
    evaluate_git_policy,
    evaluate_query,
    evaluate_sandbox_policy,
    scan_repository,
)
from core.phase6_ecosystem.model_evaluation import ModelRoutingEvaluator
from core.phase6_ecosystem.skill_review import SkillEvolutionCoordinator


class Phase6UnavailableError(RuntimeError):
    """Fixed public error for a Phase 6 capability that is not enabled."""

    def __init__(self, feature: str = "unavailable") -> None:
        self.feature = feature
        super().__init__("phase 6 capability unavailable")

    def __repr__(self) -> str:
        return "Phase6UnavailableError()"


@dataclass(frozen=True)
class Phase6FeatureFlags:
    """Explicit opt-ins for capabilities that could cross authority boundaries."""

    bounded_agent_execution: bool = False
    remote_workers: bool = False
    skill_installation: bool = False
    home_assistant_transport: bool = False
    encrypted_sync_transport: bool = False
    model_router_mutation: bool = False

    def __post_init__(self) -> None:
        for value in (
            self.bounded_agent_execution,
            self.remote_workers,
            self.skill_installation,
            self.home_assistant_transport,
            self.encrypted_sync_transport,
            self.model_router_mutation,
        ):
            if type(value) is not bool:
                raise ValueError("phase 6 feature flags must be boolean")


@dataclass(frozen=True)
class Phase6Runtime:
    """Small runtime facade over audited Phase 6 foundation packages."""

    repository_policy: RepositoryPolicy
    flags: Phase6FeatureFlags
    skill_evolution: SkillEvolutionCoordinator
    model_evaluation: ModelRoutingEvaluator

    def scan_repository(
        self,
        root: Path | str,
        *,
        policy: RepositoryPolicy | None = None,
    ) -> RepositoryIndex:
        """Build a bounded read-only index for one explicit repository root."""
        root_path = root if isinstance(root, Path) else Path(root)
        return scan_repository(RepositoryRoot(root_path), policy or self.repository_policy)

    @staticmethod
    def query_repository(
        index: RepositoryIndex,
        query: RepositoryQuery,
    ) -> RepositoryQueryResult:
        """Evaluate a deterministic bounded query over a caller-held index."""
        return evaluate_query(index, query)

    @staticmethod
    def evaluate_git(
        request: GitOperationRequest,
        state: GitStateSnapshot,
    ) -> GitPolicyDecision:
        """Plan Git authority only; never execute Git."""
        return evaluate_git_policy(request, state)

    @staticmethod
    def evaluate_sandbox(request: SandboxCommandRequest) -> SandboxPolicyDecision:
        """Evaluate a sandbox request only; never execute the command."""
        return evaluate_sandbox_policy(request)

    def create_bounded_agent_loop(
        self,
        *,
        planner: Any,
        authorizer: Any,
        auditor: Any,
        executor: Any,
        clock: Any,
        id_factory: Any,
        approval_resolver: Any | None = None,
        cancellation: Any | None = None,
    ) -> BoundedAgentLoop:
        if not self.flags.bounded_agent_execution:
            raise Phase6UnavailableError("bounded_agent_execution")
        return BoundedAgentLoop(
            planner=planner,
            authorizer=authorizer,
            auditor=auditor,
            executor=executor,
            clock=clock,
            id_factory=id_factory,
            approval_resolver=approval_resolver,
            cancellation=cancellation,
        )

    def require_enabled(self, feature: str) -> None:
        """Fail closed for optional transports and mutation surfaces."""
        if feature not in Phase6FeatureFlags.__dataclass_fields__:
            raise Phase6UnavailableError("unknown")
        if not getattr(self.flags, feature):
            raise Phase6UnavailableError(feature)

    def __repr__(self) -> str:
        return "Phase6Runtime()"


def create_phase6_runtime(
    *,
    repository_policy: RepositoryPolicy | None = None,
    flags: Phase6FeatureFlags | None = None,
    skill_evolution: SkillEvolutionCoordinator | None = None,
    model_evaluation: ModelRoutingEvaluator | None = None,
) -> Phase6Runtime:
    """Compose inert Phase 6 services without touching external state."""
    return Phase6Runtime(
        repository_policy=repository_policy or RepositoryPolicy(),
        flags=flags or Phase6FeatureFlags(),
        skill_evolution=skill_evolution or SkillEvolutionCoordinator(),
        model_evaluation=model_evaluation or ModelRoutingEvaluator(),
    )
