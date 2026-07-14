"""Read-only Brain v2 vs legacy neural reconciliation and repair planning."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.brain_v2.conflicts import (
    CONFLICT_PARTNER_EDUCATION_ON_USER,
    CONFLICT_STALE_CURRENTLY_IN,
    CONFLICT_STALE_HOME,
    CONFLICT_UNREVIEWED_LEGACY_HOME,
    ConflictReport,
    scan_conflicts,
)
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_lifecycle import lifecycle_status
from core.brain_v2.profile_summary import _is_partner_education_memory, _norm_place
from core.brain_v2.schemas import SourceLinkedMemory

from core.brain_v2.legacy_repair_types import (
    PLAN_STATUS_NOT_APPLIED,
    REPAIR_CONFIRM_TOKEN,
    RepairPlan,
    RepairPlanItem,
)

CATEGORY_STALE_STABLE_LOCATION = "stale_stable_location"
CATEGORY_STALE_CURRENT_LOCATION = "stale_current_location"
CATEGORY_WRONG_RELATION_PERSON = "wrong_relation_person_association"
CATEGORY_OWNER_EDUCATION_MISATTRIBUTED = "person_education_attributed_to_owner"
CATEGORY_SUPERSEDED_REVIEWED_FACT = "superseded_reviewed_fact"
CATEGORY_LEGACY_ONLY_MANUAL = "unresolved_legacy_only_fact"

REDACTED_STATEMENT = "[redacted]"
NEURAL_REPORTING_PREVIEW_LIMIT = 300

_PARTNER_EDUCATION_MARKERS = (
    "partner",
    "girlfriend",
    "boyfriend",
    "wife",
    "husband",
    "person b",
)


def opaque_neural_target_id(node_id: int, node_type: str) -> str:
    """Stable opaque identifier for a legacy neural row (no statement text)."""
    digest = hashlib.sha256(f"neural-node:{node_id}:{node_type}".encode()).hexdigest()[:16]
    return f"neural-{digest}"


@dataclass(frozen=True)
class NeuralFactRow:
    node_id: int
    node_type: str
    name: str
    content: str
    is_archived: int = 0

    @property
    def opaque_target_id(self) -> str:
        return opaque_neural_target_id(self.node_id, self.node_type)


def _is_partner_described_neural_row(row: NeuralFactRow) -> bool:
    """True when row text describes a partner's education, not the owner's."""
    blob = f"{row.name} {row.content}".lower()
    return any(marker in blob for marker in _PARTNER_EDUCATION_MARKERS)


def is_proven_owner_attributed_education_row(row: NeuralFactRow) -> bool:
    """True only when legacy row text proves owner-profile education misattribution."""
    if row.node_type != "FACT":
        return False
    low = (row.content or "").lower()
    if "education" not in low and "student" not in low:
        return False
    if _is_partner_described_neural_row(row):
        return False
    if re.search(r"\bowner\s+a\b", low):
        return True
    if "incorrect owner" in low or "on user profile" in low or "attributed to owner" in low:
        return True
    if re.search(r"\bi\s+(?:study|studied|am a student)\b", low):
        return True
    return False


def neural_row_fingerprint(row: NeuralFactRow) -> str:
    """Non-public fingerprint binding a repair target to an approved active row."""
    payload = (
        f"{row.node_id}|{row.node_type}|{row.name}|{row.content}|{row.is_archived}"
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def restore_snapshot_row_fingerprint(row: NeuralFactRow) -> str:
    """Fingerprint for full restore snapshot rows (active + archived lifecycle)."""
    return neural_row_fingerprint(row)


def compute_db_snapshot_fingerprint(rows: List[NeuralFactRow]) -> str:
    """Fingerprint of active neural rows (repair target binding)."""
    parts = sorted(f"{r.node_id}:{neural_row_fingerprint(r)}" for r in rows)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


def compute_restore_snapshot_fingerprint(rows: List[NeuralFactRow]) -> str:
    """Fingerprint of all neural rows including archived lifecycle state."""
    parts = sorted(f"{r.node_id}:{restore_snapshot_row_fingerprint(r)}" for r in rows)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


def resolve_row_for_opaque_target(
    rows: List[NeuralFactRow], neural_target_id: str
) -> Optional[NeuralFactRow]:
    for row in rows:
        if row.opaque_target_id == neural_target_id:
            return row
    return None


@dataclass
class ReconciliationFinding:
    category: str
    brain_v2_memory_id: Optional[str]
    neural_node_type: Optional[str]
    neural_name: Optional[str]
    neural_target_id: Optional[str]
    recommended_action: str
    statement_redacted: bool = True
    details: Dict[str, Any] = field(default_factory=dict)


def resolve_neural_db_path() -> Optional[Path]:
    """Resolve neural DB path without initializing neural memory runtime."""
    from core.path_literals import HIKARI_MEMORY_DB
    from core.runtime_paths import hikari_home

    explicit = os.environ.get("HIKARI_NEURAL_MEMORY_DB")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    candidate = hikari_home() / "brain" / HIKARI_MEMORY_DB
    return candidate if candidate.is_file() else None


def read_neural_fact_rows(db_path: Path, *, limit: Optional[int] = 300) -> List[NeuralFactRow]:
    """Strict read-only sqlite open; never calls neural memory init."""
    if not db_path.is_file():
        return []
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        if limit is None:
            rows = conn.execute(
                """
                SELECT id, node_type, name, content
                FROM nodes
                WHERE is_archived = 0
                ORDER BY id ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, node_type, name, content
                FROM nodes
                WHERE is_archived = 0
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    return [
        NeuralFactRow(
            node_id=int(r["id"]),
            node_type=str(r["node_type"] or ""),
            name=str(r["name"] or ""),
            content=str(r["content"] or ""),
            is_archived=0,
        )
        for r in rows
    ]


