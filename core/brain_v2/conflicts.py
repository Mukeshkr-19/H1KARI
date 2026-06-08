"""Read-only conflict detection between reviewed Brain v2 memories and neural profile lines."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.brain_v2.profile_summary import (
    _is_partner_education_memory,
    _norm_place,
    collect_reviewed_profile_facts,
)
from core.brain_v2.schemas import SourceLinkedMemory

CONFLICT_STALE_HOME = "stale_home"
CONFLICT_STALE_CURRENTLY_IN = "stale_currently_in"
CONFLICT_PARTNER_EDUCATION_ON_USER = "partner_education_on_user"
CONFLICT_UNREVIEWED_LEGACY_HOME = "unreviewed_legacy_home"

NEURAL_UNAVAILABLE_NOTICE = (
    "Neural summary unavailable; checked accepted Brain v2 memories only."
)

CONFLICT_REDACTED_PLACEHOLDER = "[redacted]"

ACTION_REVIEW = "review"
ACTION_IGNORE = "ignore"
ACTION_CLEAN_MANUALLY = "clean manually"


@dataclass(frozen=True)
class ConflictReport:
    """One mismatch between an accepted Brain v2 memory and a neural/profile line."""

    conflict_type: str
    reviewed_statement: str
    conflicting_line: str
    recommended_action: str
    neural_target_id: Optional[str] = None


def _best_location_statement(memories: List[SourceLinkedMemory]) -> str:
    for mem in memories:
        meta = mem.metadata or {}
        ctype = str(meta.get("candidate_type", "fact"))
        low = (mem.statement or "").lower()
        if ctype == "location" or ("live in" in low and ctype != "current_location"):
            return (mem.statement or "").strip()
    return ""


def _best_current_location_statement(memories: List[SourceLinkedMemory]) -> str:
    for mem in memories:
        meta = mem.metadata or {}
        ctype = str(meta.get("candidate_type", "fact"))
        if ctype == "current_location" or meta.get("current_location"):
            return (mem.statement or "").strip()
    return ""


def _best_partner_education_statement(memories: List[SourceLinkedMemory]) -> str:
    for mem in memories:
        if _is_partner_education_memory(mem):
            return (mem.statement or "").strip()
    return ""


def scan_conflicts(
    accepted_memories: List[SourceLinkedMemory],
    neural_summary: Optional[str],
) -> List[ConflictReport]:
    """Detect conservative conflicts between reviewed memories and neural profile lines."""
    if not neural_summary or not neural_summary.strip():
        return []

    facts = collect_reviewed_profile_facts(accepted_memories)
    reports: List[ConflictReport] = []

    for line in neural_summary.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("what i know about"):
            continue

        home_match = re.match(r"-\s*Home:\s*(.+)", stripped, re.I)
        if home_match:
            home = _norm_place(home_match.group(1))
            if facts.has_reviewed_location:
                if facts.user_locations and home not in facts.user_locations:
                    reports.append(
                        ConflictReport(
                            conflict_type=CONFLICT_STALE_HOME,
                            reviewed_statement=_best_location_statement(accepted_memories),
                            conflicting_line=stripped,
                            recommended_action=ACTION_CLEAN_MANUALLY,
                        )
                    )
            else:
                reports.append(
                    ConflictReport(
                        conflict_type=CONFLICT_UNREVIEWED_LEGACY_HOME,
                        reviewed_statement=_best_location_statement(accepted_memories),
                        conflicting_line=stripped,
                        recommended_action=ACTION_REVIEW,
                    )
                )

        current_match = re.match(r"-\s*Currently\s+in:\s*(.+)", stripped, re.I)
        if current_match:
            curr = _norm_place(current_match.group(1))
            if facts.has_reviewed_current_context:
                if (
                    facts.current_context_locations
                    and curr not in facts.current_context_locations
                ):
                    reports.append(
                        ConflictReport(
                            conflict_type=CONFLICT_STALE_CURRENTLY_IN,
                            reviewed_statement=_best_current_location_statement(
                                accepted_memories
                            ),
                            conflicting_line=stripped,
                            recommended_action=ACTION_REVIEW,
                        )
                    )
            elif facts.has_reviewed_location and not facts.has_reviewed_current_context:
                reports.append(
                    ConflictReport(
                        conflict_type=CONFLICT_STALE_CURRENTLY_IN,
                        reviewed_statement=_best_location_statement(accepted_memories),
                        conflicting_line=stripped,
                        recommended_action=ACTION_IGNORE,
                    )
                )

        edu_match = re.match(r"-\s*Education:\s*(.+)", stripped, re.I)
        if edu_match:
            school = _norm_place(edu_match.group(1))
            # Summary lines are owner-profile education only; partner-described rows
            # must not appear as "- Education:" (see build_neural_summary_lines).
            if (
                facts.has_reviewed_partner_education
                and school in facts.partner_education_orgs
            ):
                reports.append(
                    ConflictReport(
                        conflict_type=CONFLICT_PARTNER_EDUCATION_ON_USER,
                        reviewed_statement=_best_partner_education_statement(
                            accepted_memories
                        ),
                        conflicting_line=stripped,
                        recommended_action=ACTION_CLEAN_MANUALLY,
                    )
                )

    return reports


def format_conflict_report_lines(
    reports: List[ConflictReport],
    *,
    redact: bool = True,
) -> List[str]:
    """Human-readable conflict lines for status/CLI display (no mutation)."""
    if not reports:
        return ["Brain v2 / neural conflicts: none detected"]
    lines = [f"Brain v2 / neural conflicts: {len(reports)}"]
    for idx, report in enumerate(reports, start=1):
        lines.append(f"  {idx}. [{report.conflict_type}] action={report.recommended_action}")
        if not redact:
            if report.reviewed_statement:
                lines.append(f"     reviewed: {report.reviewed_statement[:120]}")
            lines.append(f"     neural:   {report.conflicting_line[:120]}")
        else:
            lines.append(f"     reviewed: {CONFLICT_REDACTED_PLACEHOLDER}")
            lines.append(f"     neural:   {CONFLICT_REDACTED_PLACEHOLDER}")
    return lines


def fetch_neural_summary_quiet() -> tuple[Optional[str], bool]:
    """Default: no neural read. Private local mode uses read-only configured DB path."""
    if os.getenv("HIKARI_BRAIN_V2_CONFLICTS_PRIVATE", "0") == "1":
        from core.brain_v2.legacy_reconciliation import fetch_neural_summary_readonly

        return fetch_neural_summary_readonly()
    return None, False


def _default_episodes_db_path() -> Path:
    from core.brain_v2.db_paths import resolve_episodes_db_path

    return resolve_episodes_db_path()


def run_brain_v2_conflicts() -> int:
    """CLI entry for ``--brain-v2-conflicts`` (no cli/status/neural imports)."""
    if os.getenv("HIKARI_DISABLE_BRAIN_V2", "0") == "1":
        print("Brain v2 is disabled (HIKARI_DISABLE_BRAIN_V2=1).", file=sys.stderr)
        return 1

    db_path = _default_episodes_db_path()
    if db_path.is_file():
        from core.brain_v2.db_paths import open_readonly_episode_store

        store = open_readonly_episode_store()
        accepted = store.get_active_accepted_memories(limit=200)
    else:
        accepted = []
    neural_summary, neural_available = fetch_neural_summary_quiet()
    if not neural_available:
        print(NEURAL_UNAVAILABLE_NOTICE)
    include_private = os.getenv("HIKARI_BRAIN_V2_CONFLICTS_PRIVATE", "0") == "1"
    reports = scan_conflicts(accepted, neural_summary)
    for line in format_conflict_report_lines(reports, redact=not include_private):
        print(line)
    if reports and not include_private:
        print(
            "Conflict statements redacted by default. "
            "Set HIKARI_BRAIN_V2_CONFLICTS_PRIVATE=1 locally for read-only private review "
            "(never mutates databases)."
        )
    return 1 if reports else 0
