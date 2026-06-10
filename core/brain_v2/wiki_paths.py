"""Private wiki output directory resolution."""

from __future__ import annotations

import os
from pathlib import Path

from core.runtime_paths import brain_dir

ENV_WIKI_DIR = "HIKARI_WIKI_DIR"


def resolve_wiki_dir() -> Path:
    """Private wiki root (never the public repo). Override with HIKARI_WIKI_DIR in tests."""
    explicit = os.environ.get(ENV_WIKI_DIR)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (brain_dir() / "wiki").expanduser().resolve()
