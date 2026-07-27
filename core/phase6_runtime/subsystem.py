"""Production Phase6Subsystem facade and state machine for HIKARI command-center path."""

from __future__ import annotations

import math
import secrets
import time
from typing import Any, Callable, Mapping, Optional, Tuple

from core.action_policy import ActorContext, Actor
from core.phase6_runtime.config import Phase6SubsystemConfig
from core.phase6_transport import (
    Phase6ErrorCode,
    build_agent_run_frame,
    build_encrypted_sync_frame,
    build_error_frame,
    build_home_assistant_proposal_frame,
    build_integration_status_frame,
    build_model_eval_frame,
    build_remote_worker_frame,
    build_repo_intel_frame,
    build_skill_evolution_frame,
    build_time_sense_frame,
)
from core.phase6_adapters.home_assistant import (
    HomeAssistantActionProposal,
    HomeAssistantConfirmation,
    HomeAssistantEntityRef,
    HomeAssistantServiceRef,
    HomeAssistantAdapterOutcome,
    HomeAssistantAdapterReason,
    HomeAssistantAdapterResult,
)
from core.phase6_agent.contracts import (
    LoopBudget,
    LoopRequest,
    LoopResult,
    LoopState,
)


def _validate_safe_text(value: Any, max_length: int, field_name: str = "text") -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    if len(value) == 0 or len(value) > max_length:
        raise ValueError(f"{field_name} length out of bounds")
    for char in value:
        code = ord(char)
        if code < 32 and char not in ("\n", "\r", "\t"):
            raise ValueError(f"{field_name} contains control characters")
        if code in (0x7F, 0x200E, 0x200F) or (0x202A <= code <= 0x202E):
            raise ValueError(f"{field_name} contains unsafe format characters")
    return value


