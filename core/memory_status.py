"""Safe read-only memory / brain diagnostics (no private conversation dumps)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from core.path_literals import DOT_HIKARI, HIKARI_PRIVATE

if TYPE_CHECKING:
    from core.orchestrator import HIKARI_Orchestrator

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EXPECTED_BRAIN = PROJECT_ROOT / HIKARI_PRIVATE / "live-brain"
BRAIN_LINK = Path.home() / DOT_HIKARI / "brain"


def _brain_v2_policy_enabled() -> bool:
    return os.getenv("HIKARI_DISABLE_BRAIN_V2", "0") != "1"


def _guest_session(orchestrator: Optional["HIKARI_Orchestrator"]) -> bool:
    if orchestrator is None:
        return False
    speaker = getattr(orchestrator, "speaker", None)
    if speaker is None:
        return False
    return bool(speaker.is_guest_speaker())


def _guest_safe_status_report() -> str:
    return "\n".join(
        [
            "HIKARI Memory Status",
            "=" * 22,
            "Speaker mode: guest",
            "Owner identity and household personal memory details are hidden in guest mode.",
            "Use owner-only maintenance commands after the guest session ends.",
        ]
    )


def _readonly_brain_counts(db_path: Path) -> Optional[tuple[int, int, int, int]]:
    if not db_path.is_file():
        return None
    try:
        uri = f"file:{db_path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
            nodes = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE is_archived = 0"
            ).fetchone()[0]
            edges = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE is_archived = 0"
            ).fetchone()[0]
            persons = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE node_type = 'PERSON' AND is_archived = 0"
            ).fetchone()[0]
            facts = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE node_type = 'FACT' AND is_archived = 0"
            ).fetchone()[0]
        return nodes, edges, persons, facts
    except sqlite3.Error:
        return None


def format_memory_status_report(orchestrator: Optional["HIKARI_Orchestrator"] = None) -> str:
    if _guest_session(orchestrator):
        return _guest_safe_status_report()

    lines = ["HIKARI Memory Status", "=" * 22]

    brain_v2_on = _brain_v2_policy_enabled()
    if orchestrator is not None:
        brain_v2_on = bool(getattr(orchestrator, "brain_v2_enabled", brain_v2_on))

    if brain_v2_on:
        lines.append("Neural memory: quarantined (Brain v2 authority)")
        lines.append("Legacy neural runtime: not used for normal chat/profile/recall")
        if orchestrator is not None and orchestrator.speaker.current_speaker:
            lines.append(f"Current speaker: {orchestrator.speaker.current_speaker}")
        lines.append("")
        lines.append("Use Brain v2 CLI reconcile/readiness for legacy migration review.")
        return "\n".join(lines)

    connected = False
    if orchestrator is not None:
        connected = bool(orchestrator.neural_memory_enabled)
    else:
        try:
            from core import neural_memory_bridge

            connected = bool(neural_memory_bridge.init_neural_memory())
        except Exception:
            connected = False

    lines.append(f"Neural memory: {'connected' if connected else 'not connected'}")

    from core.neural_memory.config import config as neural_config

    brain_path = neural_config.DB_PATH
    if BRAIN_LINK.is_symlink():
        lines.append(f"Brain symlink: {BRAIN_LINK} -> {BRAIN_LINK.resolve()}")
    lines.append(f"Brain DB path: {brain_path}")

    counts = _readonly_brain_counts(brain_path)
    if counts:
        nodes, edges, persons, facts = counts
        lines.append(f"Nodes: {nodes} (persons: {persons}, facts: {facts})")
        lines.append(f"Edges: {edges}")
    elif brain_path.is_file():
        lines.append("Counts: unavailable (read-only open failed)")
    else:
        lines.append("Counts: n/a (database file not found)")

    if orchestrator is not None and orchestrator.speaker.current_speaker:
        lines.append(f"Current speaker: {orchestrator.speaker.current_speaker}")
        if orchestrator.speaker.primary_user and not _guest_session(orchestrator):
            lines.append(f"Primary speaker: {orchestrator.speaker.primary_user}")

    lines.append("")
    lines.append("Tip: run `hikari.py --brain-v2-readiness` for Brain v2 episode status.")
    return "\n".join(lines)
