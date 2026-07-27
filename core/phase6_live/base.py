"""Shared base utilities for Phase 6 live backends.

Pure helpers.  No I/O, network, subprocess, model, or database access at
import time.  All side-effecting work happens inside explicitly constructed
backend classes.
"""

from __future__ import annotations

import math
import os
import re
import stat
from pathlib import Path
from typing import Optional


_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")


class LiveBackendError(Exception):
    """Fixed, content-free exception for live backend failures."""

    def __init__(self, reason: str = "live_backend_error") -> None:
        self.reason = reason
        super().__init__("LiveBackendError")

    def __repr__(self) -> str:
        return "LiveBackendError()"


def validate_identifier(value: object, field: str = "id") -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid_{field}")
    return value


def validate_finite(value: object, field: str = "value") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_{field}")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"invalid_{field}")
    return number


def validate_positive_int(value: object, field: str = "value", *, maximum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid_{field}")
    if maximum is not None and value > maximum:
        raise ValueError(f"invalid_{field}")
    return value


def validate_path(path: Path, *, must_exist: bool = False, must_be_file: bool = False) -> Path:
    """Validate a caller-supplied filesystem path is safe.

    - Must be absolute.
    - Must not contain NUL bytes.
    - Must not be a symlink if it is expected to exist.
    - If must_exist and must_be_file, must be a regular file.
    """
    if not isinstance(path, Path):
        raise ValueError("invalid_path")
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    try:
        path_str = str(path)
    except Exception as exc:
        raise ValueError("invalid_path") from exc
    if "\x00" in path_str:
        raise ValueError("nul_byte_in_path")
    if must_exist:
        # Use os.lstat to avoid following symlinks for the existence check.
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise ValueError("path_not_found") from None
        except OSError as exc:
            raise ValueError("path_access_error") from exc
        if stat.S_ISLNK(st.st_mode):
            raise ValueError("symlink_not_allowed")
        if must_be_file and not stat.S_ISREG(st.st_mode):
            raise ValueError("not_a_regular_file")
    return path


def safe_makedirs(path: Path) -> None:
    """Create a directory with restrictive permissions, refusing symlinks."""
    if path.exists():
        if path.is_symlink():
            raise ValueError("symlink_not_allowed")
        if not path.is_dir():
            raise ValueError("path_not_directory")
        return
    os.makedirs(path, mode=0o700, exist_ok=False)


def set_restrictive_permissions(path: Path) -> None:
    """Set restrictive owner-only permissions on a file or directory."""
    try:
        if path.is_dir():
            path.chmod(0o700)
        else:
            path.chmod(0o600)
    except OSError:
        pass
