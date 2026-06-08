"""
HIKARI macOS Integration - AppleScript-based system integration
Inspired by JARVIS (ethanplusai/jarvis)

Features:
- Calendar access (read events, create events)
- Mail access (read unread emails)
- Notes access (read/create notes)
- Reminders access (read/create reminders)
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

log = logging.getLogger("hikari.mac_integration")


async def run_applescript(script: str, timeout: float = 15.0) -> Dict[str, Any]:
    """Run AppleScript and return success/failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        success = proc.returncode == 0
        return {
            "success": success,
            "stdout": stdout.decode().strip() if stdout else "",
            "stderr": stderr.decode().strip() if stderr else "",
        }
    except asyncio.TimeoutError:
        return {"success": False, "error": "AppleScript timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


CALENDAR_GET_EVENTS = """
tell application "Calendar"
    set eventList to {}
    set todayDate to current date
    set endDate to todayDate + 1 * days

    repeat with cal in calendars
        try
            tell cal
                set calEvents to events whose start date > todayDate and start date < endDate
                repeat with evt in calEvents
                    set evtStart to start date of evt
                    set evtEnd to end date of evt
                    set evtSummary to summary of evt
                    set evtCal to name of calendar
                    set end of eventList to evtSummary & "|||" & evtStart as text & "|||" & evtEnd as text & "|||" & evtCal
                end repeat
            end tell
        on error
        end try
    end repeat
    return eventList
end tell
"""

MAIL_GET_UNREAD = """
tell application "Mail"
    set unreadList to {}
    repeat with msg in (messages of inbox whose read status is false)
        set msgSubject to subject of msg
        set msgSender to sender of msg
        set msgDate to date received of msg
        set end of unreadList to msgSubject & "|||" & msgSender & "|||" & msgDate as text
        if (count of unreadList) >= 5 then exit repeat
    end repeat
    return unreadList
end tell
"""

NOTES_GET_ALL = """
tell application "Notes"
    set noteList to {}
    repeat with nt in notes
        set ntName to name of nt
        set ntBody to plaintext of nt
        set end of noteList to ntName & "|||" & ntBody
        if (count of noteList) >= 10 then exit repeat
    end repeat
    return noteList
end tell
"""

REMINDERS_GET_TODAY = """
tell application "Reminders"
    set remList to {}
    set todayDate to current date
    repeat with lst in lists
        repeat with rem in (reminders of lst whose due date > todayDate - 1 * days)
            set remTitle to name of rem
            set remDue to due date of rem
            set end of remList to remTitle & "|||" & remDue as text
        end repeat
    end repeat
    return remList
end tell
"""

GET_ACTIVE_APP = """
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    return name of frontApp
end tell
"""

GET_CHROME_TAB = """
tell application "Google Chrome"
    try
        set tabTitle to title of active tab of front window
        set tabURL to URL of active tab of front window
        return tabTitle & "|||" & tabURL
    on error
        return ""
    end try
end tell
"""


async def get_calendar_events(days: int = 1) -> Dict[str, Any]:
    """Get calendar events for the next N days."""
    result = await run_applescript(CALENDAR_GET_EVENTS)

    if not result["success"]:
        return {"success": False, "events": [], "error": result.get("stderr", "Failed")}

    events = []
    for line in result["stdout"].split("\n"):
        if "|||" in line:
            parts = line.split("|||")
            if len(parts) >= 4:
                events.append(
                    {
                        "title": parts[0],
                        "start": parts[1],
                        "end": parts[2],
                        "calendar": parts[3],
                    }
                )

    return {
        "success": True,
        "events": events,
        "confirmation": f"Found {len(events)} events.",
    }


async def get_unread_emails(count: int = 5) -> Dict[str, Any]:
    """Get unread emails from Mail.app."""
    result = await run_applescript(MAIL_GET_UNREAD, timeout=20.0)

    if not result["success"]:
        return {"success": False, "emails": [], "error": result.get("stderr", "Failed")}

    emails = []
    for line in result["stdout"].split("\n"):
        if "|||" in line:
            parts = line.split("|||")
            if len(parts) >= 3:
                emails.append(
                    {
                        "subject": parts[0],
                        "sender": parts[1],
                        "date": parts[2],
                    }
                )

    return {
        "success": True,
        "emails": emails[:count],
        "confirmation": f"Found {len(emails)} unread emails.",
    }


async def get_notes(count: int = 10) -> Dict[str, Any]:
    """Get notes from Notes.app."""
    result = await run_applescript(NOTES_GET_ALL, timeout=15.0)

    if not result["success"]:
        return {"success": False, "notes": [], "error": result.get("stderr", "Failed")}

    notes = []
    for line in result["stdout"].split("\n"):
        if "|||" in line:
            parts = line.split("|||", 1)
            if len(parts) >= 1:
                notes.append(
                    {"name": parts[0], "body": parts[1] if len(parts) > 1 else ""}
                )

    return {
        "success": True,
        "notes": notes[:count],
        "confirmation": f"Found {len(notes)} notes.",
    }


async def get_reminders(due_today: bool = True) -> Dict[str, Any]:
    """Get reminders from Reminders.app."""
    result = await run_applescript(REMINDERS_GET_TODAY, timeout=15.0)

    if not result["success"]:
        return {
            "success": False,
            "reminders": [],
            "error": result.get("stderr", "Failed"),
        }

    reminders = []
    for line in result["stdout"].split("\n"):
        if "|||" in line:
            parts = line.split("|||")
            if len(parts) >= 2:
                reminders.append({"title": parts[0], "due": parts[1]})

    return {
        "success": True,
        "reminders": reminders,
        "confirmation": f"Found {len(reminders)} reminders.",
    }


async def get_active_app() -> Optional[str]:
    """Get frontmost application name."""
    result = await run_applescript(GET_ACTIVE_APP)
    return result["stdout"] if result["success"] else None


async def get_chrome_tab() -> Optional[Dict[str, str]]:
    """Get current Chrome tab info."""
    result = await run_applescript(GET_CHROME_TAB)
    if result["success"] and result["stdout"]:
        parts = result["stdout"].split("|||")
        if len(parts) == 2:
            return {"title": parts[0], "url": parts[1]}
    return None


class MacIntegration:
    """macOS integration wrapper."""

    async def calendar_events(self, days: int = 1) -> Dict[str, Any]:
        return await get_calendar_events(days)

    async def unread_emails(self, count: int = 5) -> Dict[str, Any]:
        return await get_unread_emails(count)

    async def notes(self, count: int = 10) -> Dict[str, Any]:
        return await get_notes(count)

    async def reminders(self, due_today: bool = True) -> Dict[str, Any]:
        return await get_reminders(due_today)

    async def active_app(self) -> Optional[str]:
        return await get_active_app()

    async def chrome_tab(self) -> Optional[Dict[str, str]]:
        return await get_chrome_tab()


_mac_instance = None


def get_mac_integration():
    global _mac_instance
    if _mac_instance is None:
        _mac_instance = MacIntegration()
    return _mac_instance
