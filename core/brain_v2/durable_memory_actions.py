"""Pure durable-memory action plans and adapter Protocols (no DB writes).

Mira wires EpisodeStore / MemoryRepairGate adapters. This module only plans
and orchestrates through injected interfaces.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal, Optional, Protocol, runtime_checkable

from core.brain_v2.durable_memory_intent import (
    DurableMemoryIntent,
    MAX_BODY_LEN,
    is_canonical_correlation_id,
    normalize_memory_body,
)

OutcomeStatus = Literal[
    "accepted",
    "pending_review",
    "pending_conflict",
    "rejected",
    "not_saved",
    "corrected",
    "forgotten",
    "unavailable",
]

PlanKind = Literal[
    "noop",
    "save_atomic",
    "suggest_review",
    "correct_supersede",
    "forget_retire",
    "request_exact_fact",
    "reject",
]

ValidationReason = Literal[
    "ok",
    "empty_body",
    "malformed_body",
    "task_not_fact",
    "sensitivity_blocked",
    "third_party_ambiguity",
    "conflict",
    "unresolved_target",
    "unsupported_action",
    "adapter_unavailable",
    "readback_failed",
    "idempotent_replay",
    "save_outcome_unknown",
    "correction_outcome_unknown",
    "forget_outcome_unknown",
]


_TASK_SHAPE = re.compile(
    r"^\s*(?:please\s+)?(?:remind\s+me|schedule|set\s+a\s+timer|create\s+a\s+task|"
    r"add\s+to\s+(?:my\s+)?(?:todo|list)|call|email|text|send)\b",
    re.I,
)
_SENSITIVE = re.compile(
    r"\b(?:password|passphrase|private\s+key|ssn|social\s+security|"
    r"credit\s+card|cvv|bank\s+account|routing\s+number)\b",
    re.I,
)
_THIRD_PARTY_AMB = re.compile(
    r"\b(?:they|them|their|he|she|him|her)\b.+\b(?:live|work|born|password|ssn)\b|"
    r"\b(?:my\s+)?(?:friend|colleague|coworker|neighbor|boss|client)\s+"
    r"(?:lives|works|was\s+born)\b",
    re.I,
)


@dataclass(frozen=True)
class DurableActionPlan:
    """Pure plan describing the next durable-memory step (no side effects)."""

    kind: PlanKind
    intent_action: str
    body: str
    idempotency_key: str
    validation: ValidationReason
    requires_readback: bool = False
    preserve_audit: bool = True
    hard_delete: bool = False

    def __repr__(self) -> str:
        return (
            "DurableActionPlan("
            f"kind={self.kind!r}, intent_action={self.intent_action!r}, "
            f"validation={self.validation!r}, requires_readback={self.requires_readback!r}, "
            f"preserve_audit={self.preserve_audit!r}, hard_delete={self.hard_delete!r}, "
            f"body_len={len(self.body)}, idempotency_key={self.idempotency_key!r})"
        )


@dataclass(frozen=True)
class DurableActionOutcome:
    """Verified outcome from executing a plan via injected adapters."""

    status: OutcomeStatus
    reason: ValidationReason
    memory_id: str = ""
    readback_ok: bool = False
    recallable: bool = False
    superseded_id: str = ""
    retired_id: str = ""
    idempotency_key: str = ""

    def __repr__(self) -> str:
        return (
            "DurableActionOutcome("
            f"status={self.status!r}, reason={self.reason!r}, "
            f"readback_ok={self.readback_ok!r}, recallable={self.recallable!r}, "
            f"has_memory_id={bool(self.memory_id)}, "
            f"has_superseded_id={bool(self.superseded_id)}, "
            f"has_retired_id={bool(self.retired_id)}, "
            f"idempotency_key={self.idempotency_key!r})"
        )


@dataclass(frozen=True)
class AtomicWriteResult:
    memory_id: str
    created: bool
    conflict: bool = False
    pending_review: bool = False


@dataclass(frozen=True)
class ReadbackResult:
    found: bool
    active: bool
    statement_matches: bool


@dataclass(frozen=True)
class SupersedeResult:
    old_memory_id: str
    new_memory_id: str
    preserved_audit: bool


@dataclass(frozen=True)
class RetireResult:
    memory_id: str
    retired: bool
    preserved: bool


@runtime_checkable
class CandidateCreateAccept(Protocol):
    def create_and_accept_candidate(
        self, *, body: str, idempotency_key: str, actor_id: str, session_id: str
    ) -> AtomicWriteResult: ...


@runtime_checkable
class AtomicWritePort(Protocol):
    def atomic_write_active(
        self, *, body: str, idempotency_key: str, actor_id: str, session_id: str
    ) -> AtomicWriteResult: ...


@runtime_checkable
class ReadbackVerifyPort(Protocol):
    def verify_readback(self, *, memory_id: str, expected_body: str) -> ReadbackResult: ...


@runtime_checkable
class ExactActiveRetrievalPort(Protocol):
    def find_exact_active(self, *, body: str) -> Optional[str]: ...


@runtime_checkable
class SupersedePort(Protocol):
    def supersede(
        self, *, old_memory_id: str, new_body: str, idempotency_key: str
    ) -> SupersedeResult: ...


@runtime_checkable
class RetirePort(Protocol):
    def retire(self, *, memory_id: str, idempotency_key: str) -> RetireResult: ...


@dataclass(frozen=True)
class DurableMemoryAdapters:
    """Injected ports; any missing port yields unavailable/not_saved."""

    candidate: Optional[CandidateCreateAccept] = None
    atomic_write: Optional[AtomicWritePort] = None
    readback: Optional[ReadbackVerifyPort] = None
    exact_active: Optional[ExactActiveRetrievalPort] = None
    supersede: Optional[SupersedePort] = None
    retire: Optional[RetirePort] = None


def make_idempotency_key(
    *,
    actor_id: str,
    session_id: str,
    action: str,
    body: str,
    target_memory_id: str = "",
) -> str:
    """Bind actor, session, action, body; correction/forget also bind target id."""
    target = (target_memory_id or "").strip()
    digest = hashlib.sha256(
        f"{actor_id}|{session_id}|{action}|{normalize_memory_body(body)}|{target}".encode(
            "utf-8"
        )
    ).hexdigest()[:32]
    return f"dmi-{digest}"


def validate_fact_body(body: str) -> ValidationReason:
    text = normalize_memory_body(body)
    if not text:
        return "empty_body"
    if len(text) > MAX_BODY_LEN:
        return "malformed_body"
    if _TASK_SHAPE.search(text):
        return "task_not_fact"
    if _SENSITIVE.search(text):
        return "sensitivity_blocked"
    if _THIRD_PARTY_AMB.search(text):
        return "third_party_ambiguity"
    return "ok"


def plan_durable_action(intent: DurableMemoryIntent) -> DurableActionPlan:
    """Build a pure plan from a DurableMemoryIntent (no I/O)."""
    key = make_idempotency_key(
        actor_id=intent.actor_id,
        session_id=intent.session_id,
        action=intent.action,
        body=intent.normalized_body,
    )

    if intent.action == "none":
        if intent.reason_code == "inferred_suggestion_only":
            return DurableActionPlan(
                kind="suggest_review",
                intent_action=intent.action,
                body=intent.normalized_body,
                idempotency_key=key,
                validation="ok",
                requires_readback=False,
                preserve_audit=True,
                hard_delete=False,
            )
        return DurableActionPlan(
            kind="noop",
            intent_action=intent.action,
            body="",
            idempotency_key=key,
            validation="ok",
            requires_readback=False,
            preserve_audit=True,
            hard_delete=False,
        )

    if intent.target == "unresolved" or intent.request_exact_fact:
        return DurableActionPlan(
            kind="request_exact_fact",
            intent_action=intent.action,
            body="",
            idempotency_key=key,
            validation="unresolved_target",
            requires_readback=False,
            preserve_audit=True,
            hard_delete=False,
        )

    validation = validate_fact_body(intent.normalized_body)
    if validation != "ok":
        return DurableActionPlan(
            kind="reject",
            intent_action=intent.action,
            body=intent.normalized_body,
            idempotency_key=key,
            validation=validation,
            requires_readback=False,
            preserve_audit=True,
            hard_delete=False,
        )

    if intent.action == "save":
        return DurableActionPlan(
            kind="save_atomic",
            intent_action="save",
            body=intent.normalized_body,
            idempotency_key=key,
            validation="ok",
            requires_readback=True,
            preserve_audit=True,
            hard_delete=False,
        )
    if intent.action == "correct":
        return DurableActionPlan(
            kind="correct_supersede",
            intent_action="correct",
            body=intent.normalized_body,
            idempotency_key=key,
            validation="ok",
            requires_readback=True,
            preserve_audit=True,
            hard_delete=False,
        )
    if intent.action == "forget":
        return DurableActionPlan(
            kind="forget_retire",
            intent_action="forget",
            body=intent.normalized_body,
            idempotency_key=key,
            validation="ok",
            requires_readback=False,
            preserve_audit=True,
            hard_delete=False,
        )
    return DurableActionPlan(
        kind="reject",
        intent_action=intent.action,
        body=intent.normalized_body,
        idempotency_key=key,
        validation="unsupported_action",
        requires_readback=False,
        preserve_audit=True,
        hard_delete=False,
    )


def _is_strict_bool(value: object) -> bool:
    """Runtime bool check — rejects int/str truthiness."""
    return isinstance(value, bool)


def _usable_memory_id(value: object, *, max_len: int = 80) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > max_len:
        return ""
    return text


def _unavailable(
    plan: DurableActionPlan,
    *,
    reason: ValidationReason = "adapter_unavailable",
    memory_id: str = "",
    superseded_id: str = "",
    retired_id: str = "",
    idempotency_key: str = "",
) -> DurableActionOutcome:
    return DurableActionOutcome(
        status="unavailable",
        reason=reason,
        memory_id=memory_id,
        superseded_id=superseded_id,
        retired_id=retired_id,
        readback_ok=False,
        recallable=False,
        idempotency_key=idempotency_key or plan.idempotency_key,
    )


def _outcome_unknown(
    plan: DurableActionPlan,
    *,
    reason: ValidationReason,
    memory_id: str = "",
    superseded_id: str = "",
    retired_id: str = "",
    idempotency_key: str = "",
) -> DurableActionOutcome:
    """Post-mutation uncertainty — never formats as certain not_saved."""
    return _unavailable(
        plan,
        reason=reason,
        memory_id=memory_id,
        superseded_id=superseded_id,
        retired_id=retired_id,
        idempotency_key=idempotency_key,
    )


def execute_durable_plan(
    plan: DurableActionPlan,
    *,
    adapters: DurableMemoryAdapters,
    actor_id: str,
    session_id: str,
    target_memory_id: str = "",
) -> DurableActionOutcome:
    """Execute a plan through injected adapters. Never hard-deletes.

    Pre-write failures are known no-write. After a mutating adapter is invoked,
    failures are outcome-unknown (never certain "Not saved.").
    """
    if plan.hard_delete:
        return DurableActionOutcome(
            status="rejected",
            reason="malformed_body",
            idempotency_key=plan.idempotency_key,
        )

    if plan.kind == "noop":
        return DurableActionOutcome(
            status="not_saved",
            reason="ok",
            idempotency_key=plan.idempotency_key,
        )

    if plan.kind == "suggest_review":
        return DurableActionOutcome(
            status="pending_review",
            reason="ok",
            recallable=False,
            idempotency_key=plan.idempotency_key,
        )

    if plan.kind == "request_exact_fact":
        return DurableActionOutcome(
            status="not_saved",
            reason="unresolved_target",
            idempotency_key=plan.idempotency_key,
        )

    if plan.kind == "reject":
        return DurableActionOutcome(
            status="rejected",
            reason=plan.validation,
            idempotency_key=plan.idempotency_key,
        )

    # Known no-write: invalid correlation before any adapter I/O.
    if not is_canonical_correlation_id(actor_id) or not is_canonical_correlation_id(
        session_id
    ):
        return DurableActionOutcome(
            status="rejected",
            reason="malformed_body",
            idempotency_key=plan.idempotency_key,
        )

    exec_key = plan.idempotency_key
    if plan.kind in ("correct_supersede", "forget_retire"):
        exec_key = make_idempotency_key(
            actor_id=actor_id,
            session_id=session_id,
            action=plan.intent_action,
            body=plan.body,
            target_memory_id=target_memory_id,
        )

    if plan.kind == "save_atomic":
        writer = adapters.atomic_write or adapters.candidate
        if writer is None or adapters.readback is None:
            return _unavailable(plan)  # known no-write
        # Pre-write lookup only — failure is known no-write.
        if adapters.exact_active is not None:
            try:
                adapters.exact_active.find_exact_active(body=plan.body)
            except Exception:
                return _unavailable(plan)
        write = None
        try:
            if hasattr(writer, "atomic_write_active"):
                write = writer.atomic_write_active(  # type: ignore[union-attr]
                    body=plan.body,
                    idempotency_key=plan.idempotency_key,
                    actor_id=actor_id,
                    session_id=session_id,
                )
            else:
                write = writer.create_and_accept_candidate(  # type: ignore[union-attr]
                    body=plan.body,
                    idempotency_key=plan.idempotency_key,
                    actor_id=actor_id,
                    session_id=session_id,
                )
        except Exception:
            return _outcome_unknown(plan, reason="save_outcome_unknown")
        if not isinstance(write, AtomicWriteResult):
            return _outcome_unknown(plan, reason="save_outcome_unknown")
        if not (
            _is_strict_bool(write.created)
            and _is_strict_bool(write.conflict)
            and _is_strict_bool(write.pending_review)
        ):
            return _outcome_unknown(plan, reason="save_outcome_unknown")
        # Contradictory pending flags after mutation → uncertain.
        if write.conflict and write.pending_review:
            return _outcome_unknown(plan, reason="save_outcome_unknown")
        mid = _usable_memory_id(write.memory_id)
        if write.conflict:
            if not mid:
                return _outcome_unknown(plan, reason="save_outcome_unknown")
            return DurableActionOutcome(
                status="pending_conflict",
                reason="conflict",
                memory_id=mid,
                recallable=False,
                idempotency_key=plan.idempotency_key,
            )
        if write.pending_review:
            if not mid:
                return _outcome_unknown(plan, reason="save_outcome_unknown")
            return DurableActionOutcome(
                status="pending_review",
                reason="ok",
                memory_id=mid,
                recallable=False,
                idempotency_key=plan.idempotency_key,
            )
        if not mid:
            return _outcome_unknown(plan, reason="save_outcome_unknown")
        try:
            readback = adapters.readback.verify_readback(
                memory_id=mid, expected_body=plan.body
            )
        except Exception:
            return _outcome_unknown(
                plan, reason="save_outcome_unknown", memory_id=mid
            )
        if not isinstance(readback, ReadbackResult):
            return _outcome_unknown(
                plan, reason="save_outcome_unknown", memory_id=mid
            )
        if not (
            _is_strict_bool(readback.found)
            and _is_strict_bool(readback.active)
            and _is_strict_bool(readback.statement_matches)
        ):
            return _outcome_unknown(
                plan, reason="save_outcome_unknown", memory_id=mid
            )
        if not (readback.found and readback.active and readback.statement_matches):
            return _outcome_unknown(
                plan, reason="save_outcome_unknown", memory_id=mid
            )
        return DurableActionOutcome(
            status="accepted",
            reason="idempotent_replay" if not write.created else "ok",
            memory_id=mid,
            readback_ok=True,
            recallable=True,
            idempotency_key=plan.idempotency_key,
        )

    if plan.kind == "correct_supersede":
        if adapters.supersede is None or adapters.readback is None:
            return _unavailable(plan, idempotency_key=exec_key)  # known no-write
        old_id = _usable_memory_id(target_memory_id)
        if not old_id:
            return DurableActionOutcome(
                status="not_saved",
                reason="unresolved_target",
                idempotency_key=exec_key,
            )
        try:
            result = adapters.supersede.supersede(
                old_memory_id=old_id,
                new_body=plan.body,
                idempotency_key=exec_key,
            )
        except Exception:
            return _outcome_unknown(
                plan, reason="correction_outcome_unknown", idempotency_key=exec_key
            )
        if not isinstance(result, SupersedeResult):
            return _outcome_unknown(
                plan, reason="correction_outcome_unknown", idempotency_key=exec_key
            )
        returned_old = _usable_memory_id(result.old_memory_id)
        returned_new = _usable_memory_id(result.new_memory_id)
        if returned_old != old_id:
            return _outcome_unknown(
                plan, reason="correction_outcome_unknown", idempotency_key=exec_key
            )
        if not returned_new or returned_new == old_id:
            return _outcome_unknown(
                plan, reason="correction_outcome_unknown", idempotency_key=exec_key
            )
        if not _is_strict_bool(result.preserved_audit):
            return _outcome_unknown(
                plan, reason="correction_outcome_unknown", idempotency_key=exec_key
            )
        if not result.preserved_audit:
            return _outcome_unknown(
                plan, reason="correction_outcome_unknown", idempotency_key=exec_key
            )
        try:
            readback = adapters.readback.verify_readback(
                memory_id=returned_new, expected_body=plan.body
            )
        except Exception:
            return _outcome_unknown(
                plan,
                reason="correction_outcome_unknown",
                memory_id=returned_new,
                superseded_id=returned_old,
                idempotency_key=exec_key,
            )
        if not isinstance(readback, ReadbackResult):
            return _outcome_unknown(
                plan,
                reason="correction_outcome_unknown",
                memory_id=returned_new,
                superseded_id=returned_old,
                idempotency_key=exec_key,
            )
        if not (
            _is_strict_bool(readback.found)
            and _is_strict_bool(readback.active)
            and _is_strict_bool(readback.statement_matches)
        ):
            return _outcome_unknown(
                plan,
                reason="correction_outcome_unknown",
                memory_id=returned_new,
                superseded_id=returned_old,
                idempotency_key=exec_key,
            )
        if not (readback.found and readback.active and readback.statement_matches):
            return _outcome_unknown(
                plan,
                reason="correction_outcome_unknown",
                memory_id=returned_new,
                superseded_id=returned_old,
                idempotency_key=exec_key,
            )
        return DurableActionOutcome(
            status="corrected",
            reason="ok",
            memory_id=returned_new,
            superseded_id=returned_old,
            readback_ok=True,
            recallable=True,
            idempotency_key=exec_key,
        )

    if plan.kind == "forget_retire":
        if adapters.retire is None:
            return _unavailable(plan, idempotency_key=exec_key)  # known no-write
        mem_id = _usable_memory_id(target_memory_id)
        if not mem_id and adapters.exact_active is not None:
            try:
                found = adapters.exact_active.find_exact_active(body=plan.body)
            except Exception:
                return _unavailable(plan, idempotency_key=exec_key)  # pre-write
            mem_id = _usable_memory_id(found)
            if mem_id:
                exec_key = make_idempotency_key(
                    actor_id=actor_id,
                    session_id=session_id,
                    action=plan.intent_action,
                    body=plan.body,
                    target_memory_id=mem_id,
                )
        if not mem_id:
            return DurableActionOutcome(
                status="not_saved",
                reason="unresolved_target",
                idempotency_key=exec_key,
            )
        try:
            retired = adapters.retire.retire(
                memory_id=mem_id, idempotency_key=exec_key
            )
        except Exception:
            return _outcome_unknown(
                plan, reason="forget_outcome_unknown", idempotency_key=exec_key
            )
        if not isinstance(retired, RetireResult):
            return _outcome_unknown(
                plan, reason="forget_outcome_unknown", idempotency_key=exec_key
            )
        retired_id = _usable_memory_id(retired.memory_id)
        if retired_id != mem_id:
            return _outcome_unknown(
                plan,
                reason="forget_outcome_unknown",
                retired_id=retired_id,
                idempotency_key=exec_key,
            )
        if not (
            _is_strict_bool(retired.retired) and _is_strict_bool(retired.preserved)
        ):
            return _outcome_unknown(
                plan,
                reason="forget_outcome_unknown",
                retired_id=mem_id,
                idempotency_key=exec_key,
            )
        if not retired.retired or not retired.preserved:
            return _outcome_unknown(
                plan,
                reason="forget_outcome_unknown",
                retired_id=mem_id,
                idempotency_key=exec_key,
            )
        return DurableActionOutcome(
            status="forgotten",
            reason="ok",
            retired_id=retired_id,
            recallable=False,
            idempotency_key=exec_key,
        )

    return DurableActionOutcome(
        status="rejected",
        reason="unsupported_action",
        idempotency_key=plan.idempotency_key,
    )