def _validate_bounded_int(value: Any, min_val: int, max_val: int, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer")
    if value < min_val or value > max_val:
        raise ValueError(f"{field_name} out of bounds [{min_val}, {max_val}]")
    return value


def _validate_bounded_float(value: Any, min_val: float, max_val: float, field_name: str) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a float")
    val = float(value)
    if not math.isfinite(val):
        raise ValueError(f"{field_name} must be finite")
    if val < min_val or val > max_val:
        raise ValueError(f"{field_name} out of bounds [{min_val}, {max_val}]")
    return val


def _validate_exact_keys(mapping: Any, expected_keys: set[str], field_name: str) -> None:
    if not isinstance(mapping, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    keys = set(mapping.keys())
    if keys != expected_keys:
        raise ValueError(f"{field_name} key set mismatch")


def _validate_safe_sequence(val: Any, max_elements: int, field_name: str) -> list[str]:
    if type(val) not in (list, tuple):
        raise ValueError(f"{field_name} must be a list or tuple")
    if len(val) > max_elements:
        raise ValueError(f"{field_name} count out of bounds")
    seen: set[str] = set()
    res: list[str] = []
    for item in val:
        item_str = _validate_safe_text(item, 128, field_name)
        if item_str in seen:
            raise ValueError(f"{field_name} contains duplicate elements")
        seen.add(item_str)
        res.append(item_str)
    return res


def _query_adapter_readiness(adapter: Any) -> bool:
    """Readiness requires an exact boolean True return value only."""
    if adapter is None:
        return False
    check_fn = getattr(adapter, "is_ready", None) or getattr(adapter, "get_readiness", None)
    if check_fn is None or not callable(check_fn):
        return False
    try:
        res = check_fn()
        return type(res) is bool and res is True
    except Exception:
        return False


class Phase6Subsystem:
    """Fail-closed facade connecting Phase 6 contracts to WebSocket command-center path."""

    def __init__(
        self,
        config: Optional[Phase6SubsystemConfig] = None,
        *,
        ha_adapter: Any | None = None,
        sync_adapter: Any | None = None,
        worker_adapter: Any | None = None,
        skill_staging_adapter: Any | None = None,
        measured_routing_adapter: Any | None = None,
        agent_kernel: Any | None = None,
        time_sense: Any | None = None,
        repo_intel: Any | None = None,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config or Phase6SubsystemConfig()
        self.ha_adapter = ha_adapter
        self.sync_adapter = sync_adapter
        self.worker_adapter = worker_adapter
        self.skill_staging_adapter = skill_staging_adapter
        self.measured_routing_adapter = measured_routing_adapter
        self.agent_kernel = agent_kernel
        self.time_sense = time_sense
        self.repo_intel = repo_intel
        self._clock = clock or time.time
        self._id_factory = id_factory or (lambda: secrets.token_hex(16))

        # Single proposal authority state machine
        self._pending_proposals: dict[str, dict[str, Any]] = {}
        self._terminal_proposals: dict[str, str] = {}  # proposal_id -> terminal state
        self._active_runs: dict[str, dict[str, Any]] = {}
        self._cancelled: bool = False

    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel_all(self) -> None:
        for pid in list(self._pending_proposals.keys()):
            self._record_terminal_proposal(pid, "CANCELLED")
        self._pending_proposals.clear()
        self._active_runs.clear()

    def shutdown(self) -> None:
        self._cancelled = True
        self.cancel_all()

    def _record_terminal_proposal(self, proposal_id: str, state: str) -> None:
        """One bounded helper for recording terminal proposal state."""
        self._pending_proposals.pop(proposal_id, None)
        self._terminal_proposals[proposal_id] = state
        if len(self._terminal_proposals) > 128:
            oldest = next(iter(self._terminal_proposals))
            self._terminal_proposals.pop(oldest, None)

    def _evict_expired_proposals(self) -> None:
        now = self._clock()
        expired: list[str] = []
        for pid, record in list(self._pending_proposals.items()):
            prop = record.get("proposal")
            exp_time = getattr(prop, "expires_at", record.get("expires_at", 0))
            if float(exp_time) <= now:
                expired.append(pid)
        for pid in expired:
            self._record_terminal_proposal(pid, "EXPIRED")

    def get_status_snapshots(self, request_id: str) -> list[Mapping[str, Any]]:
        """Return honest readiness status frames requiring explicit boolean True readiness."""
        if self._cancelled or not self.config.enabled:
            return [
                build_integration_status_frame(request_id, "home_assistant", "Home Assistant", "unavailable", "Disabled by default"),
                build_integration_status_frame(request_id, "encrypted_sync", "Encrypted Sync", "disabled", "Disabled by default"),
                build_integration_status_frame(request_id, "remote_workers", "Remote Workers", "unavailable", "Disabled by default"),
                build_integration_status_frame(request_id, "skill_evolution", "Skill Evolution", "disabled", "Disabled by default"),
                build_integration_status_frame(request_id, "model_evaluation", "Model Evaluation", "disabled", "Disabled by default"),
            ]

        ha_st = "ready" if (self.config.home_assistant_enabled and _query_adapter_readiness(self.ha_adapter)) else ("disabled" if not self.config.home_assistant_enabled else "unavailable")
        sync_st = "ready" if (self.config.encrypted_sync_enabled and _query_adapter_readiness(self.sync_adapter)) else ("disabled" if not self.config.encrypted_sync_enabled else "unavailable")
        worker_st = "ready" if (self.config.remote_workers_enabled and _query_adapter_readiness(self.worker_adapter)) else ("disabled" if not self.config.remote_workers_enabled else "unavailable")
        skill_st = "ready" if (self.config.skill_staging_enabled and _query_adapter_readiness(self.skill_staging_adapter)) else ("disabled" if not self.config.skill_staging_enabled else "unavailable")
        model_st = "ready" if (self.config.measured_routing_enabled and _query_adapter_readiness(self.measured_routing_adapter)) else ("disabled" if not self.config.measured_routing_enabled else "unavailable")

        return [
            build_integration_status_frame(request_id, "home_assistant", "Home Assistant", ha_st, "Entity control plane"),
            build_integration_status_frame(request_id, "encrypted_sync", "Encrypted Sync", sync_st, "Manifest sync planner"),
            build_integration_status_frame(request_id, "remote_workers", "Remote Workers", worker_st, "Isolated job telemetry"),
            build_integration_status_frame(request_id, "skill_evolution", "Skill Evolution", skill_st, "Reviewed skill packages"),
            build_integration_status_frame(request_id, "model_evaluation", "Model Evaluation", model_st, "Local model routing"),
        ]

    # Home Assistant Two-Phase Lifecycle

    def prepare_home_assistant(
        self,
        request_id: str,
        entity_id: str,
        domain: str,
        service: str,
        risk: str,
        effect_summary: str,
        actor_context: ActorContext,
    ) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Prepare a Home Assistant action proposal using the real adapter interface without fallbacks."""
        if self._cancelled or not self.config.enabled or not self.config.home_assistant_enabled or self.ha_adapter is None:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Home Assistant capability is unavailable"

        try:
            _validate_safe_text(entity_id, 128, "entity_id")
            _validate_safe_text(domain, 128, "domain")
            _validate_safe_text(service, 128, "service")
            _validate_safe_text(effect_summary, 512, "effect_summary")
        except ValueError:
            return False, None, Phase6ErrorCode.INVALID_REQUEST, "Invalid parameters"

        if "*" in entity_id or "*" in domain or "*" in service:
            return False, None, Phase6ErrorCode.INVALID_REQUEST, "Wildcards are prohibited in action target scope"

        self._evict_expired_proposals()

        # Reject capacity overflow without silent eviction
        if len(self._pending_proposals) >= self.config.max_pending_proposals:
            return False, None, Phase6ErrorCode.DENIED, "Pending proposal capacity limit reached"

        proposal_id = f"prop_{self._id_factory()[:16]}"
        nonce = f"nonce_{self._id_factory()[:16]}"

        entity_ref = HomeAssistantEntityRef(domain=domain, entity_id=entity_id)
        service_ref = HomeAssistantServiceRef(domain=domain, service=service)

        # Call real adapter prepare signature
        prepare_fn = getattr(self.ha_adapter, "prepare", None)
        if prepare_fn is None or not callable(prepare_fn):
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Home Assistant adapter prepare is unavailable"

        try:
            res = prepare_fn(
                proposal_id=proposal_id,
                entity_ref=entity_ref,
                service_ref=service_ref,
                service_data={},
                actor_context=actor_context,
                nonce=nonce,
            )
        except Exception:
            return False, None, Phase6ErrorCode.DENIED, "Home Assistant prepare action failed"

        # Require exact HomeAssistantAdapterResult and HomeAssistantActionProposal type
        if not isinstance(res, HomeAssistantAdapterResult):
            return False, None, Phase6ErrorCode.DENIED, "Invalid adapter result type"

        if res.outcome not in (HomeAssistantAdapterOutcome.ALLOW, HomeAssistantAdapterOutcome.REQUIRE_CONFIRMATION):
            return False, None, Phase6ErrorCode.DENIED, "Action denied by policy or audit"

        proposal_obj = res.proposal
        if not isinstance(proposal_obj, HomeAssistantActionProposal):
            return False, None, Phase6ErrorCode.DENIED, "Adapter did not return an exact HomeAssistantActionProposal"

        # Validate proposal attributes match request parameters
        if (
            proposal_obj.proposal_id != proposal_id
            or proposal_obj.nonce != nonce
            or proposal_obj.entity_ref.entity_id != entity_id
            or proposal_obj.entity_ref.domain != domain
            or proposal_obj.service_ref.domain != domain
            or proposal_obj.service_ref.service != service
            or proposal_obj.expires_at <= self._clock()
        ):
            return False, None, Phase6ErrorCode.DENIED, "Proposal attributes mismatch or expired"

        self._pending_proposals[proposal_id] = {
            "proposal": proposal_obj,
            "actor_id": actor_context.actor_id,
            "session_id": actor_context.session_id,
            "state": "PENDING",
        }

        try:
            frame = build_home_assistant_proposal_frame(
                request_id=request_id,
                proposal_id=proposal_id,
                entity_id=entity_id,
                domain=domain,
                service=service,
                risk=risk,
                effect_summary=effect_summary,
                expires_at=proposal_obj.expires_at,
                nonce=nonce,
            )
            return True, frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            self._pending_proposals.pop(proposal_id, None)
            return False, None, Phase6ErrorCode.INVALID_REQUEST, "Failed to build proposal frame"

    def confirm_home_assistant(
        self,
        request_id: str,
        proposal_id: str,
        nonce: str,
        actor_context: ActorContext,
    ) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Confirm and execute a prepared proposal using atomic single-use state transition."""
        if self._cancelled or not self.config.enabled or not self.config.home_assistant_enabled or self.ha_adapter is None:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Home Assistant capability is unavailable"

        # Check proposal state before popping
        proposal_record = self._pending_proposals.get(proposal_id)
        if not proposal_record or proposal_record.get("state") != "PENDING":
            return False, None, Phase6ErrorCode.STALE_REQUEST, "That proposal or nonce is no longer active"

        stored_actor_id = proposal_record.get("actor_id")
        stored_session_id = proposal_record.get("session_id")
        if stored_actor_id != actor_context.actor_id or stored_session_id != actor_context.session_id:
            return False, None, Phase6ErrorCode.UNAUTHORIZED, "Confirmation actor or session does not match preparing actor"

        typed_proposal = proposal_record["proposal"]
        if typed_proposal.nonce != nonce:
            return False, None, Phase6ErrorCode.STALE_REQUEST, "Nonce mismatch"

        if self._clock() > typed_proposal.expires_at:
            self._record_terminal_proposal(proposal_id, "EXPIRED")
            return False, None, Phase6ErrorCode.EXPIRED, "Proposal has expired"

        # Atomic state transition PENDING -> EXECUTING
        self._pending_proposals.pop(proposal_id, None)
        self._terminal_proposals[proposal_id] = "EXECUTING"

        confirm_fn = getattr(self.ha_adapter, "confirm_and_execute", None)
        if confirm_fn is None or not callable(confirm_fn):
            self._record_terminal_proposal(proposal_id, "FAILED")
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Home Assistant adapter execution is unavailable"

        confirmation = HomeAssistantConfirmation(
            proposal_id=proposal_id,
            nonce=nonce,
            confirmed_by_actor_id=actor_context.actor_id,
            confirmed_at=self._clock(),
        )

        try:
            res = confirm_fn(
                proposal=typed_proposal,
                confirmation=confirmation,
                actor_context=actor_context,
            )

            if not isinstance(res, HomeAssistantAdapterResult) or res.outcome != HomeAssistantAdapterOutcome.ALLOW:
                self._record_terminal_proposal(proposal_id, "DENIED")
                return False, None, Phase6ErrorCode.DENIED, "Action execution was denied or failed"

            self._record_terminal_proposal(proposal_id, "SUCCEEDED")

            # Content-free safe success message
            status_frame = build_integration_status_frame(
                request_id=request_id,
                integration_id="home_assistant",
                name="Home Assistant",
                status="ready",
                details_summary="Action executed successfully",
            )
            return True, status_frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            self._record_terminal_proposal(proposal_id, "FAILED")
            return False, None, Phase6ErrorCode.DENIED, "Home Assistant action execution failed"

    def cancel_proposal(self, proposal_id: str, actor_context: ActorContext) -> bool:
        """Cancel an active proposal owned by actor_context and session."""
        record = self._pending_proposals.get(proposal_id)
        if record and record.get("actor_id") == actor_context.actor_id and record.get("session_id") == actor_context.session_id:
            self._record_terminal_proposal(proposal_id, "CANCELLED")
            return True
        return False

    def invalidate_session_proposals(self, session_id: str) -> None:
        """Invalidate all proposals owned by session_id upon disconnect."""
        to_delete = [pid for pid, rec in self._pending_proposals.items() if rec.get("session_id") == session_id]
        for pid in to_delete:
            self._record_terminal_proposal(pid, "CANCELLED")

    # Bounded Agent Run Lifecycle

    def prepare_agent_run(
        self,
        request_id: str,
        run_id: str,
        task_summary: str,
        budget_limit: int,
        actor_context: ActorContext,
    ) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Preview a bounded agent run request with strict budget/text validation."""
        if self._cancelled or not self.config.enabled or self.agent_kernel is None:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Bounded agent execution is unavailable"

        try:
            _validate_bounded_int(budget_limit, 1, 100, "budget_limit")
            safe_summary = _validate_safe_text(task_summary, 512, "task_summary")
        except ValueError:
            return False, None, Phase6ErrorCode.INVALID_REQUEST, "Invalid budget limit or task summary"

        if len(self._active_runs) >= self.config.max_pending_runs:
            return False, None, Phase6ErrorCode.DENIED, "Agent run capacity limit reached"

        nonce = f"nonce_{self._id_factory()[:16]}"
        self._active_runs[run_id] = {
            "run_id": run_id,
            "request_id": request_id,
            "actor_id": actor_context.actor_id,
            "session_id": actor_context.session_id,
            "state": "preview",
            "step_count": 0,
            "action_count": 0,
            "budget_limit": budget_limit,
            "safe_summary": safe_summary,
            "nonce": nonce,
        }

        try:
            frame = build_agent_run_frame(
                request_id=request_id,
                run_id=run_id,
                state="preview",
                step_count=0,
                action_count=0,
                budget_limit=budget_limit,
                safe_summary=safe_summary,
            )
            return True, frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            return False, None, Phase6ErrorCode.INVALID_REQUEST, "Invalid agent run arguments"

    def start_agent_run(
        self,
        request_id: str,
        run_id: str,
        nonce: str,
        actor_context: ActorContext,
    ) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Start an agent run by executing the real BoundedAgentLoop kernel synchronously."""
        if self._cancelled or not self.config.enabled or self.agent_kernel is None:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Bounded agent execution is unavailable"

        run = self._active_runs.get(run_id)
        if not run or run.get("nonce") != nonce or run.get("state") != "preview":
            return False, None, Phase6ErrorCode.STALE_REQUEST, "Agent run is not in a startable state"

        if run.get("actor_id") != actor_context.actor_id or run.get("session_id") != actor_context.session_id:
            return False, None, Phase6ErrorCode.UNAUTHORIZED, "Actor or session mismatch for agent run"

        run_fn = getattr(self.agent_kernel, "run", None)
        if run_fn is None or not callable(run_fn):
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Kernel run interface unavailable"

        # Use server-configured allowed tools binding (never hardcoded, default ())
        budget = LoopBudget(
            max_total_steps=min(run["budget_limit"], 64),
            max_tool_actions=min(run["budget_limit"], 32),
            max_elapsed_ms=30000,
            max_event_history=64,
            max_consecutive_failures=3,
        )
        loop_request = LoopRequest(
            request_id=request_id,
            actor_id=actor_context.actor_id,
            session_id=actor_context.session_id,
            goal_summary=run["safe_summary"],
            declared_tools=self.config.allowed_agent_tools,
            budget=budget,
            source="phase6",
        )

        try:
            result = run_fn(loop_request)
            if not isinstance(result, LoopResult):
                run["state"] = "failed"
                return False, None, Phase6ErrorCode.INTERNAL_ERROR, "Invalid kernel execution result"

            # Strict validation of LoopResult step and action bounds
            if (
                type(result.steps_used) is not int
                or result.steps_used < 0
                or result.steps_used > budget.max_total_steps
                or type(result.actions_used) is not int
                or result.actions_used < 0
                or result.actions_used > budget.max_tool_actions
            ):
                run["state"] = "failed"
                return False, None, Phase6ErrorCode.INTERNAL_ERROR, "Kernel result bounds violation"

            state_map = {
                LoopState.SUCCEEDED: "succeeded",
                LoopState.DENIED: "denied",
                LoopState.CANCELLED: "cancelled",
                LoopState.EXHAUSTED: "exhausted",
                LoopState.FAILED: "failed",
            }
            final_state = state_map.get(result.state)
            if final_state is None:
                run["state"] = "failed"
                return False, None, Phase6ErrorCode.INTERNAL_ERROR, "Unsupported kernel state"

            run["state"] = final_state
            run["step_count"] = result.steps_used
            run["action_count"] = result.actions_used

            # Safe kernel summary validation without truncation
            final_summary = result.final_summary
            try:
                safe_final = _validate_safe_text(final_summary, 512, "final_summary")
            except Exception:
                safe_final = "Agent run completed; summary unavailable"

            frame = build_agent_run_frame(
                request_id=request_id,
                run_id=run_id,
                state=final_state,
                step_count=result.steps_used,
                action_count=result.actions_used,
                budget_limit=run["budget_limit"],
                safe_summary=safe_final,
            )
            return True, frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            run["state"] = "failed"
            return False, None, Phase6ErrorCode.INTERNAL_ERROR, "Kernel execution failed"

    def cancel_agent_run(self, run_id: str, actor_context: ActorContext) -> bool:
        """Cancel an agent run. Synchronously completed runs cannot be cancelled after execution."""
        run = self._active_runs.get(run_id)
        if run and run.get("actor_id") == actor_context.actor_id and run.get("session_id") == actor_context.session_id:
            if run.get("state") == "preview":
                run["state"] = "cancelled"
                return True
        return False

    def get_agent_run_status(
        self,
        request_id: str,
        run_id: str,
        actor_context: ActorContext,
    ) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Fetch status frame for an active or completed run."""
        run = self._active_runs.get(run_id)
        if not run:
            return False, None, Phase6ErrorCode.NOT_FOUND, "Agent run not found"

        if run.get("actor_id") != actor_context.actor_id or run.get("session_id") != actor_context.session_id:
            return False, None, Phase6ErrorCode.UNAUTHORIZED, "Actor mismatch"

        try:
            frame = build_agent_run_frame(
                request_id=request_id,
                run_id=run_id,
                state=run["state"],
                step_count=run["step_count"],
                action_count=run["action_count"],
                budget_limit=run["budget_limit"],
                safe_summary=run["safe_summary"],
            )
            return True, frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            return False, None, Phase6ErrorCode.INTERNAL_ERROR, "Failed to build run status frame"

    # Advisory / Evaluated Status Views via Injected Adapter Queries Only (Exact Key Sets & No Coercion)

    def get_encrypted_sync_status(self, request_id: str) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Return safe encrypted-sync status frame requiring exact key set."""
        if not self.config.enabled or not self.config.encrypted_sync_enabled or self.sync_adapter is None:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Encrypted sync capability is unavailable"

        get_status_fn = getattr(self.sync_adapter, "get_status", None)
        if get_status_fn is None or not callable(get_status_fn):
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Encrypted sync adapter status API unavailable"

        try:
            info = get_status_fn()
            _validate_exact_keys(info, {"enabled", "configured", "status", "conflict_count"}, "sync_info")

            enabled_val = info["enabled"]
            configured_val = info["configured"]
            status_val = info["status"]
            conflict_val = info["conflict_count"]

            if type(enabled_val) is not bool or type(configured_val) is not bool or type(status_val) is not str or type(conflict_val) is not int:
                return False, None, Phase6ErrorCode.UNAVAILABLE, "Sync status field type invalid"

            _validate_safe_text(status_val, 64, "sync_status")
            _validate_bounded_int(conflict_val, 0, 10000, "conflict_count")

            frame = build_encrypted_sync_frame(
                request_id=request_id,
                enabled=enabled_val,
                configured=configured_val,
                status=status_val,
                conflict_count=conflict_val,
            )
            return True, frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Failed to query sync status"

    def get_remote_worker_status(self, request_id: str, job_id: str, worker_id: str) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Return safe remote-worker status frame requiring exact key set."""
        if not self.config.enabled or not self.config.remote_workers_enabled or self.worker_adapter is None:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Remote worker capability is unavailable"

        get_job_fn = getattr(self.worker_adapter, "get_job_status", None)
        if get_job_fn is None or not callable(get_job_fn):
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Remote worker adapter status API unavailable"

        try:
            info = get_job_fn(job_id=job_id, worker_id=worker_id)
            _validate_exact_keys(info, {"state", "has_evidence", "quarantined"}, "worker_info")

            state_val = info["state"]
            evidence_val = info["has_evidence"]
            quarantined_val = info["quarantined"]

            if type(state_val) is not str or type(evidence_val) is not bool or type(quarantined_val) is not bool:
                return False, None, Phase6ErrorCode.UNAVAILABLE, "Worker status field type invalid"

            _validate_safe_text(state_val, 64, "worker_state")

            frame = build_remote_worker_frame(
                request_id=request_id,
                job_id=job_id,
                worker_id=worker_id,
                state=state_val,
                has_evidence=evidence_val,
                quarantined=quarantined_val,
            )
            return True, frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Failed to query worker status"

    def get_skill_evolution_status(self, request_id: str, package_id: str) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Return safe skill-evolution status frame requiring exact key set and sequence type."""
        if not self.config.enabled or not self.config.skill_staging_enabled or self.skill_staging_adapter is None:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Skill evolution capability is unavailable"

        get_pkg_fn = getattr(self.skill_staging_adapter, "get_package_status", None)
        if get_pkg_fn is None or not callable(get_pkg_fn):
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Skill staging adapter status API unavailable"

        try:
            info = get_pkg_fn(package_id=package_id)
            _validate_exact_keys(info, {"version", "state", "permissions_summary", "rollback_ready"}, "skill_info")

            version_val = info["version"]
            state_val = info["state"]
            perms_val = info["permissions_summary"]
            rollback_val = info["rollback_ready"]

            if type(version_val) is not str or type(state_val) is not str or type(rollback_val) is not bool:
                return False, None, Phase6ErrorCode.UNAVAILABLE, "Skill status field type invalid"

            _validate_safe_text(version_val, 32, "version")
            _validate_safe_text(state_val, 64, "skill_state")
            perms_list = _validate_safe_sequence(perms_val, 32, "permissions_summary")

            frame = build_skill_evolution_frame(
                request_id=request_id,
                package_id=package_id,
                version=version_val,
                state=state_val,
                permissions_summary=perms_list,
                rollback_ready=rollback_val,
            )
            return True, frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Failed to query skill package status"

    def get_model_eval_status(self, request_id: str, candidate_id: str) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Return safe model-evaluation status frame requiring exact key set and strict numeric bounds."""
        if not self.config.enabled or not self.config.measured_routing_enabled or self.measured_routing_adapter is None:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Model evaluation capability is unavailable"

        get_cand_fn = getattr(self.measured_routing_adapter, "get_candidate_evaluation", None)
        if get_cand_fn is None or not callable(get_cand_fn):
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Model evaluation adapter status API unavailable"

        try:
            info = get_cand_fn(candidate_id=candidate_id)
            _validate_exact_keys(info, {"privacy_class", "capabilities", "quality_score", "safety_score", "latency_ms", "recommendation"}, "model_info")

            privacy_val = info["privacy_class"]
            caps_val = info["capabilities"]
            quality_val = info["quality_score"]
            safety_val = info["safety_score"]
            latency_val = info["latency_ms"]
            rec_val = info["recommendation"]

            if type(privacy_val) is not str or type(rec_val) is not str:
                return False, None, Phase6ErrorCode.UNAVAILABLE, "Model evaluation string field invalid"

            q_score = _validate_bounded_float(quality_val, 0.0, 1.0, "quality_score")
            s_score = _validate_bounded_float(safety_val, 0.0, 1.0, "safety_score")
            l_ms = _validate_bounded_float(latency_val, 0.0, 86400000.0, "latency_ms")

            _validate_safe_text(privacy_val, 64, "privacy_class")
            _validate_safe_text(rec_val, 64, "recommendation")
            caps_list = _validate_safe_sequence(caps_val, 32, "capabilities")

            frame = build_model_eval_frame(
                request_id=request_id,
                candidate_id=candidate_id,
                privacy_class=privacy_val,
                capabilities=caps_list,
                quality_score=q_score,
                safety_score=s_score,
                latency_ms=l_ms,
                recommendation=rec_val,
            )
            return True, frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Failed to query model candidate evaluation"

    # Time Sense & Repo Intel Read Snapshots (Honest Injected Evidence Only)

    def get_time_sense_snapshot(self, request_id: str) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Return read-only Time Sense status frame requiring exact key set and bounded numeric ranges."""
        if not self.config.enabled or not self.config.time_sense_enabled or self.time_sense is None:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Time Sense capability is unavailable"

        get_snap_fn = getattr(self.time_sense, "get_snapshot", None)
        if get_snap_fn is None or not callable(get_snap_fn):
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Time Sense snapshot API unavailable"

        try:
            info = get_snap_fn()
            _validate_exact_keys(info, {"task_age_seconds", "heartbeat_status", "stuck_reason", "next_allowed_checkin", "suppression_state", "background_status"}, "time_sense_info")

            task_age = _validate_bounded_float(info["task_age_seconds"], 0.0, 86400000.0, "task_age_seconds")
            next_chk = _validate_bounded_float(info["next_allowed_checkin"], 0.0, 4102444800.0, "next_allowed_checkin")

            hb_st = _validate_safe_text(info["heartbeat_status"], 64, "heartbeat_status")
            stuck_r = info["stuck_reason"]
            if stuck_r is not None:
                stuck_r = _validate_safe_text(stuck_r, 128, "stuck_reason")
            suppr_st = _validate_safe_text(info["suppression_state"], 64, "suppression_state")
            bg_st = _validate_safe_text(info["background_status"], 64, "background_status")

            frame = build_time_sense_frame(
                request_id=request_id,
                task_age_seconds=task_age,
                heartbeat_status=hb_st,
                stuck_reason=stuck_r,
                next_allowed_checkin=next_chk,
                suppression_state=suppr_st,
                background_status=bg_st,
            )
            return True, frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Failed to query Time Sense snapshot"

    def get_repo_intel_snapshot(self, request_id: str, query_summary: str) -> Tuple[bool, Optional[Mapping[str, Any]], Phase6ErrorCode, str]:
        """Return read-only repository intelligence status frame enforcing hit_count >= len(results)."""
        if not self.config.enabled or not self.config.repo_intel_enabled or self.repo_intel is None:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Repository intelligence capability is unavailable"

        try:
            safe_q = _validate_safe_text(query_summary, 128, "query_summary")
        except ValueError:
            return False, None, Phase6ErrorCode.INVALID_REQUEST, "Invalid repository query text"

        query_fn = getattr(self.repo_intel, "query_intelligence", None)
        if query_fn is None or not callable(query_fn):
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Repository intelligence query API unavailable"

        try:
            info = query_fn(query_summary=safe_q)
            _validate_exact_keys(info, {"scan_state", "hit_count", "results"}, "repo_intel_info")

            scan_st = _validate_safe_text(info["scan_state"], 64, "scan_state")
            hit_cnt = _validate_bounded_int(info["hit_count"], 0, 100000, "hit_count")
            res_list = _validate_safe_sequence(info["results"], 100, "results")

            # Consistency check: total matches hit_count >= returned result list length
            if hit_cnt < len(res_list):
                return False, None, Phase6ErrorCode.UNAVAILABLE, "Inconsistent repository intelligence hit count"

            frame = build_repo_intel_frame(
                request_id=request_id,
                scan_state=scan_st,
                query_summary=safe_q,
                hit_count=hit_cnt,
                results=res_list,
            )
            return True, frame, Phase6ErrorCode.INVALID_REQUEST, "ok"
        except Exception:
            return False, None, Phase6ErrorCode.UNAVAILABLE, "Failed to query repository intelligence"

    def __repr__(self) -> str:
        return "Phase6Subsystem()"


def create_phase6_subsystem(
    config: Optional[Phase6SubsystemConfig] = None,
    **kwargs: Any,
) -> Phase6Subsystem:
    """Helper factory for Phase6Subsystem."""
    return Phase6Subsystem(config=config, **kwargs)
