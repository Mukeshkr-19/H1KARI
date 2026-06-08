"""Legacy neural reconciliation — read-only audit and plan-only repair."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from core.brain_v2.consolidation_pipeline import EpisodeConsolidationPipeline
from core.brain_v2.db_paths import open_readonly_episode_store
from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.legacy_neural_repair import (
    apply_neural_repair_item,
    assert_repair_target_neural_path,
    canonical_live_neural_db_path,
    is_canonical_live_neural_path,
    is_configured_neural_source_path,
)
from tests.support.legacy_neural_repair_apply import apply_neural_repair_item_for_tests
from core.brain_v2.legacy_reconciliation import (
    CATEGORY_STALE_STABLE_LOCATION,
    REDACTED_STATEMENT,
    RepairPlanItem,
    apply_repair_item,
    audit_reconciliation,
    build_repair_plan,
    build_neural_summary_lines,
    format_reconciliation_lines,
    is_proven_owner_attributed_education_row,
    read_all_active_neural_fact_rows,
    read_neural_fact_rows,
    resolve_neural_db_path,
    run_reconcile_status,
)
from core.brain_v2.memory_review_gate import MemoryReviewGate
from core.path_literals import DOT_HIKARI, EPISODES_DB, HIKARI_MEMORY_DB
from tests.privacy_scan import REPO_ROOT


def test_apply_neural_repair_item_not_implemented_in_release(tmp_path):
    item = RepairPlanItem(
        plan_id="p0",
        category="test",
        action="archive_neural",
        neural_target_id="n0",
        status="not_applied",
    )
    with pytest.raises(NotImplementedError, match="not implemented"):
        apply_neural_repair_item(
            neural_db_path=tmp_path / "copy.db",
            backup_path=tmp_path / "backup.db",
            item=item,
            confirm_token="REPAIR",
        )


def test_apply_repair_item_not_implemented_in_release(tmp_path):
    store = EpisodeStore(db_path=tmp_path / "no_apply.db")
    item = RepairPlanItem(
        plan_id="p1",
        category="test",
        action="archive_neural",
        neural_target_id="n1",
        status="not_applied",
    )
    with pytest.raises(NotImplementedError, match="not implemented"):
        apply_repair_item(
            store,
            item,
            backup_path=tmp_path / "backup.db",
            confirm_token="REPAIR",
            neural_db_path=tmp_path / "copy.db",
        )


def _seed_brain_store(store: EpisodeStore) -> str:
    episode_id = store.create_episode("reconcile-ep")
    store.add_turn(episode_id, "Remember this: I live in City A.", is_user=True)
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    linked = MemoryReviewGate(store).accept(candidates[0].candidate_id)
    return linked.memory_id


def _seed_fake_neural_db(path: Path) -> None:
    schema = """
    CREATE TABLE nodes (
        id INTEGER PRIMARY KEY,
        node_type TEXT NOT NULL,
        name TEXT NOT NULL,
        content TEXT,
        metadata TEXT,
        salience REAL DEFAULT 0.5,
        activation_count INTEGER DEFAULT 0,
        last_accessed TEXT,
        created_at TEXT,
        updated_at TEXT,
        user_id TEXT DEFAULT 'local_user',
        is_archived INTEGER DEFAULT 0,
        is_pinned INTEGER DEFAULT 0
    );
    """
    with sqlite3.connect(path) as conn:
        conn.executescript(schema)
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("LOCATION", "City C", "legacy home in City C"),
        )
        conn.commit()


def test_read_only_neural_open_does_not_create_home_hikari(tmp_path, monkeypatch):
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    facts = read_neural_fact_rows(neural_db)
    assert len(facts) == 1
    assert facts[0].opaque_target_id.startswith("neural-")
    assert not (empty_home / DOT_HIKARI).exists()


def test_reconciliation_redacts_fake_neural_content(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(neural_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _seed_brain_store(store)
    findings = audit_reconciliation(store, include_statements=False, neural_db_path=neural_db)
    assert findings
    assert any(f.category == CATEGORY_STALE_STABLE_LOCATION for f in findings)
    for finding in findings:
        assert finding.statement_redacted
        detail_blob = " ".join(str(v) for v in finding.details.values())
        assert REDACTED_STATEMENT in detail_blob
        assert "legacy home in City C" not in detail_blob
        assert "I live in City A" not in detail_blob
    lines = format_reconciliation_lines(findings, redact=True)
    blob = "\n".join(lines)
    assert CATEGORY_STALE_STABLE_LOCATION in blob
    assert "legacy home in City C" not in blob
    assert "I live in City A" not in blob


def test_repair_plan_includes_opaque_neural_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _seed_brain_store(store)
    findings = audit_reconciliation(store, neural_db_path=neural_db)
    plan = build_repair_plan(findings, neural_db_path=neural_db)
    archive_items = [i for i in plan.items if i.action == "archive_neural"]
    assert archive_items
    assert all(i.neural_target_id and i.neural_target_id.startswith("neural-") for i in archive_items)


def test_apply_neural_repair_archives_on_copy_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _seed_brain_store(store)
    findings = audit_reconciliation(store, neural_db_path=neural_db)
    plan = build_repair_plan(findings, neural_db_path=neural_db)
    item = next(i for i in plan.items if i.neural_target_id)
    assert item.target_fingerprint
    assert item.plan_source_db_fingerprint
    repair_db = tmp_path / "repair-copy.db"
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(neural_db, repair_db)
    shutil.copy2(neural_db, backup_db)
    apply_neural_repair_item_for_tests(
        neural_db_path=repair_db,
        backup_path=backup_db,
        item=item,
        confirm_token="REPAIR",
    )
    with sqlite3.connect(repair_db) as conn:
        row = conn.execute("SELECT is_archived FROM nodes WHERE id = 1").fetchone()
    assert row and int(row[0]) == 1


def test_apply_neural_repair_refuses_canonical_live_path(tmp_path, monkeypatch):
    copy_db = tmp_path / "operator-copy.db"
    _seed_fake_neural_db(copy_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(copy_db))
    live_db = tmp_path / DOT_HIKARI / "brain" / HIKARI_MEMORY_DB
    live_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(copy_db, live_db)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert is_canonical_live_neural_path(live_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    rows = read_neural_fact_rows(copy_db)
    from core.brain_v2.legacy_reconciliation import (
        compute_restore_snapshot_fingerprint,
        neural_row_fingerprint,
        read_all_neural_rows_for_restore_snapshot,
    )

    item = RepairPlanItem(
        plan_id="test-1",
        category=CATEGORY_STALE_STABLE_LOCATION,
        action="archive_neural",
        neural_target_id=rows[0].opaque_target_id,
        target_fingerprint=neural_row_fingerprint(rows[0]),
        plan_source_db_fingerprint=compute_restore_snapshot_fingerprint(
            read_all_neural_rows_for_restore_snapshot(copy_db)
        ),
    )
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(copy_db, backup_db)
    with pytest.raises(ValueError, match="Refusing"):
        apply_neural_repair_item_for_tests(
            neural_db_path=live_db,
            backup_path=backup_db,
            item=item,
            confirm_token="REPAIR",
        )


def test_repair_refuses_configured_neural_source_path(tmp_path, monkeypatch):
    source_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(source_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(source_db))
    assert is_configured_neural_source_path(source_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    item = _plan_item_for_db(source_db)
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(source_db, backup_db)
    with pytest.raises(ValueError, match="configured neural source"):
        apply_neural_repair_item_for_tests(
            neural_db_path=source_db,
            backup_path=backup_db,
            item=item,
            confirm_token="REPAIR",
        )


def test_repair_succeeds_on_designated_copy_not_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    source_db = tmp_path / "source.db"
    _seed_fake_neural_db(source_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(source_db))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _seed_brain_store(store)
    findings = audit_reconciliation(store, neural_db_path=source_db)
    plan = build_repair_plan(findings, neural_db_path=source_db)
    item = next(i for i in plan.items if i.action == "archive_neural")
    repair_db = tmp_path / "designated-repair-copy.db"
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(source_db, repair_db)
    shutil.copy2(source_db, backup_db)
    apply_neural_repair_item_for_tests(
        neural_db_path=repair_db,
        backup_path=backup_db,
        item=item,
        confirm_token="REPAIR",
    )
    with sqlite3.connect(repair_db) as conn:
        row = conn.execute("SELECT is_archived FROM nodes WHERE id = 1").fetchone()
    assert row and int(row[0]) == 1


def test_assert_repair_target_refuses_canonical_and_configured_paths(tmp_path, monkeypatch):
    source_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(source_db)
    monkeypatch.setenv("HIKARI_NEURAL_MEMORY_DB", str(source_db))
    with pytest.raises(ValueError, match="configured neural source"):
        assert_repair_target_neural_path(source_db)
    live_db = tmp_path / DOT_HIKARI / "brain" / HIKARI_MEMORY_DB
    live_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_db, live_db)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="canonical live"):
        assert_repair_target_neural_path(live_db)
    assert canonical_live_neural_db_path() == live_db


def test_apply_neural_repair_wrong_token_refuses(tmp_path, monkeypatch):
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    repair_db = tmp_path / "repair-copy.db"
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(neural_db, repair_db)
    shutil.copy2(neural_db, backup_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    rows = read_neural_fact_rows(neural_db)
    from core.brain_v2.legacy_reconciliation import (
        compute_restore_snapshot_fingerprint,
        neural_row_fingerprint,
        read_all_neural_rows_for_restore_snapshot,
    )

    item = RepairPlanItem(
        plan_id="test-2",
        category=CATEGORY_STALE_STABLE_LOCATION,
        action="archive_neural",
        neural_target_id=rows[0].opaque_target_id,
        target_fingerprint=neural_row_fingerprint(rows[0]),
        plan_source_db_fingerprint=compute_restore_snapshot_fingerprint(
            read_all_neural_rows_for_restore_snapshot(neural_db)
        ),
    )
    with pytest.raises(ValueError, match="confirm token"):
        apply_neural_repair_item_for_tests(
            neural_db_path=repair_db,
            backup_path=backup_db,
            item=item,
            confirm_token="WRONG",
        )


def test_readonly_reporting_does_not_mutate_existing_db_mtime(tmp_path, monkeypatch):
    db_path = tmp_path / EPISODES_DB
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(db_path))
    store = EpisodeStore(db_path=db_path)
    _seed_brain_store(store)
    mtime_before = db_path.stat().st_mtime_ns
    with sqlite3.connect(db_path) as conn:
        tables_before = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    readonly = open_readonly_episode_store()
    audit_reconciliation(readonly, include_statements=False, neural_db_path=None)
    build_repair_plan([])
    mtime_after = db_path.stat().st_mtime_ns
    assert mtime_after == mtime_before
    with sqlite3.connect(db_path) as conn:
        tables_after = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert tables_after == tables_before


def test_reconcile_status_subprocess_no_hikari_dir(tmp_path):
    home = tmp_path / "isolated-home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "HIKARI_BRAIN_V2_EPISODES_DB": str(tmp_path / "missing" / EPISODES_DB),
    }
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "hikari.py"), "--brain-v2-reconcile-status"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode in (0, 1)
    assert not (home / DOT_HIKARI).exists()


def test_reconcile_status_does_not_touch_live_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _seed_brain_store(store)
    findings = audit_reconciliation(store, neural_db_path=None)
    assert isinstance(findings, list)
    assert resolve_neural_db_path() is None or isinstance(resolve_neural_db_path(), Path)


def test_run_reconcile_status_uses_readonly_store(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _seed_brain_store(store)
    mtime_before = (tmp_path / EPISODES_DB).stat().st_mtime_ns
    code = run_reconcile_status(include_statements=False)
    assert (tmp_path / EPISODES_DB).stat().st_mtime_ns == mtime_before
    captured = capsys.readouterr()
    assert "reconciliation" in captured.out.lower() or code in (0, 1)


def _plan_item_for_db(neural_db: Path) -> RepairPlanItem:
    from core.brain_v2.legacy_reconciliation import (
        compute_restore_snapshot_fingerprint,
        neural_row_fingerprint,
        read_all_active_neural_fact_rows,
        read_all_neural_rows_for_restore_snapshot,
    )

    rows = read_all_active_neural_fact_rows(neural_db)
    restore_rows = read_all_neural_rows_for_restore_snapshot(neural_db)

    return RepairPlanItem(
        plan_id="fp-test",
        category=CATEGORY_STALE_STABLE_LOCATION,
        action="archive_neural",
        neural_target_id=rows[0].opaque_target_id,
        target_fingerprint=neural_row_fingerprint(rows[0]),
        plan_source_db_fingerprint=compute_restore_snapshot_fingerprint(restore_rows),
    )


def test_repair_plan_from_db_a_cannot_apply_on_unrelated_db_b(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    db_a = tmp_path / "db-a.db"
    db_b = tmp_path / "db-b.db"
    _seed_fake_neural_db(db_a)
    _seed_fake_neural_db(db_b)
    with sqlite3.connect(db_b) as conn:
        conn.execute("UPDATE nodes SET name = ?, content = ? WHERE id = 1", ("City D", "other db"))
        conn.commit()
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    item = _plan_item_for_db(db_a)
    repair_b = tmp_path / "repair-b.db"
    backup_b = tmp_path / "backup-b.db"
    shutil.copy2(db_b, repair_b)
    shutil.copy2(db_b, backup_b)
    with pytest.raises(ValueError, match="restore snapshot|snapshot does not match"):
        apply_neural_repair_item_for_tests(
            neural_db_path=repair_b,
            backup_path=backup_b,
            item=item,
            confirm_token="REPAIR",
        )


def test_repair_rejects_changed_target_row_after_planning(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    item = _plan_item_for_db(neural_db)
    repair_db = tmp_path / "repair-copy.db"
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(neural_db, repair_db)
    shutil.copy2(neural_db, backup_db)
    with sqlite3.connect(repair_db) as conn:
        conn.execute("UPDATE nodes SET content = ? WHERE id = 1", ("changed content",))
        conn.commit()
    with pytest.raises(ValueError, match="snapshot|fingerprint|target"):
        apply_neural_repair_item_for_tests(
            neural_db_path=repair_db,
            backup_path=backup_db,
            item=item,
            confirm_token="REPAIR",
        )


def test_repair_unrelated_backup_refuses_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    other_db = tmp_path / "other.db"
    _seed_fake_neural_db(neural_db)
    _seed_fake_neural_db(other_db)
    with sqlite3.connect(other_db) as conn:
        conn.execute("UPDATE nodes SET name = ?, content = ? WHERE id = 1", ("City D", "other db"))
        conn.commit()
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    item = _plan_item_for_db(neural_db)
    repair_db = tmp_path / "repair-copy.db"
    shutil.copy2(neural_db, repair_db)
    with pytest.raises(ValueError, match="backup|snapshot"):
        apply_neural_repair_item_for_tests(
            neural_db_path=repair_db,
            backup_path=other_db,
            item=item,
            confirm_token="REPAIR",
        )


def test_repair_backup_missing_target_refuses_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    item = _plan_item_for_db(neural_db)
    repair_db = tmp_path / "repair-copy.db"
    backup_db = tmp_path / "backup-empty.db"
    shutil.copy2(neural_db, repair_db)
    with sqlite3.connect(backup_db) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        conn.commit()
    with pytest.raises(ValueError, match="backup"):
        apply_neural_repair_item_for_tests(
            neural_db_path=repair_db,
            backup_path=backup_db,
            item=item,
            confirm_token="REPAIR",
        )


def test_repair_failed_mutation_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    item = _plan_item_for_db(neural_db)
    repair_db = tmp_path / "repair-copy.db"
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(neural_db, repair_db)
    shutil.copy2(neural_db, backup_db)
    with sqlite3.connect(repair_db) as conn:
        conn.execute("DROP TABLE nodes")
        conn.commit()
    with pytest.raises(Exception):
        apply_neural_repair_item_for_tests(
            neural_db_path=repair_db,
            backup_path=backup_db,
            item=item,
            confirm_token="REPAIR",
        )
    with sqlite3.connect(backup_db) as conn:
        archived = conn.execute(
            "SELECT is_archived FROM nodes WHERE id = 1"
        ).fetchone()
    assert archived and int(archived[0]) == 0


def _seed_partner_education_neural(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("FACT", "School A", "Partner Person B education at School A"),
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("FACT", "School B", "Owner A own education at School B"),
        )
        conn.commit()


def _seed_many_rows_plus_stale_home(path: Path, *, filler_count: int = 301) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("LOCATION", "City B", "legacy home in City B"),
        )
        for idx in range(2, filler_count + 2):
            conn.execute(
                "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
                ("FACT", f"Place {idx}", f"unrelated filler {idx}"),
            )
        conn.commit()


def test_unreviewed_legacy_home_has_no_archive_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    findings = audit_reconciliation(store, neural_db_path=neural_db)
    assert any(f.category == "unresolved_legacy_only_fact" for f in findings)
    assert not any(f.category == CATEGORY_STALE_STABLE_LOCATION for f in findings)
    plan = build_repair_plan(findings, neural_db_path=neural_db)
    assert not any(i.action == "archive_neural" for i in plan.items)
    assert not any(i.neural_target_id for i in plan.items)


def test_manual_review_plan_item_cannot_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    rows = read_neural_fact_rows(neural_db)
    from core.brain_v2.legacy_reconciliation import (
        compute_restore_snapshot_fingerprint,
        neural_row_fingerprint,
        read_all_neural_rows_for_restore_snapshot,
    )

    manual_item = RepairPlanItem(
        plan_id="manual-1",
        category="unresolved_legacy_only_fact",
        action="manual_review",
        neural_target_id=rows[0].opaque_target_id,
        target_fingerprint=neural_row_fingerprint(rows[0]),
        plan_source_db_fingerprint=compute_restore_snapshot_fingerprint(
            read_all_neural_rows_for_restore_snapshot(neural_db)
        ),
    )
    repair_db = tmp_path / "repair-copy.db"
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(neural_db, repair_db)
    shutil.copy2(neural_db, backup_db)
    with pytest.raises(ValueError, match="archive_neural"):
        apply_neural_repair_item_for_tests(
            neural_db_path=repair_db,
            backup_path=backup_db,
            item=manual_item,
            confirm_token="REPAIR",
        )


def test_supersede_or_retire_neural_item_refused(tmp_path, monkeypatch):
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    item = _plan_item_for_db(neural_db)
    item.action = "supersede_or_retire"
    repair_db = tmp_path / "repair-copy.db"
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(neural_db, repair_db)
    shutil.copy2(neural_db, backup_db)
    with pytest.raises(ValueError, match="archive_neural"):
        apply_neural_repair_item_for_tests(
            neural_db_path=repair_db,
            backup_path=backup_db,
            item=item,
            confirm_token="REPAIR",
        )


def test_partner_described_neural_row_no_archive_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    with sqlite3.connect(neural_db) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("FACT", "School A", "Partner Person B student education at School A"),
        )
        conn.commit()
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("partner-only")
    store.add_turn(
        episode_id,
        "Remember this: my partner Person B is a student at School A.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    findings = audit_reconciliation(store, neural_db_path=neural_db)
    assert not any(
        f.category == "person_education_attributed_to_owner" for f in findings
    )
    plan = build_repair_plan(findings, neural_db_path=neural_db)
    assert not any(i.action == "archive_neural" for i in plan.items)


def test_owner_attributed_education_targets_only_proven_row(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    with sqlite3.connect(neural_db) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("FACT", "School A", "Owner A education on user profile at School A"),
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("FACT", "School B", "Owner A own education at School B"),
        )
        conn.commit()
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("partner-edu")
    store.add_turn(
        episode_id,
        "Remember this: my partner Person B is a student at School A.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    findings = audit_reconciliation(store, neural_db_path=neural_db)
    edu_findings = [
        f
        for f in findings
        if f.category == "person_education_attributed_to_owner"
    ]
    assert len(edu_findings) == 1
    target_ids = {f.neural_target_id for f in edu_findings if f.neural_target_id}
    rows = read_all_active_neural_fact_rows(neural_db)
    school_a = next(r for r in rows if "user profile" in r.content.lower())
    school_b = next(r for r in rows if r.name == "School B")
    assert school_a.opaque_target_id in target_ids
    assert school_b.opaque_target_id not in target_ids


def test_same_school_two_education_rows_no_archive_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    with sqlite3.connect(neural_db) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("FACT", "School A", "incorrect owner education at School A"),
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("FACT", "School A", "Owner A education on user profile at School A"),
        )
        conn.commit()
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("partner-edu-ambig")
    store.add_turn(
        episode_id,
        "Remember this: my partner Person B is a student at School A.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    findings = audit_reconciliation(store, neural_db_path=neural_db)
    edu_findings = [
        f for f in findings if f.category == "person_education_attributed_to_owner"
    ]
    assert edu_findings
    assert not any(f.neural_target_id for f in edu_findings)
    plan = build_repair_plan(findings, neural_db_path=neural_db)
    assert not any(i.action == "archive_neural" for i in plan.items)


def test_unique_misattributed_education_row_gets_archive_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    with sqlite3.connect(neural_db) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            (
                "FACT",
                "School A",
                "Owner A education attributed to owner profile at School A",
            ),
        )
        conn.commit()
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    episode_id = store.create_episode("partner-edu-unique")
    store.add_turn(
        episode_id,
        "Remember this: my partner Person B is a student at School A.",
        is_user=True,
    )
    candidates = EpisodeConsolidationPipeline(store).process_episode(episode_id)[1]
    MemoryReviewGate(store).accept(candidates[0].candidate_id)
    plan = build_repair_plan(
        audit_reconciliation(store, neural_db_path=neural_db),
        neural_db_path=neural_db,
    )
    archive_items = [i for i in plan.items if i.action == "archive_neural"]
    assert len(archive_items) == 1
    rows = read_all_active_neural_fact_rows(neural_db)
    owner_row = next(r for r in rows if is_proven_owner_attributed_education_row(r))
    assert archive_items[0].neural_target_id == owner_row.opaque_target_id


def _seed_many_row_neural_db(path: Path, *, row_count: int = 301) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        for idx in range(1, row_count + 1):
            conn.execute(
                "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
                ("FACT", f"Place {idx}", f"filler row {idx}"),
            )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("LOCATION", "City C", "legacy home in City C"),
        )
        conn.commit()


def test_fingerprint_rejects_db_differing_beyond_reporting_window(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    db_a = tmp_path / "db-a.db"
    db_b = tmp_path / "db-b.db"
    _seed_many_row_neural_db(db_a)
    shutil.copy2(db_a, db_b)
    with sqlite3.connect(db_b) as conn:
        conn.execute(
            "UPDATE nodes SET content = ? WHERE id = 1", ("mutated oldest row",)
        )
        conn.commit()
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _seed_brain_store(store)
    findings = audit_reconciliation(store, neural_db_path=db_a)
    plan = build_repair_plan(findings, neural_db_path=db_a)
    item = next(i for i in plan.items if i.action == "archive_neural")
    repair_b = tmp_path / "repair-b.db"
    backup_b = tmp_path / "backup-b.db"
    shutil.copy2(db_b, repair_b)
    shutil.copy2(db_b, backup_b)
    with pytest.raises(ValueError, match="restore snapshot|snapshot does not match"):
        apply_neural_repair_item_for_tests(
            neural_db_path=repair_b,
            backup_path=backup_b,
            item=item,
            confirm_token="REPAIR",
        )


def test_fingerprint_identical_full_copy_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_many_row_neural_db(neural_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _seed_brain_store(store)
    findings = audit_reconciliation(store, neural_db_path=neural_db)
    plan = build_repair_plan(findings, neural_db_path=neural_db)
    item = next(i for i in plan.items if i.action == "archive_neural")
    repair_db = tmp_path / "repair-copy.db"
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(neural_db, repair_db)
    shutil.copy2(neural_db, backup_db)
    apply_neural_repair_item_for_tests(
        neural_db_path=repair_db,
        backup_path=backup_db,
        item=item,
        confirm_token="REPAIR",
    )


def test_backup_rejects_restore_snapshot_archived_row_difference(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    _seed_fake_neural_db(neural_db)
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    _seed_brain_store(store)
    findings = audit_reconciliation(store, neural_db_path=neural_db)
    plan = build_repair_plan(findings, neural_db_path=neural_db)
    item = next(i for i in plan.items if i.action == "archive_neural")
    repair_db = tmp_path / "repair-copy.db"
    backup_db = tmp_path / "backup-copy.db"
    shutil.copy2(neural_db, repair_db)
    shutil.copy2(neural_db, backup_db)
    with sqlite3.connect(backup_db) as conn:
        conn.execute("UPDATE nodes SET is_archived = 1 WHERE id = 1")
        conn.commit()
    with pytest.raises(ValueError, match="restore snapshot"):
        apply_neural_repair_item_for_tests(
            neural_db_path=repair_db,
            backup_path=backup_db,
            item=item,
            confirm_token="REPAIR",
        )


def test_build_neural_summary_omits_partner_education_rows(tmp_path):
    from core.brain_v2.legacy_reconciliation import NeuralFactRow

    rows = [
        NeuralFactRow(
            1,
            "FACT",
            "School A",
            "Partner Person B student education at School A",
        ),
        NeuralFactRow(
            2,
            "FACT",
            "School B",
            "Owner A education on user profile at School B",
        ),
    ]
    summary = build_neural_summary_lines(rows)
    assert "school a" not in summary.lower()
    assert "school b" in summary.lower()


def test_ambiguous_education_neural_rows_manual_review_without_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HIKARI_BRAIN_V2_EPISODES_DB", str(tmp_path / EPISODES_DB))
    neural_db = tmp_path / HIKARI_MEMORY_DB
    with sqlite3.connect(neural_db) as conn:
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            "INSERT INTO nodes (node_type, name, content) VALUES (?, ?, ?)",
            ("FACT", "School X", "generic education reference"),
        )
        conn.commit()
    store = EpisodeStore(db_path=tmp_path / EPISODES_DB)
    findings = audit_reconciliation(store, neural_db_path=neural_db)
    assert not any(f.neural_target_id for f in findings)
