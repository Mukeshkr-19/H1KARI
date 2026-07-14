"""Apply legacy neural repair only on explicit copied databases (never live/source)."""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.brain_v2.legacy_reconciliation import (
    NeuralFactRow,
    compute_restore_snapshot_fingerprint,
    neural_row_fingerprint,
    read_all_active_neural_fact_rows,
    read_all_neural_rows_for_restore_snapshot,
    resolve_neural_db_path,
    resolve_row_for_opaque_target,
)
from core.brain_v2.legacy_repair_types import (
    PLAN_STATUS_APPLIED,
    REPAIR_CONFIRM_TOKEN,
    RepairPlanItem,
)


@dataclass(frozen=True)
class NeuralRepairAuditEntry:
    plan_item_id: str
    neural_target_id: str
    action: str
    status: str


def canonical_live_neural_db_path() -> Path:
    """Default live neural DB path (independent of HIKARI_NEURAL_MEMORY_DB)."""
    from core.path_literals import HIKARI_MEMORY_DB
    from core.runtime_paths import hikari_home

    return hikari_home() / "brain" / HIKARI_MEMORY_DB


def configured_neural_source_path() -> Optional[Path]:
    """Currently configured neural source DB (env override or HOME default)."""
    return resolve_neural_db_path()


def is_canonical_live_neural_path(neural_db_path: Path) -> bool:
    canonical = canonical_live_neural_db_path()
    if not canonical.is_file():
        return False
    return neural_db_path.expanduser().resolve() == canonical.resolve()


def is_configured_neural_source_path(neural_db_path: Path) -> bool:
    source = configured_neural_source_path()
    if not source:
        return False
    return neural_db_path.expanduser().resolve() == source.resolve()


def assert_repair_target_neural_path(neural_db_path: Path) -> None:
    """Refuse canonical live path and configured neural source path for mutation."""
    if is_canonical_live_neural_path(neural_db_path):
        raise ValueError(
            "Refusing to repair the canonical live neural database path. "
            "Provide a separately copied repair target database."
        )
    if is_configured_neural_source_path(neural_db_path):
        raise ValueError(
            "Refusing to repair the configured neural source database path. "
            "Provide a separately copied repair target database."
        )


# Backward-compatible alias used by older tests/imports.
assert_non_live_neural_target = assert_repair_target_neural_path


def _verify_sqlite_db(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"Neural database not found: {path}")
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nodes'"
        ).fetchone()
        if not row:
            raise ValueError(
                "Database is not a legacy neural memory schema (missing nodes table)."
            )
    finally:
        conn.close()


def _read_active_rows(db_path: Path) -> List[NeuralFactRow]:
    return read_all_active_neural_fact_rows(db_path)


def _read_restore_snapshot_rows(db_path: Path) -> List[NeuralFactRow]:
    return read_all_neural_rows_for_restore_snapshot(db_path)


def _verify_backup_matches_plan(
    backup_path: Path,
    *,
    item: RepairPlanItem,
    expected_target_fingerprint: str,
) -> None:
    if not item.plan_source_db_fingerprint:
        raise ValueError("repair plan item missing approved source database fingerprint")
    backup_rows = _read_restore_snapshot_rows(backup_path)
    if not backup_rows:
        raise ValueError(
            "backup is not a valid pre-mutation restore snapshot (no neural rows)"
        )
    backup_fp = compute_restore_snapshot_fingerprint(backup_rows)
    if backup_fp != item.plan_source_db_fingerprint:
        raise ValueError(
            "backup restore snapshot does not match the approved repair plan source "
            "(includes active and archived row lifecycle state)"
        )
    target_row = resolve_row_for_opaque_target(backup_rows, item.neural_target_id or "")
    if not target_row:
        raise ValueError("backup snapshot is missing the approved repair target row")
    if neural_row_fingerprint(target_row) != expected_target_fingerprint:
        raise ValueError(
            "backup target row fingerprint does not match the approved repair plan"
        )


def resolve_node_id_for_target(neural_db_path: Path, neural_target_id: str) -> Optional[int]:
    """Map opaque target id back to integer node id (read-only scan)."""
    rows = _read_active_rows(neural_db_path)
    row = resolve_row_for_opaque_target(rows, neural_target_id)
    return row.node_id if row else None


def apply_neural_repair_item(
    *,
    neural_db_path: Path,
    backup_path: Path,
    item: RepairPlanItem,
    confirm_token: str,
) -> NeuralRepairAuditEntry:
    """Not shipped: neural repair apply is read-only reconciliation/plan in this release."""
    del neural_db_path, backup_path, item, confirm_token
    raise NotImplementedError(
        "Neural repair apply is not implemented in this release. "
        "Use read-only --brain-v2-reconcile-status and --brain-v2-repair-plan only."
    )


def copy_neural_db_for_repair(source: Path, dest: Path) -> Path:
    """Not shipped; use tests.support.neural_repair_utils.copy_neural_db_for_repair."""
    del source, dest
    raise NotImplementedError(
        "copy_neural_db_for_repair is test-support only in this release."
    )
