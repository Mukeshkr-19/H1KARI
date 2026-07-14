"""Runtime path helpers for local/private HIKARI state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from core.path_literals import DOT_HIKARI


def hikari_home(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve private runtime state without ever accepting a code checkout."""
    env = os.environ if environ is None else environ
    explicit = env.get("HIKARI_HOME", "").strip()
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if (candidate / "hikari.py").is_file() and (candidate / "core").is_dir():
            raise RuntimeError(
                "HIKARI_HOME points to a HIKARI code checkout. "
                "Use HIKARI_REPO_ROOT for code and a separate HIKARI_HOME for private state."
            )
        return candidate

    base = Path.home() if home is None else Path(home).expanduser()
    return base / DOT_HIKARI


def brain_dir() -> Path:
    """Return the private brain directory, following the live brain symlink."""
    return Path(os.getenv("HIKARI_BRAIN_DIR", hikari_home() / "brain")).expanduser()


def legacy_data_dir() -> Path:
    """Return the private location for legacy JSON runtime data.

    Older HIKARI modules still use small JSON files for compatibility. Keep those
    files beside the private brain instead of recreating a public repo `data/`
    directory.
    """
    return Path(
        os.getenv("HIKARI_LEGACY_DATA_DIR", brain_dir() / "legacy-data")
    ).expanduser()
