"""Brain v2 data models — Omi-inspired episode and memory candidate pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EpisodeLifecycleState(str, Enum):
    IN_PROGRESS = "in_progress"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class MemoryCandidateStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class MemoryLayer(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    ENTITY = "entity"
    PROCEDURAL = "procedural"
    CONSOLIDATION = "consolidation"


@dataclass
class TranscriptSegment:
    """One utterance or chat turn inside a raw episode (verbatim evidence)."""

    segment_id: str
    episode_id: str
    sequence: int
    text: str
    is_user: bool = True
    speaker_label: str = "user"
    started_at: str = field(default_factory=_utc_now)
    ended_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranscriptSegment":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class StructuredEpisode:
    """Consolidated view of an episode — separate from raw transcript segments."""

    episode_id: str
    session_id: str
    lifecycle_state: str = EpisodeLifecycleState.COMPLETED.value
    title: str = ""
    summary: str = ""
    action_items: List[str] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    segment_count: int = 0
    started_at: str = field(default_factory=_utc_now)
    ended_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredEpisode":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class MemoryCandidate:
    """Extracted memory proposal — not durable until reviewed."""

    candidate_id: str
    episode_id: str
    statement: str
    candidate_type: str = "fact"
    confidence: float = 0.5
    salience: float = 0.5
    review_status: str = MemoryCandidateStatus.PENDING.value
    source_segment_ids: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryCandidate":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SourceLinkedMemory:
    """Accepted memory with evidence links back to episode segments."""

    memory_id: str
    candidate_id: str
    episode_id: str
    statement: str
    source_segment_ids: List[str] = field(default_factory=list)
    neural_node_key: Optional[str] = None
    accepted_at: str = field(default_factory=_utc_now)
    layer: str = MemoryLayer.SEMANTIC.value
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceLinkedMemory":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def dumps_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)

def loads_json(raw: Optional[str], default: Any = None) -> Any:
    if not raw:
        return default if default is not None else {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default if default is not None else {}
