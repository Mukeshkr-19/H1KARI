"""Redacted Brain v2 phase readiness / sign-off (read-only, counts and categories only)."""

from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.brain_v2.legacy_reconciliation import (
    CATEGORY_SUPERSEDED_REVIEWED_FACT,
    audit_reconciliation,
    read_all_active_neural_fact_rows,
    resolve_neural_db_path,
)
from core.brain_v2.schemas import MemoryCandidateStatus

READINESS_READY = "READY"
READINESS_NOT_READY = "NOT READY"
REPAIR_MODE_READ_ONLY_PLAN = "read-only-plan-only"
REPAIR_APPLY_NOT_IMPLEMENTED = "not_implemented"
LEGACY_PERSONAL_RECALL_QUARANTINED = "quarantined"
LEGACY_PERSONAL_RECALL_DEGRADED = "degraded_runtime_unavailable"
LEGACY_PERSONAL_RECALL_NOT_QUARANTINED = "not_quarantined"
LEGACY_PATH_QUARANTINED = "quarantined"
LEGACY_PATH_DEGRADED = "degraded"
LEGACY_PATH_NOT_QUARANTINED = "not_quarantined"
PERSONAL_FACTUAL_AI_FALLBACK_BLOCKED = "blocked"
PERSONAL_FACTUAL_AI_FALLBACK_ALLOWED = "allowed"

BRAIN_V2_RUNTIME_AVAILABLE = "available"
BRAIN_V2_RUNTIME_DEGRADED = "degraded_unavailable"
BRAIN_V2_POLICY_DISABLED = "disabled"

_ACTIONABLE_RECONCILIATION_CATEGORIES = frozenset(
    {
        "stale_stable_location",
        "stale_current_location",
        "person_education_attributed_to_owner",
        "wrong_relation_person_association",
    }
)


@dataclass(frozen=True)
class BrainV2ReadinessReport:
    state: str
    pending_count: int
    rejected_count: int
    accepted_active_count: int
    unresolved_conflict_categories: Dict[str, int]
    repair_capability: str
    repair_apply_status: str
    episodes_db_source: str
    neural_audit_source: str
    brain_v2_policy: str
    brain_v2_runtime: str
    legacy_personal_recall_authority: str
    legacy_personal_answer_path: str
    legacy_personal_prompt_path: str
    legacy_personal_write_path: str
    unsafe_override_active: bool
    personal_factual_general_ai_fallback: str
    legacy_rows_preserved_for_private_migration: int
    blockers: List[str]
    readiness_meaning: str


def _brain_v2_policy_status() -> str:
    if os.getenv("HIKARI_DISABLE_BRAIN_V2", "0") == "1":
        return BRAIN_V2_POLICY_DISABLED
    return "enabled"


def _probe_brain_v2_runtime() -> Tuple[str, Optional[object]]:
    """Return (runtime_status, episodes_store_or_none)."""
    if _brain_v2_policy_status() == BRAIN_V2_POLICY_DISABLED:
        return BRAIN_V2_POLICY_DISABLED, None
    try:
        from core.brain_v2.db_paths import open_readonly_episode_store

        store = open_readonly_episode_store()
        store.get_candidates(status=MemoryCandidateStatus.PENDING)
        return BRAIN_V2_RUNTIME_AVAILABLE, store
    except (SystemExit, Exception):
        return BRAIN_V2_RUNTIME_DEGRADED, None


def _legacy_personal_recall_authority(*, policy: str, runtime: str) -> str:
    if policy == BRAIN_V2_POLICY_DISABLED:
        return LEGACY_PERSONAL_RECALL_NOT_QUARANTINED
    if runtime == BRAIN_V2_RUNTIME_AVAILABLE:
        return LEGACY_PERSONAL_RECALL_QUARANTINED
    return LEGACY_PERSONAL_RECALL_DEGRADED


