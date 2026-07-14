"""
HIKARI v2.0 - System Agent
App launching, system control, time, calendar
"""

import os
import sys
import subprocess
import webbrowser
import time
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path

from agents.base import BaseAgent
from core.command_intent import (
    is_phone_call_command,
    phone_call_target,
    system_agent_confidence,
)
from core.os_side_effects import osascript_disabled


class SystemAgent(BaseAgent):
    """Handles system operations: apps, websites, system info"""

    def __init__(self):
        super().__init__(
            "system", "System operations, app launching, and device control"
        )
        self._app_cache = {}

        self.register_tool("open_app", self.open_app)
        self.register_tool("open_website", self.open_website)
        self.register_tool("get_system_info", self.get_system_info)
        self.register_tool("get_battery", self.get_battery)
        self.register_tool("make_call", self.make_call)

    def handle(self, user_input: str, context: str = "") -> Optional[str]:
        lowered = user_input.lower().strip()

        # Empty trash
        if "trash" in lowered or "bin" in lowered:
            return self.system_control(user_input)

        # Quit/close app commands
        if "quit" in lowered or "close" in lowered or "kill" in lowered:
            return self.control_app(user_input)

        # Restart/shutdown commands
        if any(
            word in lowered
            for word in ["restart", "reboot", "shutdown", "shut down"]
        ):
            return self.system_control(user_input)

        # Sleep/lock commands
        if any(w in lowered for w in ["sleep", "lock screen", "lock mac"]):
            return self.system_control(user_input)

        # Play music commands
        if lowered.startswith("play ") or lowered == "play" or "play" in lowered:
            return self.control_music(user_input)

        # Pause/stop music
        if any(w in lowered for w in ["stop music", "pause music", "stop playing"]):
            return self.control_music("pause")

        # Phone / FaceTime call — not nicknames like "call me baby"
        if is_phone_call_command(user_input):
            target = phone_call_target(user_input)
            if target:
                return self.make_call(target)

        # Open facetime directly (without "call")
        if lowered == "facetime" or lowered.startswith("open facetime"):
            return self.open_app("FaceTime")

        # Open app - only trigger with explicit "open" command
        if lowered.startswith("open ") or lowered.startswith("open "):
            # Extract the app name - handle "open facetime", "open chrome", etc.
            if "app" in lowered:
                app = lowered.replace("open", "").replace("app", "").strip()
            else:
                # "open facetime" -> "facetime"
                app = lowered.replace("open", "").strip()

            if app:
                return self.open_app(app)
        elif lowered.startswith("launch "):
            app = lowered.replace("launch", "").strip()
            return self.open_app(app)

        # Open website
        elif lowered.startswith("open "):
            site = lowered.replace("open", "").strip()
            if site and not site.endswith(("file", "document")):
                return self.open_website(site)

        # System info
        if any(
            w in lowered
            for w in [
                "system info",
                "system status",
                "how much space",
                "disk space",
                "memory",
            ]
        ):
            return self.get_system_info()
        if any(w in lowered for w in ["battery", "charge", "power"]):
            return self.get_battery()

        return None

    def can_handle(self, user_input: str) -> float:
        return system_agent_confidence(user_input)

    def control_music(self, command: str) -> str:
        """Control Spotify/Music playback - fixed to actually search and play"""
        cmd = command.lower()
        if osascript_disabled():
            return "Music control is disabled during QA."

        try:
            # Make sure Spotify is running
            subprocess.run(["open", "-a", "Spotify"], timeout=5)
            time.sleep(1)

            if "pause" in cmd or "stop playing" in cmd:
                script = 'tell application "Spotify" to pause'
                subprocess.run(["osascript", "-e", script], timeout=10)
                return "Paused."

            elif "next" in cmd:
                script = 'tell application "Spotify" to next track'
                subprocess.run(["osascript", "-e", script], timeout=10)
                return "Next track."

            elif "previous" in cmd or "back" in cmd:
                script = 'tell application "Spotify" to previous track'
                subprocess.run(["osascript", "-e", script], timeout=10)
                return "Previous track."

            elif "volume" in cmd:
                if "up" in cmd or "louder" in cmd:
                    script = "set volume output volume ((output volume of (get volume settings)) + 20)"
                elif "down" in cmd or "quieter" in cmd:
                    script = "set volume output volume ((output volume of (get volume settings)) - 20)"
                else:
                    return "Volume up or down?"
                subprocess.run(["osascript", "-e", script], timeout=10)
                return "Volume adjusted."

            elif "play" in cmd:
                # Extract search query
                search = cmd.replace("play", "").strip()

                if not search:
                    script = 'tell application "Spotify" to play'
                    subprocess.run(["osascript", "-e", script], timeout=10)
                    return "Playing."

                # Activate Spotify
                subprocess.run(
                    ["osascript", "-e", 'tell application "Spotify" to activate'],
                    timeout=5,
                )
                time.sleep(0.5)

                # Save current clipboard
                old_clip = subprocess.run(["pbpaste"], capture_output=True, text=True)

                # Copy search to clipboard
                p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                p.communicate(input=search.encode())

                # Cmd+L to focus search, Cmd+V to paste, Enter
                script = """
                tell application "System Events"
                    keystroke "l" using command down
                    delay 0.3
                    keystroke "v" using command down
                    delay 0.5
                    keystroke return
                end tell
                """
                try:
                    subprocess.run(["osascript", "-e", script], timeout=10)
                    time.sleep(2)
                except:
                    pass

                # Restore clipboard
                p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                p.communicate(
                    input=old_clip.stdout.encode() if old_clip.stdout else b""
                )

                return f"Searching Spotify for '{search}'..."

            return "Say 'play', 'pause', 'next', or 'volume up/down'"

        except Exception as e:
            return f"Music error: {str(e)[:50]}"

    def open_app(self, app_name: str) -> str:
        """Open app - if not found, try as website"""
        if not app_name:
            return "Which app would you like to open?"

        app_name = app_name.strip()

        # Map common names
        app_map = {
            "brave": "Brave Browser",
            "chrome": "Google Chrome",
            "safari": "Safari",
            "firefox": "Firefox",
            "slack": "Slack",
            "discord": "Discord",
            "spotify": "Spotify",
            "whatsapp": "WhatsApp",
            "music": "Music",
            "notes": "Notes",
            "calendar": "Calendar",
            "mail": "Mail",
            "messages": "Messages",
            "facetime": "FaceTime",
            "terminal": "Terminal",
            "vscode": "Visual Studio Code",
            "pycharm": "PyCharm",
            "telegram": "Telegram",
            "signal": "Signal",
            "zoom": "Zoom",
            "teams": "Microsoft Teams",
        }

        # Check mapped name first
        lookup_name = app_map.get(app_name.lower(), app_name)

        try:
            if sys.platform == "darwin":
                # First: try to find app using mdfind
                result = subprocess.run(
                    [
                        "mdfind",
                        f"kMDItemKind==Application && (casedge=='{lookup_name}' || casedge=='{app_name}')",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.stdout.strip():
                    # Found app!
                    app_path = result.stdout.strip().split("\n")[0]
                    subprocess.Popen(["open", app_path])
                    return f"Opening {app_name}..."

                # Second: try open -a command
                result2 = subprocess.run(
                    ["open", "-a", lookup_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result2.returncode == 0:
                    return f"Opening {app_name}..."

                # Third: try with original name
                result3 = subprocess.run(
                    ["open", "-a", app_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result3.returncode == 0:
                    return f"Opening {app_name}..."

                # NOT FOUND - try as website instead!
                return self.open_website(app_name)

            else:
                subprocess.Popen([app_name])
                return f"Opening {app_name}..."
        except Exception as e:
            return f"Couldn't open {app_name}: {str(e)}"

    def open_website(self, site: str) -> str:
        """Open a website in browser"""
        if not site:
            return "Which website would you like to open?"

        site = site.replace("website", "").replace("site", "").strip()

        # Known websites
        website_map = {
            "youtube": "https://youtube.com",
            "twitter": "https://twitter.com",
            "x": "https://x.com",
            "instagram": "https://instagram.com",
            "facebook": "https://facebook.com",
            "reddit": "https://reddit.com",
            "gmail": "https://gmail.com",
            "google": "https://google.com",
            "github": "https://github.com",
            "linkedin": "https://linkedin.com",
            "netflix": "https://netflix.com",
            "spotify": "https://spotify.com",
            "whatsapp": "https://web.whatsapp.com",
            "telegram": "https://web.telegram.org",
            "discord": "https://discord.com",
            "unfoldstudio": "https://unfoldstudio.net",
            "unfold": "https://unfoldstudio.net",
        }

        # Check known websites first
        if site.lower() in website_map:
            url = website_map[site.lower()]
        elif "." not in site:
            # Try common domains
            url = f"https://www.{site}.com"
        else:
            url = f"https://{site}" if not site.startswith("http") else site

        try:
            if osascript_disabled():
                return f"Opening {site}..."
            webbrowser.open(url)
            return f"Opening {site}..."
        except Exception as e:
            return f"Couldn't open {site}: {str(e)}"

    def control_app(self, command: str) -> str:
        """Control apps - quit, close, kill"""
        cmd = command.lower()

        # Extract app name
        app_name = (
            cmd.replace("quit", "").replace("close", "").replace("kill", "").strip()
        )

        if not app_name:
            return "Which app to quit?"

        # Map common names
        app_map = {
            "spotify": "Spotify",
            "chrome": "Google Chrome",
            "brave": "Brave Browser",
            "safari": "Safari",
            "slack": "Slack",
            "discord": "Discord",
            "whatsapp": "WhatsApp",
            "telegram": "Telegram",
            "zoom": "Zoom",
            "teams": "Microsoft Teams",
            "vscode": "Visual Studio Code",
            "pycharm": "PyCharm",
            "terminal": "Terminal",
            "notes": "Notes",
            "messages": "Messages",
            "mail": "Mail",
            "facetime": "FaceTime",
        }

        lookup = app_map.get(app_name, app_name.title())

        try:
            if osascript_disabled():
                return f"App control is disabled during QA for {lookup}."
            script = f'tell application "{lookup}" to quit'
            subprocess.run(["osascript", "-e", script], timeout=10)
            return f"Quit {lookup}."
        except:
            return f"Couldn't quit {app_name}"

    def system_control(self, command: str) -> str:
        """Control Mac system - sleep, lock, restart"""
        cmd = command.lower().strip()

        confirmation_rules = (
            (("empty trash", "empty the trash", "empty bin"), "confirm empty trash"),
            (("restart", "reboot"), "confirm restart"),
            (("shutdown", "shut down"), "confirm shutdown"),
            (("sleep",), "confirm sleep"),
        )
        for keywords, confirmation in confirmation_rules:
            if any(keyword in cmd for keyword in keywords) and cmd != confirmation:
                return f"Confirmation required: say '{confirmation}'."

        try:
            if "sleep" in cmd:
                subprocess.run(["pmset", "sleepnow"], timeout=5)
                return "Going to sleep..."
            elif "lock" in cmd or "lock screen" in cmd:
                subprocess.run(
                    [
                        "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",
                        "-suspend",
                    ],
                    timeout=5,
                )
                return "Locking screen..."
            elif "restart" in cmd or "reboot" in cmd:
                if osascript_disabled():
                    return "Restart is disabled during QA."
                subprocess.run(
                    ["osascript", "-e", 'tell app "System Events" to restart'],
                    timeout=5,
                )
                return "Restarting..."
            elif "shutdown" in cmd or "shut down" in cmd:
                if osascript_disabled():
                    return "Shutdown is disabled during QA."
                subprocess.run(
                    ["osascript", "-e", 'tell app "System Events" to shutdown'],
                    timeout=5,
                )
                return "Shutting down..."
            elif "empty trash" in cmd or "empty the trash" in cmd or "empty bin" in cmd:
                # macOS blocks direct access to Trash - need to use Finder
                # Check if we can access
                trash_path = os.path.expanduser("~/.Trash")

                try:
                    files = os.listdir(trash_path)
                except PermissionError:
                    return "Need Full Disk Access permission. Go to System Settings > Privacy & Security > Full Disk Access, then enable for Python/Anaconda."
                except Exception as e:
                    return f"Can't access trash: {str(e)}"

                if not files:
                    return "Trash is already empty."

                # Try AppleScript - this is the proper macOS way
                if osascript_disabled():
                    return "Empty trash is disabled during QA."
                try:
                    result = subprocess.run(
                        ["osascript", "-e", 'tell app "Finder" to empty trash'],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.returncode == 0:
                        return f"Emptyed trash. Removed {len(files)} items."
                except Exception as e:
                    pass

                # If AppleScript didn't work, tell user
                return "Couldn't empty trash. macOS blocked it. You may need to grant Full Disk Access to Python in System Settings > Privacy & Security > Full Disk Access."
            else:
                return "Say 'sleep', 'lock', 'restart', or 'shutdown'"
        except Exception as e:
            return f"Couldn't do that: {str(e)}"

    def get_system_info(self) -> str:
        """Get system information"""
        info = []

        try:
            if sys.platform == "darwin":
                # Disk usage
                result = subprocess.run(
                    ["df", "-h", "/"], capture_output=True, text=True
                )
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    parts = lines[1].split()
                    info.append(f"Disk: {parts[3]} available of {parts[1]}")

                # Memory
                result = subprocess.run(
                    ["memory_pressure"], capture_output=True, text=True
                )
                if "System-wide memory free percentage" in result.stdout:
                    pct = result.stdout.split(":")[-1].strip().replace("%", "")
                    info.append(f"Memory: {pct}% free")

                # Uptime
                result = subprocess.run(["uptime"], capture_output=True, text=True)
                info.append(f"Uptime: {result.stdout.strip()}")

            info.append(f"Platform: {sys.platform}")
            info.append(f"Python: {sys.version.split()[0]}")

        except Exception as e:
            info.append(f"Error gathering info: {str(e)}")

        return "System Info:\n" + "\n".join(info)

    def get_battery(self) -> str:
        """Get battery information"""
        try:
            if sys.platform == "darwin":
                result = subprocess.run(
                    ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5
                )
                output = result.stdout.strip()
                if output:
                    return f"Battery: {output}"
                return "Could not read battery info"
            else:
                return "Battery info only available on macOS"
        except Exception as e:
            return f"Battery error: {str(e)}"

    def make_call(self, name: str) -> str:
        """Make a FaceTime call - opens FaceTime and searches for contact"""
        if not name:
            return "Who would you like to call?"

        name = name.strip().lower()
        if osascript_disabled():
            return "FaceTime calling is disabled during QA."

        # Open FaceTime with the name - it will search your Contacts
        try:
            # Use AppleScript to search Contacts and call
            script = f'''
            tell application "FaceTime"
                activate
                make new incoming call to "{name}"
            end tell
            '''
            subprocess.run(["osascript", "-e", script], timeout=10)
            return f"Calling {name.title()}..."
        except Exception as e:
            # Fallback: just open FaceTime
            try:
                subprocess.Popen(["open", "-a", "FaceTime"])
                return f"Opening FaceTime to call {name}..."
            except Exception as e2:
                return f"Couldn't make call: {str(e2)}"

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        status.update(
            {
                "platform": sys.platform,
                "python_version": sys.version.split()[0],
            }
        )
        return status
