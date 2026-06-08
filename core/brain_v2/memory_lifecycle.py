"""Accepted-memory lifecycle helpers (active / retired / superseded)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.brain_v2.schemas import SourceLinkedMemory

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_RETIRED = "retired"
LIFECYCLE_SUPERSEDED = "superseded"
CORRECTION_SOURCE_OPERATOR = "operator_reviewed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def lifecycle_status(metadata: Optional[Dict[str, Any]]) -> str:
    return str((metadata or {}).get("lifecycle_status") or LIFECYCLE_ACTIVE)


def is_active_memory(memory: SourceLinkedMemory) -> bool:
    return lifecycle_status(memory.metadata) == LIFECYCLE_ACTIVE


def filter_active_memories(
    memories: List[SourceLinkedMemory],
) -> List[SourceLinkedMemory]:
    return [m for m in memories if is_active_memory(m)]


def append_audit_entry(metadata: Dict[str, Any], action: str, **fields: Any) -> Dict[str, Any]:
    meta = dict(metadata)
    audit = list(meta.get("correction_audit") or [])
    entry = {"action": action, "at": _utc_now(), **fields}
    audit.append(entry)
    meta["correction_audit"] = audit
    return meta


def is_operator_reviewed_correction(memory: SourceLinkedMemory) -> bool:
    return (memory.metadata or {}).get("correction_source") == CORRECTION_SOURCE_OPERATOR


def collect_inactive_normalized_statements(
    memories: List[SourceLinkedMemory],
) -> set:
    from core.brain_v2.candidate_scoring import normalize_statement

    inactive: set = set()
    for mem in memories:
        if not is_active_memory(mem):
            norm = normalize_statement(mem.statement)
            if norm:
                inactive.add(norm)
    return inactive
