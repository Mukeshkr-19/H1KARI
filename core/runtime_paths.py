"""Runtime path helpers for local/private HIKARI state."""

from __future__ import annotations

import os
from pathlib import Path

from core.path_literals import DOT_HIKARI


def brain_dir() -> Path:
    """Return the private brain directory, following the live brain symlink."""
    return Path(os.getenv("HIKARI_BRAIN_DIR", Path.home() / DOT_HIKARI / "brain")).expanduser()


def legacy_data_dir() -> Path:
    """Return the private location for legacy JSON runtime data.

    Older HIKARI modules still use small JSON files for compatibility. Keep those
    files beside the private brain instead of recreating a public repo `data/`
    directory.
    """
    return Path(
        os.getenv("HIKARI_LEGACY_DATA_DIR", brain_dir() / "legacy-data")
    ).expanduser()
