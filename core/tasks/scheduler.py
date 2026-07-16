"""macOS Reminders scheduling for task intents (opt-in)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Iterable, Optional

from core.os_side_effects import osascript_disabled

ENV_ENABLE_TASK_SCHEDULER = "HIKARI_ENABLE_TASK_SCHEDULER"


@dataclass(frozen=True)
class SchedulerResult:
    ok: bool
    backend: str = "macos_reminders"
    error: Optional[str] = None


def task_scheduler_enabled() -> bool:
    """Legacy Reminders execution is quarantined until its policy adapter lands."""
    return False


def _escape_applescript_string(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace('"', '\\"')


class MacOSReminderScheduler:
    """Create a Reminders item via osascript when explicitly enabled."""

    def schedule_reminder(self, *, title: str, body: str = "") -> SchedulerResult:
        if not task_scheduler_enabled():
            return SchedulerResult(ok=False, error="scheduler_disabled")
        if osascript_disabled():
            return SchedulerResult(ok=False, error="osascript_disabled")

        raw_title = (title or "HIKARI task")[:500]
        raw_body = (body or "")[:2000]
        safe_title = _escape_applescript_string(raw_title)
        safe_body = _escape_applescript_string(raw_body)
        script = (
            'tell application "Reminders"\n'
            f'    make new reminder with properties {{name:"{safe_title}", body:"{safe_body}"}}\n'
            "end tell"
        )
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return SchedulerResult(
                ok=False,
                error=_sanitize_error(str(exc), redactions=(raw_title, raw_body)),
            )

        if proc.returncode != 0:
            return SchedulerResult(
                ok=False,
                error=_sanitize_error(
                    proc.stderr or proc.stdout or "osascript_failed",
                    redactions=(raw_title, raw_body),
                ),
            )
        return SchedulerResult(ok=True)


def _sanitize_error(
    message: str,
    *,
    redactions: Iterable[str] = (),
) -> str:
    text = re.sub(r"\s+", " ", (message or "").strip())
    for value in redactions:
        raw = (value or "").strip()
        if raw:
            text = text.replace(raw, "[redacted task]")
    if len(text) > 160:
        text = text[:157] + "..."
    return text or "unknown_scheduler_error"


def extract_due_text(raw_text: str) -> Optional[str]:
    low = (raw_text or "").lower()
    for token in ("tomorrow", "today", "tonight", "next week", "monday", "tuesday"):
        if token in low:
            return token
    m = re.search(
        r"\b(?:on|at)\s+((?:monday|tuesday|wednesday|thursday|friday|saturday|sunday).*)",
        low,
    )
    if m:
        return m.group(1).strip()[:120]
    return None
