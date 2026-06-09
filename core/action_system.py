"""
HIKARI Action System - Execute real system actions
Inspired by JARVIS (ethanplusai/jarvis)

Actions include:
- Open Terminal and run commands
- Open Browser and navigate to URLs
- Open Applications
- Screenshot capture
- Control system settings
"""

import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote
from typing import Optional, Dict, Any

from core.os_side_effects import osascript_disabled

log = logging.getLogger("hikari.actions")

DESKTOP_PATH = Path.home() / "Desktop"


async def run_applescript(script: str, timeout: float = 10.0) -> Dict[str, Any]:
    """Run AppleScript and return success/failure."""
    if osascript_disabled():
        return {"success": False, "error": "osascript_disabled"}

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
            "stdout": stdout.decode().strip() if success else "",
            "stderr": stderr.decode().strip() if not success else "",
        }
    except asyncio.TimeoutError:
        return {"success": False, "error": "AppleScript timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def open_terminal(command: str = "", cwd: str = "") -> Dict[str, Any]:
    """Open Terminal.app and optionally run a command."""
    if command:
        escaped = command.replace('"', '\\"')
        if cwd:
            script = f'tell application "Terminal" to activate do script "cd {cwd} && {escaped}"'
        else:
            script = f'tell application "Terminal" to activate do script "{escaped}"'
    else:
        script = 'tell application "Terminal" to activate'

    result = await run_applescript(script)

    return {
        "success": result["success"],
        "confirmation": "Terminal opened, sir."
        if result["success"]
        else "Failed to open Terminal.",
    }


async def open_browser(url: str, browser: str = "chrome") -> Dict[str, Any]:
    """Open URL in browser (Chrome or Firefox)."""
    escaped_url = url.replace('"', '\\"')

    if browser.lower() == "safari":
        script = f'tell application "Safari" to activate open location "{escaped_url}"'
    elif browser.lower() == "firefox":
        script = f'tell application "Firefox" to activate open location "{escaped_url}"'
    else:
        script = f'tell application "Google Chrome" to activate open location "{escaped_url}"'

    result = await run_applescript(script)

    browser_name = {"chrome": "Chrome", "safari": "Safari", "firefox": "Firefox"}.get(
        browser.lower(), browser
    )
    return {
        "success": result["success"],
        "confirmation": f"Opened in {browser_name}."
        if result["success"]
        else f"Failed to open {browser_name}.",
    }


async def open_application(app_name: str) -> Dict[str, Any]:
    """Open an application by name."""
    escaped = app_name.replace('"', '\\"')
    script = f'tell application "{escaped}" to activate'
    result = await run_applescript(script)

    return {
        "success": result["success"],
        "confirmation": f"Opened {app_name}."
        if result["success"]
        else f"Failed to open {app_name}.",
    }


async def get_frontmost_app() -> Optional[str]:
    """Get the name of the frontmost application."""
    script = """
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    return name of frontApp
end tell
"""
    result = await run_applescript(script)
    if result["success"]:
        return result["stdout"]
    return None


async def get_chrome_tab() -> Optional[Dict[str, str]]:
    """Get current Chrome tab title and URL."""
    script = """
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
    result = await run_applescript(script)
    if result["success"] and result["stdout"]:
        parts = result["stdout"].split("|||")
        if len(parts) == 2:
            return {"title": parts[0], "url": parts[1]}
    return None


async def get_safari_tab() -> Optional[Dict[str, str]]:
    """Get current Safari tab title and URL."""
    script = """
tell application "Safari"
    try
        set tabTitle to name of current tab of front window
        set tabURL to URL of current tab of front window
        return tabTitle & "|||" & tabURL
    on error
        return ""
    end try
end tell
"""
    result = await run_applescript(script)
    if result["success"] and result["stdout"]:
        parts = result["stdout"].split("|||")
        if len(parts) == 2:
            return {"title": parts[0], "url": parts[1]}
    return None


async def take_screenshot(
    save_path: str = "", region: str = "screen"
) -> Dict[str, Any]:
    """Take a screenshot. Region: screen, window, or selection."""
    if not save_path:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        save_path = str(DESKTOP_PATH / f"screenshot_{timestamp}.png")

    if region == "selection":
        cmd = ["screencapture", "-i", save_path]
    elif region == "window":
        cmd = ["screencapture", "-w", save_path]
    else:
        cmd = ["screencapture", save_path]

    try:
        proc = await asyncio.create_subprocess_exec(*cmd)
        await proc.communicate()

        if Path(save_path).exists():
            return {
                "success": True,
                "path": save_path,
                "confirmation": "Screenshot saved.",
            }
    except Exception as e:
        log.error(f"Screenshot failed: {e}")

    return {"success": False, "confirmation": "Screenshot failed."}


async def get_clipboard() -> Optional[str]:
    """Get clipboard text content."""
    script = "return clipboard"
    result = await run_applescript(script)
    return result["stdout"] if result["success"] else None


async def set_clipboard(text: str) -> Dict[str, Any]:
    """Set clipboard text content."""
    escaped = text.replace('"', '\\"')
    script = f'set the clipboard to "{escaped}"'
    result = await run_applescript(script)

    return {
        "success": result["success"],
        "confirmation": "Copied to clipboard."
        if result["success"]
        else "Failed to copy.",
    }


async def get_system_volume() -> int:
    """Get system volume (0-100)."""
    script = "output volume of (get system info)"
    result = await run_applescript(script)
    try:
        return int(result["stdout"]) if result["success"] else 50
    except:
        return 50


async def set_system_volume(level: int) -> Dict[str, Any]:
    """Set system volume (0-100)."""
    level = max(0, min(100, level))
    script = f"set volume output volume {level}"
    result = await run_applescript(script)

    return {
        "success": result["success"],
        "confirmation": f"Volume set to {level}%."
        if result["success"]
        else "Failed to set volume.",
    }


async def get_brightness() -> int:
    """Get screen brightness (0-100)."""
    script = "brightness of display 1"
    result = await run_applescript(script)
    try:
        return int(float(result["stdout"]) * 100) if result["success"] else 50
    except:
        return 50


async def set_brightness(level: int) -> Dict[str, Any]:
    """Set screen brightness (0-100)."""
    level = max(0, min(100, level)) / 100
    script = f"set brightness to {level}"
    result = await run_applescript(script)

    return {
        "success": result["success"],
        "confirmation": f"Brightness set to {int(level * 100)}%."
        if result["success"]
        else "Failed to set brightness.",
    }


async def list_files(directory: str = "", pattern: str = "*") -> Dict[str, Any]:
    """List files in a directory."""
    if not directory:
        directory = str(DESKTOP_PATH)

    try:
        path = Path(directory)
        if not path.exists():
            return {"success": False, "files": [], "error": "Directory not found"}

        files = [f.name for f in path.glob(pattern) if f.is_file()][:20]
        dirs = [f.name + "/" for f in path.glob("*") if f.is_dir()][:10]

        return {
            "success": True,
            "files": files,
            "directories": dirs,
            "confirmation": f"Found {len(files)} files.",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_system_info() -> Dict[str, Any]:
    """Get basic system information."""
    script = """
set info to ""
tell application "System Events"
    set info to (system info)
end tell
return info
"""
    result = await run_applescript(script)

    if result["success"]:
        try:
            # Parse the system info
            return {
                "success": True,
                "system": result["stdout"],
                "confirmation": "System info retrieved.",
            }
        except:
            pass

    return {"success": False, "error": "Failed to get system info"}


async def execute_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Main action router - execute the appropriate action."""

    action = action.lower()

    if action == "open_terminal":
        return await open_terminal(
            command=params.get("command", ""), cwd=params.get("cwd", "")
        )

    elif action == "open_browser" or action == "browse":
        url = params.get("url", "")
        if not url.startswith("http"):
            url = f"https://www.google.com/search?q={quote(url)}"
        return await open_browser(url, params.get("browser", "chrome"))

    elif action == "open_app" or action == "open_application":
        return await open_application(params.get("app", ""))

    elif action == "screenshot":
        return await take_screenshot(
            save_path=params.get("path", ""), region=params.get("region", "screen")
        )

    elif action == "get_front_app":
        app = await get_frontmost_app()
        return {"success": True, "app": app, "confirmation": f"Front app: {app}"}

    elif action == "get_chrome_tab":
        tab = await get_chrome_tab()
        return {
            "success": True,
            "tab": tab,
            "confirmation": f"Tab: {tab}" if tab else "No active tab",
        }

    elif action == "clipboard_get":
        text = await get_clipboard()
        return {
            "success": True,
            "text": text,
            "confirmation": f"Clipboard: {text[:50]}...",
        }

    elif action == "clipboard_set":
        return await set_clipboard(params.get("text", ""))

    elif action == "volume_get":
        vol = await get_system_volume()
        return {"success": True, "volume": vol, "confirmation": f"Volume: {vol}%"}

    elif action == "volume_set":
        return await set_system_volume(params.get("level", 50))

    elif action == "brightness_get":
        bright = await get_brightness()
        return {
            "success": True,
            "brightness": bright,
            "confirmation": f"Brightness: {bright}%",
        }

    elif action == "brightness_set":
        return await set_brightness(params.get("level", 50))

    elif action == "list_files":
        return await list_files(
            directory=params.get("directory", ""), pattern=params.get("pattern", "*")
        )

    else:
        return {"success": False, "confirmation": f"Unknown action: {action}"}


# Singleton instance for easy import
_action_instance = None


def get_action_system():
    """Get the action system singleton."""
    global _action_instance
    if _action_instance is None:
        _action_instance = ActionSystem()
    return _action_instance


class ActionSystem:
    """Action system wrapper class."""

    async def execute(
        self, action: str, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Execute an action."""
        if params is None:
            params = {}
        return await execute_action(action, params)

    async def open_terminal(self, command: str = "") -> Dict[str, Any]:
        return await open_terminal(command)

    async def open_browser(self, url: str, browser: str = "chrome") -> Dict[str, Any]:
        return await open_browser(url, browser)

    async def open_app(self, app: str) -> Dict[str, Any]:
        return await open_application(app)

    async def screenshot(self, path: str = "") -> Dict[str, Any]:
        return await take_screenshot(path)

    async def get_chrome_tab(self) -> Optional[Dict[str, str]]:
        return await get_chrome_tab()

    async def get_front_app(self) -> Optional[str]:
        return await get_frontmost_app()
