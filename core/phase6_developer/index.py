"""Indexing entry point for Phase 6 developer mode.

This module provides a stable public facade for building a ``RepositoryIndex``
from a ``RepositoryRoot`` and ``RepositoryPolicy``. The actual read-only scan
and conservative language-specific extraction live in sibling modules; this
file exists to keep the allowlisted ``core/phase6_developer/index.py`` surface
explicit and discoverable.

No I/O, subprocess, network, or code execution occurs in this module.
"""

from __future__ import annotations

from core.phase6_developer.contracts import (
    RepositoryIndex,
    RepositoryPolicy,
    RepositoryRoot,
)
from core.phase6_developer.repository import scan_repository


def index_repository(root: RepositoryRoot, policy: RepositoryPolicy) -> RepositoryIndex:
    """Build and return a deterministic ``RepositoryIndex`` for ``root``.

    This is a convenience alias for ``scan_repository``. It performs a read-only
    walk, validates containment, applies ``policy`` limits, and returns an
    immutable index with files, symbols, imports, references, and edges.
    """
    return scan_repository(root, policy)


__all__ = ["index_repository", "scan_repository"]
