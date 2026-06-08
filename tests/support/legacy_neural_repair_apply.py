"""Test-only neural repair apply helpers (not imported by production runtime)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.brain_v2.legacy_neural_repair import (
    NeuralRepairAuditEntry,
    _read_restore_snapshot_rows,
    _verify_backup_matches_plan,
    _verify_sqlite_db,
    assert_repair_target_neural_path,
)
from core.brain_v2.legacy_reconciliation import (
    NeuralFactRow,
    compute_restore_snapshot_fingerprint,
    neural_row_fingerprint,
    resolve_row_for_opaque_target,
)
from core.brain_v2.legacy_repair_types import (
    PLAN_STATUS_APPLIED,
    REPAIR_CONFIRM_TOKEN,
    RepairPlanItem,
)


def apply_neural_repair_item_for_tests(
    *,
    neural_db_path: Path,
    backup_path: Path,
    item: RepairPlanItem,
    confirm_token: str,
) -> NeuralRepairAuditEntry:
    """Archive one legacy neural node on an explicit copied DB (tests only)."""
    if confirm_token != REPAIR_CONFIRM_TOKEN:
        raise ValueError(f"confirm token must be exactly {REPAIR_CONFIRM_TOKEN}")
    if item.action != "archive_neural":
        raise ValueError(
            f"neural mutation accepts only archive_neural, got {item.action!r}"
        )
    if not item.neural_target_id:
        raise ValueError("repair item has no neural target id")
    if not item.target_fingerprint or not item.plan_source_db_fingerprint:
        raise ValueError(
            "repair item missing approved target or source database fingerprint"
        )

    assert_repair_target_neural_path(neural_db_path)
    _verify_sqlite_db(neural_db_path)
    if backup_path.resolve() == neural_db_path.resolve():
        raise ValueError("backup path must be a separate copy, not the repair target itself")
    _verify_sqlite_db(backup_path)
    _verify_backup_matches_plan(
        backup_path,
        item=item,
        expected_target_fingerprint=item.target_fingerprint,
    )

    repair_rows = _read_restore_snapshot_rows(neural_db_path)
    repair_fp = compute_restore_snapshot_fingerprint(repair_rows)
    if repair_fp != item.plan_source_db_fingerprint:
        raise ValueError(
            "repair database restore snapshot does not match the approved repair plan source"
        )

    target_row = resolve_row_for_opaque_target(repair_rows, item.neural_target_id)
    if not target_row:
        raise ValueError(
            f"neural target not found or already inactive: {item.neural_target_id}"
        )
    if neural_row_fingerprint(target_row) != item.target_fingerprint:
        raise ValueError(
            "approved neural target no longer matches plan fingerprint; refusing mutation"
        )

    node_id = target_row.node_id
    conn = sqlite3.connect(neural_db_path, timeout=10.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, node_type, name, content, is_archived FROM nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"neural node missing: {item.neural_target_id}")
        live_row = NeuralFactRow(
            node_id=int(row[0]),
            node_type=str(row[1] or ""),
            name=str(row[2] or ""),
            content=str(row[3] or ""),
        )
        if neural_row_fingerprint(live_row) != item.target_fingerprint:
            raise ValueError(
                "target row changed since repair plan approval; refusing mutation"
            )
        if int(row[4]) != 0:
            raise ValueError(f"neural node already archived: {item.neural_target_id}")
        table_cols = {
            col_row[1] for col_row in conn.execute("PRAGMA table_info(nodes)").fetchall()
        }
        if "updated_at" in table_cols:
            conn.execute(
                "UPDATE nodes SET is_archived = 1, updated_at = datetime('now') WHERE id = ?",
                (node_id,),
            )
        else:
            conn.execute(
                "UPDATE nodes SET is_archived = 1 WHERE id = ?",
                (node_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return NeuralRepairAuditEntry(
        plan_item_id=item.plan_id,
        neural_target_id=item.neural_target_id,
        action="archive_neural",
        status=PLAN_STATUS_APPLIED,
    )
