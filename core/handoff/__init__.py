"""Phase 4 bounded task-handoff core.

A handoff transfers only a bounded task reference and frozen preview.
It never transfers authority, approval IDs, grants, or execution tickets.
"""

from __future__ import annotations

from core.handoff.contracts import (
    FrozenHandoffPreview,
    HandoffErrorCode,
    HandoffRecord,
    HandoffResult,
    HandoffState,
    make_offer_record,
)
from core.handoff.service import AcceptancePolicy, HandoffService, TaskLookup
from core.handoff.store import HandoffStore

__all__ = [
    "FrozenHandoffPreview",
    "AcceptancePolicy",
    "HandoffErrorCode",
    "HandoffRecord",
    "HandoffResult",
    "HandoffService",
    "HandoffState",
    "HandoffStore",
    "TaskLookup",
    "make_offer_record",
]