def read_all_active_neural_fact_rows(db_path: Path) -> List[NeuralFactRow]:
    """Full active-row scan for sign-off conflict detection and repair targets."""
    return read_neural_fact_rows(db_path, limit=None)


def read_all_neural_rows_for_restore_snapshot(db_path: Path) -> List[NeuralFactRow]:
    """All rows (active + archived) for restore-grade backup/repair fingerprinting."""
    if not db_path.is_file():
        return []
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, node_type, name, content, COALESCE(is_archived, 0) AS is_archived
            FROM nodes
            ORDER BY id ASC
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        NeuralFactRow(
            node_id=int(r["id"]),
            node_type=str(r["node_type"] or ""),
            name=str(r["name"] or ""),
            content=str(r["content"] or ""),
            is_archived=int(r["is_archived"]),
        )
        for r in rows
    ]


def read_neural_facts_readonly(db_path: Path, *, limit: int = 300) -> List[NeuralFactRow]:
    return read_neural_fact_rows(db_path, limit=limit)


def build_neural_summary_lines(
    rows: List[NeuralFactRow], *, preview_limit: Optional[int] = None
) -> str:
    """Build profile lines for conflict detection (full rows) or bounded preview display."""
    ordered = rows
    if preview_limit is not None and len(rows) > preview_limit:
        ordered = sorted(rows, key=lambda r: r.node_id, reverse=True)[:preview_limit]
    lines = ["What I know about you:"]
    for fact in ordered:
        if fact.node_type == "LOCATION":
            lines.append(f"- Home: {fact.name}")
        elif fact.node_type == "FACT":
            low = fact.content.lower()
            if "currently in" in low or "visiting" in low:
                lines.append(f"- Currently in: {fact.name}")
            elif "education" in low or "student" in low:
                if is_proven_owner_attributed_education_row(fact):
                    lines.append(f"- Education: {fact.name}")
            else:
                lines.append(f"- {fact.name}: {fact.content[:80]}")
    return "\n".join(lines)


def fetch_neural_summary_readonly() -> tuple[Optional[str], bool]:
    """Internal read-only neural profile summary for conflict detection only."""
    neural_path = resolve_neural_db_path()
    if not neural_path:
        return None, False
    rows = read_all_active_neural_fact_rows(neural_path)
    if not rows:
        return None, False
    return build_neural_summary_lines(rows), True


def _education_rows_for_school(school: str, rows: List[NeuralFactRow]) -> List[NeuralFactRow]:
    matches: List[NeuralFactRow] = []
    for row in rows:
        low = row.content.lower()
        if row.node_type == "FACT" and (
            "education" in low or "student" in low
        ) and _norm_place(row.name) == school:
            matches.append(row)
    return matches


def match_neural_target_for_line(
    conflicting_line: str, rows: List[NeuralFactRow]
) -> Optional[str]:
    stripped = (conflicting_line or "").strip()
    home_match = re.match(r"-\s*Home:\s*(.+)", stripped, re.I)
    if home_match:
        place = _norm_place(home_match.group(1))
        for row in rows:
            if row.node_type == "LOCATION" and _norm_place(row.name) == place:
                return row.opaque_target_id
    current_match = re.match(r"-\s*Currently\s+in:\s*(.+)", stripped, re.I)
    if current_match:
        place = _norm_place(current_match.group(1))
        for row in rows:
            low = row.content.lower()
            if row.node_type == "FACT" and (
                "currently in" in low or "visiting" in low
            ) and _norm_place(row.name) == place:
                return row.opaque_target_id
    edu_match = re.match(r"-\s*Education:\s*(.+)", stripped, re.I)
    if edu_match:
        school = _norm_place(edu_match.group(1))
        matches = [
            row
            for row in _education_rows_for_school(school, rows)
            if is_proven_owner_attributed_education_row(row)
        ]
        if len(matches) == 1:
            return matches[0].opaque_target_id
        return None
    return None


