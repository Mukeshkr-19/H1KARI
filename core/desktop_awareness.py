"""
HIKARI Desktop Awareness - See what's on your screen
Inspired by JARVIS (ethanplusai/jarvis)

Features:
- Screenshot capture (full screen, window, selection)
- OCR text extraction from screenshots
- Active window/app detection
- Screen region analysis
"""

import asyncio
import base64
import io
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

log = logging.getLogger("hikari.desktop")

DESKTOP_PATH = Path.home() / "Desktop"


async def run_command(cmd: List[str], timeout: float = 30.0) -> Dict[str, Any]:
    """Run a shell command and return output."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        return {
            "success": proc.returncode == 0,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
        }
    except asyncio.TimeoutError:
        return {"success": False, "error": "Command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def capture_screen(
    save_path: str = "", include_cursor: bool = True
) -> Dict[str, Any]:
    """Capture the entire screen."""
    if not save_path:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        save_path = str(DESKTOP_PATH / f"screen_{timestamp}.png")

    cursor_flag = "-C" if include_cursor else ""
    cmd = ["screencapture", cursor_flag, save_path]

    result = await run_command(cmd)

    if result["success"] and Path(save_path).exists():
        size = Path(save_path).stat().st_size
        return {
            "success": True,
            "path": save_path,
            "size": size,
            "confirmation": f"Screenshot saved to {save_path}",
        }

    return {
        "success": False,
        "confirmation": "Failed to capture screen",
        "error": result.get("stderr", "Unknown error"),
    }


async def capture_window(window_name: str = "") -> Dict[str, Any]:
    """Capture a specific window by name."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    save_path = str(DESKTOP_PATH / f"window_{timestamp}.png")

    # Use screencapture with window selection
    cmd = ["screencapture", "-w", save_path]
    result = await run_command(cmd)

    if result["success"] and Path(save_path).exists():
        return {
            "success": True,
            "path": save_path,
            "confirmation": f"Window screenshot saved",
        }

    return {
        "success": False,
        "confirmation": "Failed to capture window",
    }


async def capture_region(x: int, y: int, width: int, height: int) -> Dict[str, Any]:
    """Capture a specific screen region."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    save_path = str(DESKTOP_PATH / f"region_{timestamp}.png")

    # Use screencapture with geometry
    cmd = ["screencapture", "-x", f"-R{x},{y},{width},{height}", save_path]
    result = await run_command(cmd)

    if result["success"] and Path(save_path).exists():
        return {
            "success": True,
            "path": save_path,
            "region": {"x": x, "y": y, "width": width, "height": height},
            "confirmation": f"Region screenshot saved",
        }

    return {
        "success": False,
        "confirmation": "Failed to capture region",
    }


async def get_active_window() -> Optional[Dict[str, str]]:
    """Get the currently active window information."""
    from core.os_side_effects import osascript_disabled

    if osascript_disabled():
        return None

    script = """
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    try
        set windowTitle to name of first window of frontApp
    on error
        set windowTitle to ""
    end try
    return appName & "|||" & windowTitle
end tell
"""

    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        if proc.returncode == 0:
            result = stdout.decode().strip()
            if "|||" in result:
                parts = result.split("|||")
                return {
                    "app": parts[0],
                    "window": parts[1] if len(parts) > 1 else "",
                }
            return {"app": result, "window": ""}
    except:
        pass

    return None


async def list_windows() -> List[Dict[str, str]]:
    """List all open windows."""
    from core.os_side_effects import osascript_disabled

    if osascript_disabled():
        return []

    script = """
tell application "System Events"
    set windowList to {}
    repeat with proc in application processes
        try
            set procName to name of proc
            repeat with win in windows of proc
                try
                    set winName to name of win
                    set end of windowList to procName & "|||" & winName
                on error
                end try
            end repeat
        on error
        end try
    end repeat
    return windowList
end tell
"""

    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

        if proc.returncode == 0:
            result = stdout.decode().strip()
            windows = []
            for line in result.split("\n"):
                if "|||" in line:
                    parts = line.split("|||")
                    windows.append({"app": parts[0], "window": parts[1]})
            return windows
    except:
        pass

    return []


async def get_running_apps() -> List[str]:
    """Get list of running applications."""
    from core.os_side_effects import osascript_disabled

    if osascript_disabled():
        return []

    script = """
tell application "System Events"
    set appList to name of every application process whose background only is false
    return appList as text
end tell
"""

    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        if proc.returncode == 0:
            apps = stdout.decode().strip().split(", ")
            return [a.strip() for a in apps if a.strip()]
    except:
        pass

    return []


async def analyze_screen_content() -> Dict[str, Any]:
    """Get a summary of what's on screen."""
    active = await get_active_window()
    running = await get_running_apps()
    windows = await list_windows()

    return {
        "active_window": active,
        "running_apps": running,
        "open_windows": windows[:10],  # Limit to 10
        "app_count": len(running),
        "window_count": len(windows),
    }


async def take_screenshot_with_analysis() -> Dict[str, Any]:
    """Take a screenshot and analyze it."""
    # Capture screen
    capture_result = await capture_screen()

    if not capture_result["success"]:
        return capture_result

    # Get active window info
    active = await get_active_window()

    return {
        "success": True,
        "path": capture_result["path"],
        "active_window": active,
        "confirmation": f"Screenshot captured. Active: {active.get('app', 'Unknown')}",
    }


class DesktopAwareness:
    """Desktop awareness wrapper."""

    def __init__(self):
        self.last_screenshot = None

    async def capture(self, mode: str = "screen", path: str = "") -> Dict[str, Any]:
        """Capture screen/window/region."""
        if mode == "window":
            return await capture_window()
        elif mode == "region":
            return await capture_region(0, 0, 800, 600)  # Default region
        else:
            return await capture_screen(save_path=path)

    async def analyze(self) -> Dict[str, Any]:
        """Analyze current screen content."""
        return await analyze_screen_content()

    async def get_active(self) -> Optional[Dict[str, str]]:
        """Get active window."""
        return await get_active_window()

    async def get_apps(self) -> List[str]:
        """Get running apps."""
        return await get_running_apps()

    async def screenshot_and_analyze(self) -> Dict[str, Any]:
        """Take screenshot and get context."""
        return await take_screenshot_with_analysis()


# Singleton
_desktop_instance = None


def get_desktop_awareness():
    """Get the desktop awareness singleton."""
    global _desktop_instance
    if _desktop_instance is None:
        _desktop_instance = DesktopAwareness()
    return _desktop_instance
