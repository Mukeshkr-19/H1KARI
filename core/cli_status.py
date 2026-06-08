"""Small startup panel for the interactive terminal CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent


def get_hikari_version() -> str:
    package_json = REPO_ROOT / "package.json"
    if not package_json.is_file():
        return "unknown"
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    return str(data.get("version") or "unknown")


def get_build_id() -> str:
    """Return the current Git commit so stale running CLIs are obvious."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=2,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _pad(text: str, width: int) -> str:
    if len(text) > width:
        return text[: width - 3] + "..."
    return text + (" " * (width - len(text)))


def _router_display() -> dict[str, Any]:
    try:
        from core.router import get_router

        router = get_router()
        return router.get_routing_display()
    except Exception:
        return {}


def get_startup_panel(width: int = 60) -> str:
    """Return an ASCII-only panel with useful non-private startup status."""
    info = _router_display()
    provider = info.get("provider") or "none"
    model = info.get("model") or "unavailable"
    fallbacks = info.get("fallback_labels") or []
    fallback_text = ", ".join(fallbacks[:3]) if fallbacks else "none configured"
    version = get_hikari_version()
    build = get_build_id()

    inner = width - 4
    rows = [
        f"Version   {version}",
        f"Build     {build}",
        f"Provider  {provider}",
        f"Model     {model}",
        f"Fallback  {fallback_text}",
        "Mode      text chat",
    ]

    border = "+" + "-" * (width - 2) + "+"
    body = [border]
    for row in rows:
        body.append("| " + _pad(row, inner) + " |")
    body.append(border)
    return "\n".join(body)