def attach_neural_targets(
    reports: List[ConflictReport], rows: List[NeuralFactRow]
) -> List[ConflictReport]:
    enriched: List[ConflictReport] = []
    for report in reports:
        target = match_neural_target_for_line(report.conflicting_line, rows)
        enriched.append(
            ConflictReport(
                conflict_type=report.conflict_type,
                reviewed_statement=report.reviewed_statement,
                conflicting_line=report.conflicting_line,
                recommended_action=report.recommended_action,
                neural_target_id=target,
            )
        )
    return enriched


def audit_reconciliation(
    store: EpisodeStore,
    *,
    include_statements: bool = False,
    neural_db_path: Optional[Path] = None,
) -> List[ReconciliationFinding]:
    active = store.get_active_accepted_memories(limit=500)
    all_accepted = store.get_accepted_memories(limit=500)
    findings: List[ReconciliationFinding] = []

    for mem in all_accepted:
        status = lifecycle_status(mem.metadata)
        if status == "superseded":
            findings.append(
                ReconciliationFinding(
                    category=CATEGORY_SUPERSEDED_REVIEWED_FACT,
                    brain_v2_memory_id=mem.memory_id,
                    neural_node_type=None,
                    neural_name=None,
                    neural_target_id=None,
                    recommended_action="ignore_superseded_history",
                    statement_redacted=not include_statements,
                    details={
                        "lifecycle_status": status,
                        "statement": mem.statement if include_statements else REDACTED_STATEMENT,
                    },
                )
            )

    neural_path = neural_db_path or resolve_neural_db_path()
    neural_rows: List[NeuralFactRow] = []
    if neural_path:
        neural_rows = read_all_active_neural_fact_rows(neural_path)
    summary = build_neural_summary_lines(neural_rows) if neural_rows else ""
    conflict_reports = scan_conflicts(active, summary if summary.strip() else None)
    conflict_reports = attach_neural_targets(conflict_reports, neural_rows)

    for report in conflict_reports:
        category = CATEGORY_LEGACY_ONLY_MANUAL
        if report.conflict_type == CONFLICT_STALE_HOME:
            category = CATEGORY_STALE_STABLE_LOCATION
        elif report.conflict_type == CONFLICT_UNREVIEWED_LEGACY_HOME:
            category = CATEGORY_LEGACY_ONLY_MANUAL
        elif report.conflict_type == CONFLICT_STALE_CURRENTLY_IN:
            category = CATEGORY_STALE_CURRENT_LOCATION
        elif report.conflict_type == CONFLICT_PARTNER_EDUCATION_ON_USER:
            category = CATEGORY_OWNER_EDUCATION_MISATTRIBUTED
        neural_target_id = report.neural_target_id
        if report.conflict_type in (
            CONFLICT_UNREVIEWED_LEGACY_HOME,
            CONFLICT_STALE_CURRENTLY_IN,
        ) or report.recommended_action in ("review", "ignore"):
            neural_target_id = None
        findings.append(
            ReconciliationFinding(
                category=category,
                brain_v2_memory_id=None,
                neural_node_type="FACT",
                neural_name=None,
                neural_target_id=neural_target_id,
                recommended_action=report.recommended_action,
                statement_redacted=not include_statements,
                details={
                    "conflict_type": report.conflict_type,
                    "reviewed_statement": (
                        report.reviewed_statement
                        if include_statements
                        else REDACTED_STATEMENT
                    ),
                    "conflicting_line": (
                        report.conflicting_line
                        if include_statements
                        else REDACTED_STATEMENT
                    ),
                },
            )
        )

    if neural_rows and not active:
        findings.append(
            ReconciliationFinding(
                category=CATEGORY_LEGACY_ONLY_MANUAL,
                brain_v2_memory_id=None,
                neural_node_type=None,
                neural_name=None,
                neural_target_id=None,
                recommended_action="manual_review",
                statement_redacted=True,
                details={"neural_fact_count": len(neural_rows)},
            )
        )

    return findings


