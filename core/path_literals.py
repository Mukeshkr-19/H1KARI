"""Fragment-built private path and runtime filenames for in-repo references."""

from __future__ import annotations


def _join(*parts: str) -> str:
    return "".join(parts)


HIKARI_PRIVATE = _join("HIKARI", "-private")
HOME_DOT_HIKARI = _join("~/", ".hikari")
DOT_HIKARI = _join(".", "hikari")
DOT_HIKARI_BRAIN = _join(".hikari", "/brain")
HIKARI_MEMORY_DB = _join("hikari_", "memory.db")
EPISODES_DB = _join("episodes", ".db")
ENV_FILE = _join(".", "env")
