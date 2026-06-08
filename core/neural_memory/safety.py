"""Safety checks for Hikari Neural Memory."""

import logging
import re
from pathlib import Path
from typing import Optional

from .config import config

logger = logging.getLogger(__name__)


class MemorySafety:
    BLOCKED_PATTERNS = [
        r"rm\s+-rf",
        r"drop\s+table",
        r"delete\s+from.*where.*1\s*=\s*1",
        r";\s*shutdown",
    ]

    def __init__(self):
        self._compiled_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.BLOCKED_PATTERNS
        ]
        self._validate_paths()

    def _validate_paths(self):
        if not config.BRAIN_DIR.exists():
            try:
                config.BRAIN_DIR.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                logger.error(f"Cannot create brain directory: {config.BRAIN_DIR}")
                raise

    def validate_query(self, query: str) -> tuple[bool, Optional[str]]:
        for pattern in self._compiled_patterns:
            if pattern.search(query):
                return False, f"Query contains blocked pattern"
        return True, None

    def validate_node_name(self, name: str) -> tuple[bool, Optional[str]]:
        if not name or len(name) > 200:
            return False, "Invalid name length"

        if not re.match(r"^[\w\s\-./]+$", name):
            return False, "Name contains invalid characters"

        return True, None

    def sanitize_content(self, content: str) -> str:
        content = content.strip()
        content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", content)
        return content[:100000]

    def check_corruption(self) -> dict:
        issues = []

        if not config.DB_PATH.exists():
            issues.append("Database file missing")
            return {"healthy": False, "issues": issues}

        try:
            import sqlite3

            conn = sqlite3.connect(config.DB_PATH)
            cursor = conn.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            conn.close()

            if result[0] != "ok":
                issues.append(f"Integrity check failed: {result[0]}")
        except Exception as e:
            issues.append(f"Database check failed: {e}")

        return {"healthy": len(issues) == 0, "issues": issues}

    def backup_if_needed(self):
        backup_dir = config.BACKUPS_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)

        if not config.DB_PATH.exists():
            return

        backup_path = (
            backup_dir / f"hikari_memory_backup_{int(__import__('time').time())}.db"
        )

        try:
            import shutil

            shutil.copy2(config.DB_PATH, backup_path)

            backups = sorted(
                backup_dir.glob("hikari_memory_backup_*.db"),
                key=lambda p: p.stat().st_mtime,
            )
            while len(backups) > 5:
                oldest = backups.pop(0)
                oldest.unlink()
                logger.info(f"Removed old backup: {oldest}")

        except Exception as e:
            logger.error(f"Backup failed: {e}")


safety = MemorySafety()
