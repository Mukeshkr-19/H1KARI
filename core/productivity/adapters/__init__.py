"""Bounded Phase 3 productivity adapters package."""

from core.productivity.adapters.macos_actions import (
    OSASCRIPT_PATH,
    OSASCRIPT_TIMEOUT_SECONDS,
    CalendarDraftMacAdapter,
    CommandResult,
    EmailDraftMacAdapter,
    ReminderCreateMacAdapter,
    escape_applescript_string,
    production_osascript_runner,
)

__all__ = (
    "OSASCRIPT_PATH",
    "OSASCRIPT_TIMEOUT_SECONDS",
    "CalendarDraftMacAdapter",
    "CommandResult",
    "EmailDraftMacAdapter",
    "ReminderCreateMacAdapter",
    "escape_applescript_string",
    "production_osascript_runner",
)
