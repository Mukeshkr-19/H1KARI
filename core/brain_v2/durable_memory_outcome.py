"""Truthful durable-memory acknowledgments and retrieval decision policy.

Acknowledgment formatter accepts only typed verified outcomes — never
free-form model text as proof of persistence. Diagnostics stay content-safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence, Tuple

from core.brain_v2.durable_memory_actions import DurableActionOutcome, OutcomeStatus

AckMessage = Literal[
    "Saved to long-term Brain.",
    "Queued for review; not recallable yet.",
    "Conflict detected; previous record was not replaced.",
    "Not saved.",
    "Could not verify whether the durable save completed.",
    "Could not verify whether the correction completed.",
    "Could not verify whether the record was retired.",
    "Corrected; previous record superseded and new active record verified.",
    "Forgotten; record retired and excluded from active recall.",
    "Need the exact fact; nothing was written.",
]

RetrievalLane = Literal[
    "exact_active",
    "semantic_active",
    "status_explanation",
    "none",
]

MemoryLifecycleState = Literal[
    "active",
    "pending",
    "rejected",
    "retired",
    "superseded",
]


@dataclass(frozen=True)
class RetrievalCandidate:
    """Synthetic retrieval hit for pure decision policy tests."""

    memory_id: str
    lane: Literal["exact_active", "semantic_active", "status_only"]
    lifecycle: MemoryLifecycleState
    match_score: float = 0.0
    status_query_match: bool = False

    def __repr__(self) -> str:
        return (
            "RetrievalCandidate("
            f"lane={self.lane!r}, lifecycle={self.lifecycle!r}, "
            f"match_score={self.match_score!r}, "
            f"status_query_match={self.status_query_match!r}, "
            f"has_memory_id={bool(self.memory_id)})"
        )


@dataclass(frozen=True)
class RetrievalDecision:
    lane: RetrievalLane
    memory_id: str = ""
    explain_status: MemoryLifecycleState | None = None
    reason_code: str = "none"

    def __repr__(self) -> str:
        return (
            "RetrievalDecision("
            f"lane={self.lane!r}, reason_code={self.reason_code!r}, "
            f"explain_status={self.explain_status!r}, "
            f"has_memory_id={bool(self.memory_id)})"
        )


_STATUS_TO_ACK: dict[OutcomeStatus, AckMessage] = {
    "accepted": "Saved to long-term Brain.",
    "pending_review": "Queued for review; not recallable yet.",
    "pending_conflict": "Conflict detected; previous record was not replaced.",
    "rejected": "Not saved.",
    "not_saved": "Not saved.",
    "unavailable": "Not saved.",
    "corrected": "Corrected; previous record superseded and new active record verified.",
    "forgotten": "Forgotten; record retired and excluded from active recall.",
}

_CONTENT_SAFE_REASONS = {
    "ok": "ok",
    "empty_body": "empty_input",
    "malformed_body": "malformed_input",
    "task_not_fact": "task_not_fact",
    "sensitivity_blocked": "sensitivity_blocked",
    "third_party_ambiguity": "third_party_ambiguity",
    "conflict": "conflict",
    "unresolved_target": "needs_exact_fact",
    "unsupported_action": "unsupported",
    "adapter_unavailable": "unavailable",
    "readback_failed": "verification_failed",
    "idempotent_replay": "ok",
    "save_outcome_unknown": "save_outcome_unknown",
    "correction_outcome_unknown": "correction_outcome_unknown",
    "forget_outcome_unknown": "forget_outcome_unknown",
}

_UNCERTAIN_SAVE = "Could not verify whether the durable save completed."
_UNCERTAIN_CORRECT = "Could not verify whether the correction completed."
_UNCERTAIN_FORGET = "Could not verify whether the record was retired."


@dataclass(frozen=True)
class DurableAck:
    message: AckMessage
    status: OutcomeStatus
    reason_code: str
    recallable: bool

    def __repr__(self) -> str:
        return (
            "DurableAck("
            f"message={self.message!r}, status={self.status!r}, "
            f"reason_code={self.reason_code!r}, recallable={self.recallable!r})"
        )


def format_durable_acknowledgment(outcome: DurableActionOutcome) -> DurableAck:
    """Format an acknowledgment from a typed verified outcome only."""
    if not isinstance(outcome, DurableActionOutcome):
        raise TypeError("outcome_must_be_DurableActionOutcome")

    status = outcome.status
    safe_reason = _CONTENT_SAFE_REASONS.get(outcome.reason, "unspecified")

    if status == "accepted":
        if not (outcome.memory_id and outcome.readback_ok and outcome.recallable):
            return DurableAck(
                message=_UNCERTAIN_SAVE,
                status="unavailable",
                reason_code="verification_failed",
                recallable=False,
            )
        return DurableAck(
            message="Saved to long-term Brain.",
            status="accepted",
            reason_code=safe_reason,
            recallable=True,
        )

    if status == "corrected":
        if not (
            outcome.memory_id
            and outcome.readback_ok
            and outcome.recallable
            and outcome.superseded_id
        ):
            return DurableAck(
                message=_UNCERTAIN_CORRECT,
                status="unavailable",
                reason_code="verification_failed",
                recallable=False,
            )
        return DurableAck(
            message=_STATUS_TO_ACK["corrected"],
            status="corrected",
            reason_code=safe_reason,
            recallable=True,
        )

    if status == "forgotten":
        if not outcome.retired_id or outcome.recallable:
            return DurableAck(
                message=_UNCERTAIN_FORGET,
                status="unavailable",
                reason_code="verification_failed",
                recallable=False,
            )
        return DurableAck(
            message=_STATUS_TO_ACK["forgotten"],
            status="forgotten",
            reason_code=safe_reason,
            recallable=False,
        )

    def _usable_ack_id(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip()) and len(value.strip()) <= 80

    if status == "pending_review":
        # Adapter-correlated pending requires a usable memory ID.
        # Suggestion-only (reason ok, no ID) remains a known non-write review queue.
        if not _usable_ack_id(outcome.memory_id):
            if outcome.reason == "ok":
                return DurableAck(
                    message=_STATUS_TO_ACK["pending_review"],
                    status="pending_review",
                    reason_code=safe_reason,
                    recallable=False,
                )
            return DurableAck(
                message=_UNCERTAIN_SAVE,
                status="unavailable",
                reason_code="save_outcome_unknown",
                recallable=False,
            )
        return DurableAck(
            message=_STATUS_TO_ACK["pending_review"],
            status="pending_review",
            reason_code=safe_reason,
            recallable=False,
        )

    if status == "pending_conflict":
        if not _usable_ack_id(outcome.memory_id):
            return DurableAck(
                message=_UNCERTAIN_SAVE,
                status="unavailable",
                reason_code="save_outcome_unknown",
                recallable=False,
            )
        return DurableAck(
            message=_STATUS_TO_ACK["pending_conflict"],
            status="pending_conflict",
            reason_code=safe_reason,
            recallable=False,
        )

    if status == "unavailable":
        if outcome.reason == "save_outcome_unknown":
            return DurableAck(
                message=_UNCERTAIN_SAVE,
                status="unavailable",
                reason_code="save_outcome_unknown",
                recallable=False,
            )
        if outcome.reason == "correction_outcome_unknown":
            return DurableAck(
                message=_UNCERTAIN_CORRECT,
                status="unavailable",
                reason_code="correction_outcome_unknown",
                recallable=False,
            )
        if outcome.reason == "forget_outcome_unknown":
            return DurableAck(
                message=_UNCERTAIN_FORGET,
                status="unavailable",
                reason_code="forget_outcome_unknown",
                recallable=False,
            )
        # Known pre-write adapter absence (and any other non-action-specific unavailable).
        return DurableAck(
            message="Not saved.",
            status="unavailable",
            reason_code=safe_reason,
            recallable=False,
        )

    if status == "not_saved" and outcome.reason == "unresolved_target":
        return DurableAck(
            message="Need the exact fact; nothing was written.",
            status="not_saved",
            reason_code="needs_exact_fact",
            recallable=False,
        )

    # rejected / not_saved (known no-write paths)
    return DurableAck(
        message="Not saved.",
        status=status if status in ("rejected", "not_saved", "unavailable") else "not_saved",
        reason_code=safe_reason,
        recallable=False,
    )


def _score_valid(score: float) -> bool:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return False
    if value != value:  # NaN
        return False
    if value in (float("inf"), float("-inf")):
        return False
    return 0.0 <= value <= 1.0


def _pick_best(candidates: Sequence[RetrievalCandidate]) -> Optional[RetrievalCandidate]:
    valid = [c for c in candidates if _score_valid(c.match_score)]
    if not valid:
        return None
    # Deterministic: highest score, then stable memory_id tie-break.
    return max(valid, key=lambda c: (c.match_score, c.memory_id))


def decide_retrieval(
    candidates: Sequence[RetrievalCandidate],
    *,
    status_query: bool = False,
) -> RetrievalDecision:
    """Pure retrieval lane policy.

    Order: exact active structured facts, then semantic active matches.
    Pending/rejected/retired/superseded only when specifically matched for
    status explanation. Never blanket "check pending review".
    """
    exact_active = [
        c
        for c in candidates
        if c.lane == "exact_active" and c.lifecycle == "active"
    ]
    best_exact = _pick_best(exact_active)
    if best_exact is not None:
        return RetrievalDecision(
            lane="exact_active",
            memory_id=best_exact.memory_id,
            reason_code="exact_active_hit",
        )

    semantic_active = [
        c
        for c in candidates
        if c.lane == "semantic_active" and c.lifecycle == "active"
    ]
    best_sem = _pick_best(semantic_active)
    if best_sem is not None:
        return RetrievalDecision(
            lane="semantic_active",
            memory_id=best_sem.memory_id,
            reason_code="semantic_active_hit",
        )

    if status_query:
        status_hits = [
            c
            for c in candidates
            if c.status_query_match
            and c.lifecycle in ("pending", "rejected", "retired", "superseded")
        ]
        if len(status_hits) == 1:
            hit = status_hits[0]
            return RetrievalDecision(
                lane="status_explanation",
                memory_id=hit.memory_id,
                explain_status=hit.lifecycle,
                reason_code="matched_status_explanation",
            )
        if len(status_hits) > 1:
            return RetrievalDecision(
                lane="none",
                reason_code="ambiguous_status_matches",
            )

    # Never fall back to pending review without a specific match.
    return RetrievalDecision(lane="none", reason_code="no_active_match")


def content_safe_diagnostic(decision: RetrievalDecision) -> str:
    """Content-safe diagnostic string — never includes private values."""
    parts: Tuple[str, ...] = (
        f"lane={decision.lane}",
        f"reason={decision.reason_code}",
        f"has_id={'yes' if decision.memory_id else 'no'}",
    )
    if decision.explain_status:
        parts = parts + (f"status={decision.explain_status}",)
    return "; ".join(parts)