def build_repair_plan(
    findings: List[ReconciliationFinding],
    *,
    neural_db_path: Optional[Path] = None,
) -> RepairPlan:
    plan_id = str(uuid.uuid4())
    neural_rows = (
        read_all_active_neural_fact_rows(neural_db_path)
        if neural_db_path and neural_db_path.is_file()
        else []
    )
    restore_rows = (
        read_all_neural_rows_for_restore_snapshot(neural_db_path)
        if neural_db_path and neural_db_path.is_file()
        else []
    )
    source_db_fingerprint = (
        compute_restore_snapshot_fingerprint(restore_rows) if restore_rows else None
    )
    items: List[RepairPlanItem] = []
    for idx, finding in enumerate(findings, start=1):
        if finding.category == CATEGORY_SUPERSEDED_REVIEWED_FACT:
            continue
        action = "manual_review"
        plan_neural_target_id: Optional[str] = None
        target_fingerprint = None
        if (
            finding.recommended_action == "clean manually"
            and finding.neural_target_id
            and finding.category
            in (CATEGORY_STALE_STABLE_LOCATION, CATEGORY_OWNER_EDUCATION_MISATTRIBUTED)
        ):
            action = "archive_neural"
            plan_neural_target_id = finding.neural_target_id
            if neural_rows:
                row = resolve_row_for_opaque_target(neural_rows, plan_neural_target_id)
                if row:
                    target_fingerprint = neural_row_fingerprint(row)
        elif finding.recommended_action in ("clean manually", "review") and finding.brain_v2_memory_id:
            action = "supersede_or_retire"
        items.append(
            RepairPlanItem(
                plan_id=f"{plan_id[:8]}-{idx}",
                category=finding.category,
                action=action,
                target_memory_id=finding.brain_v2_memory_id,
                neural_target_id=plan_neural_target_id,
                target_fingerprint=target_fingerprint,
                plan_source_db_fingerprint=source_db_fingerprint,
                status=PLAN_STATUS_NOT_APPLIED,
            )
        )
    return RepairPlan(
        plan_id=plan_id,
        items=items,
        source_db_fingerprint=source_db_fingerprint,
    )


def format_reconciliation_lines(
    findings: List[ReconciliationFinding], *, redact: bool = True
) -> List[str]:
    if not findings:
        return ["Brain v2 reconciliation: no findings"]
    lines = [f"Brain v2 reconciliation: {len(findings)} finding(s)"]
    for idx, finding in enumerate(findings, start=1):
        lines.append(
            f"  {idx}. [{finding.category}] action={finding.recommended_action} "
            f"memory={finding.brain_v2_memory_id or '—'} "
            f"neural_target={finding.neural_target_id or '—'}"
        )
        if not redact and finding.details.get("statement"):
            lines.append(f"     detail: {str(finding.details.get('statement'))[:80]}")
    return lines


def apply_repair_item(
    store: EpisodeStore,
    item: RepairPlanItem,
    *,
    backup_path: Path,
    confirm_token: str,
    neural_db_path: Optional[Path] = None,
    supersede_statement: Optional[str] = None,
) -> str:
    """Not implemented in this release — read-only reconciliation/plan only."""
    del store, item, backup_path, confirm_token, neural_db_path, supersede_statement
    raise NotImplementedError(
        "Neural repair apply is not implemented in this release. Use read-only "
        "--brain-v2-reconcile-status and --brain-v2-repair-plan; copy-only repair "
        "helpers remain in legacy_neural_repair for private temp-DB tests only."
    )


def run_reconcile_status(*, include_statements: bool = False) -> int:
    from core.brain_v2.db_paths import open_readonly_episode_store

    store = open_readonly_episode_store()
    findings = audit_reconciliation(store, include_statements=include_statements)
    for line in format_reconciliation_lines(findings, redact=not include_statements):
        print(line)
    return 1 if findings else 0


def run_repair_plan() -> int:
    from core.brain_v2.db_paths import open_readonly_episode_store

    store = open_readonly_episode_store()
    findings = audit_reconciliation(store, include_statements=False)
    neural_path = resolve_neural_db_path()
    plan = build_repair_plan(findings, neural_db_path=neural_path)
    print(f"Repair plan: {plan.plan_id}")
    if not plan.items:
        print("  (no actionable items)")
        return 0
    for item in plan.items:
        print(
            f"  - {item.plan_id} category={item.category} action={item.action} "
            f"memory={item.target_memory_id or '—'} "
            f"neural_target={item.neural_target_id or '—'} "
            f"status={item.status} requires_backup=yes "
            f"confirm={item.requires_confirm_token}"
        )
    print(
        "Optional migration only: repair executor works on copied DBs in tests/library. "
        "Runtime personal recall uses Brain v2 authority + legacy quarantine, not live repair. "
        "Apply requires a separate copy path, restore snapshot backup, and confirm token REPAIR."
    )
    return 0