def assess_brain_v2_readiness(*, episodes_store=None) -> BrainV2ReadinessReport:
    policy = _brain_v2_policy_status()
    runtime_status, probed_store = _probe_brain_v2_runtime()
    if episodes_store is not None and policy == "enabled":
        runtime_status = BRAIN_V2_RUNTIME_AVAILABLE
    store = episodes_store or probed_store
    if store is None and policy != BRAIN_V2_POLICY_DISABLED:
        try:
            from core.brain_v2.db_paths import open_readonly_episode_store

            store = open_readonly_episode_store()
        except (SystemExit, Exception):
            store = None
            if runtime_status == BRAIN_V2_RUNTIME_AVAILABLE:
                runtime_status = BRAIN_V2_RUNTIME_DEGRADED

    pending_count = 0
    rejected_count = 0
    accepted_active_count = 0
    if store is not None:
        pending = store.get_candidates(status=MemoryCandidateStatus.PENDING)
        rejected = store.get_candidates(status=MemoryCandidateStatus.REJECTED)
        active = store.get_active_accepted_memories(limit=500)
        pending_count = len(pending)
        rejected_count = len(rejected)
        accepted_active_count = len(active)
    else:
        pending = []
        rejected = []
        active = []

    neural_path = resolve_neural_db_path() if store is not None else None
    findings = (
        audit_reconciliation(store, include_statements=False, neural_db_path=neural_path)
        if store is not None
        else []
    )
    actionable = [
        f
        for f in findings
        if f.category != CATEGORY_SUPERSEDED_REVIEWED_FACT
        and f.recommended_action != "ignore_superseded_history"
        and f.category in _ACTIONABLE_RECONCILIATION_CATEGORIES
    ]
    cat_counts = Counter(f.category for f in actionable)

    preserved_rows = 0
    if neural_path and neural_path.is_file():
        preserved_rows = len(read_all_active_neural_fact_rows(neural_path))

    authority = _legacy_personal_recall_authority(policy=policy, runtime=runtime_status)
    from core.brain_v2.profile_summary import unsafe_neural_profile_supplement_env_set

    unsafe_override = unsafe_neural_profile_supplement_env_set()
    if policy == "enabled" and runtime_status == BRAIN_V2_RUNTIME_AVAILABLE:
        path_state = LEGACY_PATH_QUARANTINED
    elif policy == "enabled":
        path_state = LEGACY_PATH_DEGRADED
    else:
        path_state = LEGACY_PATH_NOT_QUARANTINED

    blockers: List[str] = []
    if policy == BRAIN_V2_POLICY_DISABLED:
        blockers.append("brain_v2_policy=disabled")
        if authority == LEGACY_PERSONAL_RECALL_NOT_QUARANTINED:
            blockers.append("legacy_personal_recall_authority=not_quarantined")
    if policy == "enabled" and runtime_status != BRAIN_V2_RUNTIME_AVAILABLE:
        blockers.append("brain_v2_runtime=degraded_unavailable")
    if store is not None and pending_count:
        blockers.append(f"pending_review={pending_count}")
    if cat_counts and authority != LEGACY_PERSONAL_RECALL_QUARANTINED:
        for category, count in sorted(cat_counts.items()):
            blockers.append(f"actionable_legacy_conflict:{category}={count}")
    if policy == "enabled" and authority == LEGACY_PERSONAL_RECALL_NOT_QUARANTINED:
        blockers.append("legacy_personal_recall_authority=not_quarantined")
    if unsafe_override:
        blockers.append("unsafe_neural_profile_supplement=enabled")
    personal_factual_ai = (
        PERSONAL_FACTUAL_AI_FALLBACK_BLOCKED
        if policy == "enabled"
        else PERSONAL_FACTUAL_AI_FALLBACK_ALLOWED
    )
    if policy == "enabled" and path_state != LEGACY_PATH_QUARANTINED:
        blockers.append("personal_factual_general_ai_fallback=not_blocked")

    state = READINESS_NOT_READY if blockers else READINESS_READY
    meaning = (
        "Brain v2 is the sole personal-memory authority: runtime available, no pending "
        "review, legacy neural answer/prompt/write paths quarantined, no unsafe override."
        if state == READINESS_READY
        else "Brain v2 personal recall authority or review queue is not sign-off ready."
    )

    if os.getenv("HIKARI_BRAIN_V2_EPISODES_DB"):
        episodes_source = "env_override"
    elif os.getenv("HIKARI_BRAIN_V2_EVAL") == "1":
        episodes_source = "eval_temp_only"
    else:
        episodes_source = "default_resolution"

    if neural_path and neural_path.is_file():
        if os.getenv("HIKARI_NEURAL_MEMORY_DB"):
            neural_audit = "read_only_configured_path"
        else:
            neural_audit = "read_only_default_resolution"
    else:
        neural_audit = "skipped_no_db"

    return BrainV2ReadinessReport(
        state=state,
        pending_count=pending_count,
        rejected_count=rejected_count,
        accepted_active_count=accepted_active_count,
        unresolved_conflict_categories=dict(cat_counts),
        repair_capability=REPAIR_MODE_READ_ONLY_PLAN,
        repair_apply_status=REPAIR_APPLY_NOT_IMPLEMENTED,
        episodes_db_source=episodes_source,
        neural_audit_source=neural_audit,
        brain_v2_policy=policy,
        brain_v2_runtime=runtime_status,
        legacy_personal_recall_authority=authority,
        legacy_personal_answer_path=path_state,
        legacy_personal_prompt_path=path_state,
        legacy_personal_write_path=path_state,
        unsafe_override_active=unsafe_override,
        personal_factual_general_ai_fallback=personal_factual_ai,
        legacy_rows_preserved_for_private_migration=preserved_rows,
        blockers=blockers,
        readiness_meaning=meaning,
    )


