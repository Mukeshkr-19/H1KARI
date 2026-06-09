"""
HIKARI v3 - Enhanced Mac Integration
Complete Mac control: apps, calendar, mail, notes, reminders, system settings
"""

import os
import sys
import asyncio
import subprocess
from typing import Optional, Dict, Any, List
from datetime import datetime

from core.os_side_effects import osascript_disabled

class MacControl:
    """Complete Mac integration - controls everything on your Mac"""

    def __init__(self):
        self._cache = {}

    async def run_applescript(self, script: str, timeout: float = 15.0) -> Dict[str, Any]:
        """Run AppleScript and return result"""
        if osascript_disabled():
            return {"success": False, "error": "osascript_disabled"}

        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "success": proc.returncode == 0,
                "stdout": stdout.decode().strip() if stdout else "",
                "stderr": stderr.decode().strip() if stderr else "",
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": "AppleScript timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # === APPS ===

    async def open_app(self, app_name: str) -> str:
        """Open any application"""
        app_map = {
            "safari": "Safari", "chrome": "Google Chrome", "brave": "Brave Browser",
            "firefox": "Firefox", "slack": "Slack", "discord": "Discord",
            "spotify": "Spotify", "music": "Music", "notes": "Notes",
            "calendar": "Calendar", "mail": "Mail", "messages": "Messages",
            "facetime": "FaceTime", "terminal": "Terminal",
            "vscode": "Visual Studio Code", "pycharm": "PyCharm",
            "telegram": "Telegram", "zoom": "Zoom", "teams": "Microsoft Teams",
        }

        name = app_map.get(app_name.lower(), app_name.title())

        try:
            # Try mdfind first
            result = await self.run_applescript(f'name of application "{name}"')
            if result["success"]:
                subprocess.Popen(["open", "-a", name])
                return f"Opening {name}..."

            subprocess.Popen(["open", "-a", name])
            return f"Opening {name}..."
        except Exception as e:
            return f"Couldn't open {app_name}: {str(e)}"

    async def close_app(self, app_name: str) -> str:
        """Quit an application"""
        app_map = {
            "safari": "Safari", "chrome": "Google Chrome", "spotify": "Spotify",
            "slack": "Slack", "discord": "Discord",
        }
        name = app_map.get(app_name.lower(), app_name.title())
        result = await self.run_applescript(f'tell application "{name}" to quit')
        return f"Quit {name}." if result["success"] else f"Couldn't quit {name}"

    async def get_frontmost_app(self) -> str:
        """Get the currently active app"""
        result = await self.run_applescript('''
            tell application "System Events"
                set frontApp to first application process whose frontmost is true
                return name of frontApp
            end tell
        ''')
        return result.get("stdout", "Unknown")

    # === SYSTEM CONTROL ===

    async def get_battery(self) -> Dict[str, Any]:
        """Get battery status"""
        result = await self.run_applescript('''
            tell application (system info)
                battery status
            end tell
        ''')
        return {"status": result.get("stdout", "Unknown")}

    async def get_disk_space(self) -> str:
        """Get available disk space"""
        result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
        lines = result.stdout.strip().split("\n")
        if len(lines) > 1:
            parts = lines[1].split()
            return f"Disk: {parts[3]} available of {parts[1]}"
        return "Couldn't get disk info"

    async def get_memory(self) -> str:
        """Get memory usage"""
        result = subprocess.run(["memory_pressure"], capture_output=True, text=True)
        if "System-wide memory free percentage" in result.stdout:
            pct = result.stdout.split(":")[-1].strip().replace("%", "")
            return f"Memory free: {pct}%"
        return "Couldn't get memory info"

    async def set_volume(self, level: int) -> str:
        """Set volume (0-100)"""
        level = max(0, min(100, level))
        script = f"set volume output volume {level}"
        await self.run_applescript(script)
        return f"Volume set to {level}%"

    async def set_brightness(self, level: int) -> str:
        """Set screen brightness (0-100)"""
        level = max(0, min(100, level))
        script = f"set brightness of (first screen) to {level / 100.0}"
        await self.run_applescript(script)
        return f"Brightness set to {level}%"

    async def lock_screen(self) -> str:
        """Lock the Mac screen"""
        try:
            subprocess.run([
                "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",
                "-suspend"
            ], timeout=5)
            return "Screen locked"
        except:
            return "Couldn't lock screen"

    async def sleep_mac(self) -> str:
        """Put Mac to sleep"""
        subprocess.run(["pmset", "sleepnow"], timeout=5)
        return "Going to sleep..."

    async def restart_mac(self) -> str:
        """Restart the Mac"""
        await self.run_applescript('tell app "System Events" to restart')
        return "Restarting..."

    async def shutdown_mac(self) -> str:
        """Shut down the Mac"""
        await self.run_applescript('tell app "System Events" to shutdown')
        return "Shutting down..."

    async def empty_trash(self) -> str:
        """Empty the trash"""
        result = await self.run_applescript('tell app "Finder" to empty trash')
        return "Trash emptied!" if result["success"] else "Couldn't empty trash"

    # === CALENDAR ===

    async def get_calendar_events(self, days: int = 1) -> List[Dict]:
        """Get upcoming calendar events"""
        script = '''
        tell application "Calendar"
            set eventList to {}
            set todayDate to current date
            set endDate to todayDate + {} * days
            repeat with cal in calendars
                try
                    tell cal
                        set calEvents to events whose start date > todayDate and start date < endDate
                        repeat with evt in calEvents
                            set evtStart to start date of evt
                            set evtEnd to end date of evt
                            set evtSummary to summary of evt
                            set end of eventList to {{summary:evtSummary, start:evtStart as text, end:evtEnd as text}}
                        end repeat
                    end tell
                end try
            end repeat
            return eventList
        end tell
        '''.format(days)

        result = await self.run_applescript(script, timeout=20)
        if result["success"]:
            # Parse events from output
            events = []
            for line in result["stdout"].split("\n"):
                if "summary:" in line:
                    events.append({"raw": line})
            return events
        return []

    # === MAIL ===

    async def get_unread_emails(self, count: int = 5) -> List[Dict]:
        """Get unread emails"""
        script = f'''
        tell application "Mail"
            set unreadList to {{}}
            repeat with msg in (messages of inbox whose read status is false)
                set msgSubject to subject of msg
                set msgSender to sender of msg
                set end of unreadList to {{subject:msgSubject, sender:msgSender}}
                if (count of unreadList) >= {count} then exit repeat
            end repeat
            return unreadList
        end tell
        '''

        result = await self.run_applescript(script, timeout=20)
        emails = []
        if result["success"]:
            for line in result["stdout"].split("\n"):
                if "subject:" in line:
                    emails.append({"raw": line})
        return emails

    # === NOTES ===

    async def get_notes(self, count: int = 10) -> List[Dict]:
        """Get notes from Notes app"""
        script = f'''
        tell application "Notes"
            set noteList to {{}}
            repeat with nt in notes
                set ntName to name of nt
                set ntBody to plaintext of nt
                set end of noteList to {{name:ntName, body:ntBody}}
                if (count of noteList) >= {count} then exit repeat
            end repeat
            return noteList
        end tell
        '''

        result = await self.run_applescript(script, timeout=15)
        notes = []
        if result["success"]:
            for line in result["stdout"].split("\n"):
                if "name:" in line:
                    notes.append({"raw": line})
        return notes

    # === REMINDERS ===

    async def get_reminders(self, due_today: bool = True) -> List[Dict]:
        """Get reminders"""
        script = '''
        tell application "Reminders"
            set remList to {}
            set todayDate to current date
            repeat with lst in lists
                repeat with rem in (reminders whose due date > todayDate - 1 * days)
                    set remTitle to name of rem
                    set remDue to due date of rem
                    set end of remList to {remTitle, remDue as text}
                end repeat
            end repeat
            return remList
        end tell
        '''

        result = await self.run_applescript(script, timeout=15)
        reminders = []
        if result["success"]:
            for line in result["stdout"].split("\n"):
                if "remTitle" in line or "{" in line:
                    reminders.append({"raw": line})
        return reminders

    # === MUSIC ===

    async def control_spotify(self, action: str, query: str = "") -> str:
        """Control Spotify playback"""
        if action == "play" and query:
            # Search and play
            return f"Searching Spotify for '{query}'..."
        elif action == "pause":
            await self.run_applescript('tell application "Spotify" to pause')
            return "Paused"
        elif action == "next":
            await self.run_applescript('tell application "Spotify" to next track')
            return "Next track"
        elif action == "previous":
            await self.run_applescript('tell application "Spotify" to previous track')
            return "Previous track"
        elif action == "play":
            await self.run_applescript('tell application "Spotify" to play')
            return "Playing"
        return f"Unknown action: {action}"

    # === CLIPBOARD ===

    async def get_clipboard(self) -> str:
        """Get clipboard content"""
        result = subprocess.run(["pbpaste"], capture_output=True, text=True)
        return result.stdout

    async def set_clipboard(self, text: str) -> str:
        """Set clipboard content"""
        proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        proc.communicate(input=text.encode())
        return "Copied to clipboard"

    # === SCREENSHOT ===

    async def screenshot(self, path: str = "") -> str:
        """Take a screenshot"""
        if not path:
            path = os.path.expanduser(
                f"~/Desktop/screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            )

        result = subprocess.run(["screencapture", "-x", path], capture_output=True)
        return f"Screenshot saved to {path}" if result.returncode == 0 else "Failed to take screenshot"


# Singleton
_mac_control = None

def get_mac_control():
    global _mac_control
    if _mac_control is None:
        _mac_control = MacControl()
    return _mac_control
