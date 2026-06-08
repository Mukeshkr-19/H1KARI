"""Brain v2 status — shared by doctor, memory-status, and CLI."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.path_literals import EPISODES_DB


def is_brain_v2_enabled() -> bool:
    return os.getenv("HIKARI_DISABLE_BRAIN_V2", "0") != "1"


def default_episodes_db_path() -> Path:
    from core.brain_v2.db_paths import resolve_episodes_db_path

    return resolve_episodes_db_path()


def collect_brain_v2_status(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Read-only counts; safe when DB is missing."""
    enabled = is_brain_v2_enabled()
    path = Path(db_path) if db_path else default_episodes_db_path()
    out: Dict[str, Any] = {
        "enabled": enabled,
        "db_path": str(path),
        "db_exists": path.is_file(),
        "raw_episodes": 0,
        "transcript_segments": 0,
        "structured_episodes": 0,
        "pending_candidates": 0,
        "accepted_memories": 0,
        "rejected_candidates": 0,
        "duplicate_pending": 0,
    }
    if not enabled:
        out["note"] = "disabled via HIKARI_DISABLE_BRAIN_V2=1"
        return out
    if not path.is_file():
        out["note"] = "database not created yet (run a chat session or brain-v2 CLI)"
        return out
    try:
        uri = f"file:{path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
            out["raw_episodes"] = conn.execute(
                "SELECT COUNT(*) FROM raw_episodes"
            ).fetchone()[0]
            out["transcript_segments"] = conn.execute(
                "SELECT COUNT(*) FROM transcript_segments"
            ).fetchone()[0]
            out["structured_episodes"] = conn.execute(
                "SELECT COUNT(*) FROM structured_episodes"
            ).fetchone()[0]
            out["pending_candidates"] = conn.execute(
                "SELECT COUNT(*) FROM memory_candidates WHERE review_status = 'pending'"
            ).fetchone()[0]
            out["accepted_memories"] = conn.execute(
                "SELECT COUNT(*) FROM source_linked_memories"
            ).fetchone()[0]
            out["rejected_candidates"] = conn.execute(
                "SELECT COUNT(*) FROM memory_candidates WHERE review_status = 'rejected'"
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT metadata FROM memory_candidates WHERE review_status = 'pending'"
            ).fetchall()
        import json

        dup = 0
        for (raw_meta,) in rows:
            try:
                meta = json.loads(raw_meta) if raw_meta else {}
            except json.JSONDecodeError:
                meta = {}
            if meta.get("duplicate_of"):
                dup += 1
        out["duplicate_pending"] = dup
    except sqlite3.Error as exc:
        out["error"] = str(exc)
    return out


def format_brain_v2_status_lines(status: Optional[Dict[str, Any]] = None) -> List[str]:
    status = status or collect_brain_v2_status()
    lines = [
        f"Brain v2: {'enabled' if status.get('enabled') else 'disabled'}",
        f"  DB path: {status.get('db_path', 'n/a')}",
        f"  DB exists: {'yes' if status.get('db_exists') else 'no'}",
    ]
    if status.get("note"):
        lines.append(f"  Note: {status['note']}")
    if status.get("error"):
        lines.append(f"  Error: {status['error']}")
        return lines
    if status.get("db_exists"):
        lines.extend(
            [
                f"  Raw episodes: {status.get('raw_episodes', 0)}",
                f"  Transcript segments: {status.get('transcript_segments', 0)}",
                f"  Structured episodes: {status.get('structured_episodes', 0)}",
                f"  Pending candidates: {status.get('pending_candidates', 0)}",
                f"  Rejected candidates: {status.get('rejected_candidates', 0)}",
                f"  Accepted memories: {status.get('accepted_memories', 0)}",
            ]
        )
        if status.get("duplicate_pending", 0):
            lines.append(
                f"  Pending marked duplicate-of-primary: {status['duplicate_pending']}"
            )
    return lines