def format_readiness_lines(report: BrainV2ReadinessReport) -> List[str]:
    lines = [
        f"Brain v2 readiness: {report.state}",
        f"  meaning={report.readiness_meaning}",
        f"  brain_v2_policy={report.brain_v2_policy}",
        f"  brain_v2_runtime={report.brain_v2_runtime}",
        f"  pending_candidates={report.pending_count}",
        f"  rejected_candidates={report.rejected_count}",
        f"  accepted_active_memories={report.accepted_active_count}",
        f"  legacy_personal_recall_authority={report.legacy_personal_recall_authority}",
        f"  legacy_personal_answer_path={report.legacy_personal_answer_path}",
        f"  legacy_personal_prompt_path={report.legacy_personal_prompt_path}",
        f"  legacy_personal_write_path={report.legacy_personal_write_path}",
        f"  unsafe_override_active={str(report.unsafe_override_active).lower()}",
        (
            "  personal_factual_general_ai_fallback="
            f"{report.personal_factual_general_ai_fallback}"
        ),
        (
            "  legacy_rows_preserved_for_private_migration="
            f"{report.legacy_rows_preserved_for_private_migration}"
        ),
        f"  repair_capability={report.repair_capability}",
        f"  repair_apply_status={report.repair_apply_status}",
        f"  episodes_db_source={report.episodes_db_source}",
        f"  neural_audit_source={report.neural_audit_source}",
    ]
    if report.unresolved_conflict_categories:
        if report.legacy_personal_recall_authority == LEGACY_PERSONAL_RECALL_QUARANTINED:
            lines.append("  archival_legacy_findings (quarantined; optional migration):")
        else:
            lines.append("  actionable_legacy_conflicts:")
        for category, count in sorted(report.unresolved_conflict_categories.items()):
            lines.append(f"    - {category}={count}")
    else:
        lines.append("  archival_legacy_findings: none")
    if report.blockers:
        lines.append("  blockers:")
        for blocker in report.blockers:
            lines.append(f"    - {blocker}")
    lines.append(
        "  note: legacy personal data is quarantined from answers when runtime is available. "
        "Reported legacy findings are optional archive migration work and do not block "
        "READY while quarantine is active."
    )
    return lines


def run_brain_v2_readiness() -> int:
    if os.getenv("HIKARI_DISABLE_BRAIN_V2", "0") == "1":
        print("Brain v2 is disabled (HIKARI_DISABLE_BRAIN_V2=1).", file=sys.stderr)
        return 1
    try:
        report = assess_brain_v2_readiness()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for line in format_readiness_lines(report):
        print(line)
    return 0 if report.state == READINESS_READY else 1
