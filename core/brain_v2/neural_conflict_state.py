"""Unresolved legacy neural conflict detection for safe recall suppression."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

from core.brain_v2.conflicts import (
    CONFLICT_PARTNER_EDUCATION_ON_USER,
    CONFLICT_STALE_CURRENTLY_IN,
    CONFLICT_STALE_HOME,
    CONFLICT_UNREVIEWED_LEGACY_HOME,
    ConflictReport,
    scan_conflicts,
)
from core.brain_v2.profile_summary import ReviewedProfileFacts, collect_reviewed_profile_facts
from core.brain_v2.recall_intent import (
    INTENT_CURRENT_LOCATION,
    INTENT_EDUCATION,
    INTENT_LOCATION,
    INTENT_PROFILE_SUMMARY,
)
from core.brain_v2.schemas import SourceLinkedMemory

CONFLICT_REVIEW_NEEDED_MESSAGE = (
    "I have conflicting older memory for that and it needs review "
    "before I answer confidently."
)


@dataclass
class UnresolvedConflictSnapshot:
    """Categories where legacy neural conflicts exist without safe reviewed truth."""

    conflict_types: Set[str] = field(default_factory=set)
    categories: Set[str] = field(default_factory=set)

    def blocks_recall_intent(self, intent: str) -> bool:
        if not self.conflict_types:
            return False
        if intent == INTENT_PROFILE_SUMMARY:
            return bool(self.conflict_types)
        if intent == INTENT_LOCATION:
            return CONFLICT_STALE_HOME in self.conflict_types
        if intent == INTENT_CURRENT_LOCATION:
            return CONFLICT_STALE_CURRENTLY_IN in self.conflict_types
        if intent == INTENT_EDUCATION:
            return CONFLICT_PARTNER_EDUCATION_ON_USER in self.conflict_types
        return False


def build_unresolved_conflict_snapshot(
    accepted_memories: List[SourceLinkedMemory],
    neural_summary: Optional[str],
    *,
    facts: Optional[ReviewedProfileFacts] = None,
) -> UnresolvedConflictSnapshot:
    """Return conflict categories that lack safe reviewed Brain v2 truth."""
    reports = scan_conflicts(accepted_memories, neural_summary)
    if not reports:
        return UnresolvedConflictSnapshot()

    profile = facts or collect_reviewed_profile_facts(accepted_memories)
    unresolved_types: Set[str] = set()
    categories: Set[str] = set()

    for report in reports:
        categories.add(report.conflict_type)
        if _is_unresolved_for_reviewed_truth(report, profile):
            unresolved_types.add(report.conflict_type)

    return UnresolvedConflictSnapshot(
        conflict_types=unresolved_types,
        categories=categories,
    )


def _is_unresolved_for_reviewed_truth(
    report: ConflictReport,
    facts: ReviewedProfileFacts,
) -> bool:
    if report.conflict_type == CONFLICT_UNREVIEWED_LEGACY_HOME:
        return False
    if report.conflict_type == CONFLICT_STALE_HOME:
        return False
    if report.conflict_type == CONFLICT_STALE_CURRENTLY_IN:
        return not facts.has_reviewed_current_context
    if report.conflict_type == CONFLICT_PARTNER_EDUCATION_ON_USER:
        return not facts.has_reviewed_user_education
    return True
