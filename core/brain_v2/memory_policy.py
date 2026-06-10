"""Central memory policy router — silent routing for owner utterances."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from core.brain_v2.memory_type import MemoryTypeInference, infer_memory_type
from core.brain_v2.owner_auto_trust import is_explicit_remember_command

_UNCERTAIN_HYPOTHETICAL = re.compile(
    r"\b(?:might|maybe|perhaps|possibly|thinking\s+about|considering|"
    r"not\s+sure\s+if|could\s+(?:move|relocate)|may\s+(?:move|relocate))\b",
    re.I,
)


class MemoryPolicyRoute(str, Enum):
    ACTIVE_MEMORY = "active_memory"
    SESSION_MEMORY = "session_memory"
    EPISODE_ONLY = "episode_only"
    TASK = "task"
    REVIEW_QUEUE = "review_queue"
    REJECT = "reject"


@dataclass(frozen=True)
class MemoryPolicyDecision:
    route: MemoryPolicyRoute
    candidate_type: str = "fact"
    reason: str = ""
    statement: str = ""
    inferred: Optional[MemoryTypeInference] = None


def route_owner_utterance(
    text: str,
    *,
    guest: bool = False,
    skip_owner_identity: bool = False,
) -> MemoryPolicyDecision:
    """Route one owner utterance to a single memory bucket (silent by default)."""
    raw = (text or "").strip()
    if not raw:
        return MemoryPolicyDecision(MemoryPolicyRoute.REJECT, reason="empty")

    if guest:
        from core.brain_statements import is_declarative_memory_statement

        if is_declarative_memory_statement(raw):
            return MemoryPolicyDecision(
                MemoryPolicyRoute.REJECT,
                reason="guest_owner_memory",
                statement=raw,
            )

    if skip_owner_identity:
        return MemoryPolicyDecision(
            MemoryPolicyRoute.EPISODE_ONLY,
            reason="skip_owner_identity",
            statement=raw,
        )

    from core.brain_statements import (
        is_declarative_memory_statement,
        is_task_or_action_statement,
    )

    if is_task_or_action_statement(raw):
        return MemoryPolicyDecision(
            MemoryPolicyRoute.TASK,
            reason="task_or_action",
            statement=raw,
        )

    try:
        from core.speaker_context import is_temporary_speaker_intro

        if is_temporary_speaker_intro(raw):
            return MemoryPolicyDecision(
                MemoryPolicyRoute.REJECT,
                reason="guest_intro",
                statement=raw,
            )
    except ImportError:
        pass

    if is_casual_episode_filler(raw):
        return MemoryPolicyDecision(
            MemoryPolicyRoute.EPISODE_ONLY,
            reason="casual_filler",
            statement=raw,
        )

    if is_uncertain_or_hypothetical(raw):
        inferred = infer_memory_type(raw)
        return MemoryPolicyDecision(
            MemoryPolicyRoute.EPISODE_ONLY,
            candidate_type=inferred.candidate_type,
            reason="uncertain_hypothetical",
            statement=raw,
            inferred=inferred,
        )

    explicit = is_explicit_remember_command(raw)
    inferred = infer_memory_type(raw, explicit_remember=explicit)

    if inferred.candidate_type == "current_location":
        return MemoryPolicyDecision(
            MemoryPolicyRoute.SESSION_MEMORY,
            candidate_type="current_location",
            reason="trip_or_current_location",
            statement=raw,
            inferred=inferred,
        )

    if not is_declarative_memory_statement(raw):
        return MemoryPolicyDecision(
            MemoryPolicyRoute.EPISODE_ONLY,
            reason="not_declarative",
            statement=raw,
            inferred=inferred,
        )

    from core.brain_v2.candidate_quality import (
        QUALITY_REJECT,
        QUALITY_WEAK,
        classify_candidate,
    )
    from core.brain_v2.owner_auto_trust import is_owner_scoped_auto_trust_candidate
    from core.brain_v2.schemas import MemoryCandidate

    quality = classify_candidate(
        raw,
        candidate_type=inferred.candidate_type,
        explicit_remember=explicit,
    )
    if quality.label == QUALITY_REJECT:
        return MemoryPolicyDecision(
            MemoryPolicyRoute.EPISODE_ONLY,
            candidate_type=inferred.candidate_type,
            reason="quality_reject",
            statement=raw,
            inferred=inferred,
        )

    meta: Dict[str, object] = {
        **(inferred.metadata or {}),
        **quality.to_metadata(),
    }
    if explicit:
        meta["explicit_remember"] = True
    candidate = MemoryCandidate(
        candidate_id=str(uuid.uuid4()),
        episode_id="policy-check",
        statement=raw,
        candidate_type=inferred.candidate_type,
        metadata=meta,
    )

    if is_owner_scoped_auto_trust_candidate(candidate, raw):
        return MemoryPolicyDecision(
            MemoryPolicyRoute.ACTIVE_MEMORY,
            candidate_type=inferred.candidate_type,
            reason="owner_auto_trust",
            statement=raw,
            inferred=inferred,
        )

    if explicit:
        return MemoryPolicyDecision(
            MemoryPolicyRoute.REVIEW_QUEUE,
            candidate_type=inferred.candidate_type,
            reason="explicit_remember_review",
            statement=raw,
            inferred=inferred,
        )

    if quality.label == QUALITY_WEAK:
        return MemoryPolicyDecision(
            MemoryPolicyRoute.EPISODE_ONLY,
            candidate_type=inferred.candidate_type,
            reason="weak_fact",
            statement=raw,
            inferred=inferred,
        )

    return MemoryPolicyDecision(
        MemoryPolicyRoute.REVIEW_QUEUE,
        candidate_type=inferred.candidate_type,
        reason="needs_review",
        statement=raw,
        inferred=inferred,
    )


def is_uncertain_or_hypothetical(text: str) -> bool:
    """Plans or facts stated with uncertainty should not become active truth."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_UNCERTAIN_HYPOTHETICAL.search(raw))


def is_casual_episode_filler(text: str) -> bool:
    """Short acknowledgements should stay conversational, not model-dependent."""
    raw = re.sub(r"[.!?,]+", "", (text or "").strip().lower())
    if not raw:
        return False
    return raw in {
        "haha",
        "haha okay",
        "lol",
        "lol okay",
        "ok",
        "okay",
        "oh okay",
        "cool",
        "nice",
        "got it",
        "thanks",
        "thank you",
    }


def policy_route_table() -> Dict[str, str]:
    """Human-readable policy summary for docs and tests."""
    return {
        "identity / stable home / education / preference / clear relation": MemoryPolicyRoute.ACTIVE_MEMORY.value,
        "trip or current city (I am in ...)": MemoryPolicyRoute.SESSION_MEMORY.value,
        "casual chat / filler / vague / uncertain": MemoryPolicyRoute.EPISODE_ONLY.value,
        "reminders / open / schedule / code tasks": MemoryPolicyRoute.TASK.value,
        "third-party or sensitive facts": MemoryPolicyRoute.REVIEW_QUEUE.value,
        "guest owner-fact attempts": MemoryPolicyRoute.REJECT.value,
    }
