"""Helpers for sqlite3.Row and plain dict rows (consolidation / storage)."""

from __future__ import annotations

from typing import Any, Mapping, Optional


def row_as_dict(row: Optional[Any]) -> dict:
    """
    Normalize a sqlite3.Row, mapping, or None into a plain dict.
    sqlite3.Row supports dict(row) in modern Python, but iterating keys()
    is the most defensive pattern across versions.
    """
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, Mapping):
        return dict(row)
    keys = getattr(row, "keys", None)
    if callable(keys):
        return {k: row[k] for k in keys()}
    raise TypeError(f"Unsupported row type: {type(row)!r}")
