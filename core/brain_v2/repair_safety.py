"""Safety gates for Brain v2 accepted-memory repair on the live episodes database."""

from __future__ import annotations

import os
from typing import Optional

from core.brain_v2.db_paths import (
    episodes_db_explicitly_configured,
    resolve_episodes_db_path,
)

ENV_SKIP_REPAIR_CONFIRM = "HIKARI_BRAIN_V2_SKIP_REPAIR_CONFIRM"

REPAIR_CONFIRM_RETIRE = "RETIRE"
REPAIR_CONFIRM_SUPERSEDE = "SUPERSEDE"
REPAIR_CONFIRM_EDIT = "EDIT"

_REPAIR_ACTION_TOKENS = {
    "retire": REPAIR_CONFIRM_RETIRE,
    "supersede": REPAIR_CONFIRM_SUPERSEDE,
    "edit_metadata": REPAIR_CONFIRM_EDIT,
}


def is_live_episodes_database() -> bool:
    """True when using the default private brain path (not an explicit test override)."""
    return not episodes_db_explicitly_configured()


def repair_confirmation_required() -> bool:
    if os.getenv(ENV_SKIP_REPAIR_CONFIRM, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    return is_live_episodes_database()


def expected_repair_confirm_token(action: str) -> str:
    token = _REPAIR_ACTION_TOKENS.get(action)
    if not token:
        raise ValueError(f"Unknown repair action for confirmation: {action}")
    return token


def validate_repair_confirmation(
    action: str,
    confirm_repair: Optional[str],
) -> Optional[str]:
    """Return an error message when confirmation is missing or wrong; else None."""
    if not repair_confirmation_required():
        return None
    expected = expected_repair_confirm_token(action)
    if not confirm_repair:
        return (
            f"Live Brain v2 database repair requires --confirm-repair {expected} "
            f"(exact, case-sensitive). Back up the private brain directory first."
        )
    if confirm_repair != expected:
        return (
            f"Invalid --confirm-repair token (expected exactly {expected}); "
            "no changes were made."
        )
    return None


def format_live_repair_warning(action: str) -> str:
    db_path = resolve_episodes_db_path()
    token = expected_repair_confirm_token(action)
    return (
        "WARNING: This will modify the live Brain v2 episodes database.\n"
        f"  database: {db_path}\n"
        "  backup: copy the private brain directory before applying.\n"
        f"  apply: re-run with --confirm-repair {token}"
    )
