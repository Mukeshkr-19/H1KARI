"""Shared types for legacy neural reconciliation and repair (no circular imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

REPAIR_CONFIRM_TOKEN = "REPAIR"
PLAN_STATUS_NOT_APPLIED = "not_applied"
PLAN_STATUS_APPLIED = "applied"


@dataclass
class RepairPlanItem:
    plan_id: str
    category: str
    action: str
    target_memory_id: Optional[str] = None
    neural_target_id: Optional[str] = None
    target_fingerprint: Optional[str] = None
    plan_source_db_fingerprint: Optional[str] = None
    requires_backup: bool = True
    requires_confirm_token: str = REPAIR_CONFIRM_TOKEN
    status: str = PLAN_STATUS_NOT_APPLIED


@dataclass
class RepairPlan:
    plan_id: str
    items: List[RepairPlanItem] = field(default_factory=list)
    source_db_fingerprint: Optional[str] = None
