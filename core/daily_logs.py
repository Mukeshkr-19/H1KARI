"""
Rotate append-only logs when the calendar day changes.
Archives previous day's file under logs/archive/ (local-only; not committed).
"""

from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path


def maybe_rotate_daily_log(repo_root: Path, log_filename: str) -> Path:
    """
    If logs/{log_filename} exists and its mtime is from a previous calendar day,
    move it to logs/archive/{stem}-YYYY-MM-DD{suffix}.
    Returns the path to use for today's writes (may be newly empty).
    """
    logs = repo_root / "logs"
    archive = logs / "archive"
    logs.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)

    log_path = logs / log_filename
    if not log_path.is_file():
        return log_path

    mtime_date = datetime.fromtimestamp(log_path.stat().st_mtime).date()
    today = date.today()
    if mtime_date >= today:
        return log_path

    stamp = mtime_date.isoformat()
    stem = log_path.stem
    suffix = log_path.suffix or ".log"
    dest = archive / f"{stem}-{stamp}{suffix}"
    n = 1
    while dest.exists():
        n += 1
        dest = archive / f"{stem}-{stamp}-{n}{suffix}"

    shutil.move(str(log_path), str(dest))
    return log_path
