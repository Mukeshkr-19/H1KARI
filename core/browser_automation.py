"""
HIKARI Browser Automation - Web browsing capabilities
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List
from urllib.parse import quote

log = logging.getLogger("hikari.browser")


async def run_applescript(script: str, timeout: float = 10.0) -> Dict[str, Any]:
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
    except Exception as e:
        return {"success": False, "error": str(e)}


async def open_url(url: str, browser: str = "chrome") -> Dict[str, Any]:
    """Open a URL in the specified browser."""

    escaped_url = url.replace('"', '\\"')

    browsers = {
        "chrome": "Google Chrome",
        "safari": "Safari",
        "firefox": "Firefox",
        "arc": "Arc",
        "brave": "Brave Browser",
    }

    app_name = browsers.get(browser.lower(), "Google Chrome")

    script = f'tell application "{app_name}" to activate open location "{escaped_url}"'
    result = await run_applescript(script)

    return {
        "success": result["success"],
        "confirmation": f"Opened in {app_name}."
        if result["success"]
        else "Failed to open browser.",
    }


async def search_google(query: str, browser: str = "chrome") -> Dict[str, Any]:
    """Search Google for a query."""
    url = f"https://www.google.com/search?q={quote(query)}"
    return await open_url(url, browser)


async def search_youtube(query: str, browser: str = "chrome") -> Dict[str, Any]:
    """Search YouTube for a video."""
    url = f"https://www.youtube.com/results?search_query={quote(query)}"
    return await open_url(url, browser)


async def search_github(query: str, browser: str = "chrome") -> Dict[str, Any]:
    """Search GitHub for repositories."""
    url = f"https://github.com/search?q={quote(query)}"
    return await open_url(url, browser)


async def get_chrome_tabs() -> List[Dict[str, str]]:
    """Get list of open Chrome tabs."""

    script = """
tell application "Google Chrome"
    set tabList to {}
    repeat with win in windows
        repeat with tab in tabs of win
            set tabTitle to title of tab
            set tabURL to URL of tab
            set end of tabList to tabTitle & "|||" & tabURL
        end repeat
    end repeat
    return tabList
end tell
"""

    result = await run_applescript(script)

    if not result["success"]:
        return []

    tabs = []
    for line in result["stdout"].split("\n"):
        if "|||" in line:
            parts = line.split("|||")
            if len(parts) >= 2:
                tabs.append({"title": parts[0], "url": parts[1]})

    return tabs


async def get_safari_tabs() -> List[Dict[str, str]]:
    """Get list of open Safari tabs."""

    script = """
tell application "Safari"
    set tabList to {}
    repeat with win in windows
        repeat with tab in tabs of win
            set tabTitle to name of tab
            set tabURL to URL of tab
            set end of tabList to tabTitle & "|||" & tabURL
        end repeat
    end repeat
    return tabList
end tell
"""

    result = await run_applescript(script)

    if not result["success"]:
        return []

    tabs = []
    for line in result["stdout"].split("\n"):
        if "|||" in line:
            parts = line.split("|||")
            if len(parts) >= 2:
                tabs.append({"title": parts[0], "url": parts[1]})

    return tabs


async def close_chrome_tab(url_contains: str = "") -> Dict[str, Any]:
    """Close a Chrome tab that contains a specific URL."""

    if url_contains:
        script = f'''
tell application "Google Chrome"
    repeat with win in windows
        repeat with tab in tabs of win
            if URL of tab contains "{url_contains}" then
                close tab
                return "closed"
            end if
        end repeat
    end repeat
    return "not found"
end tell
'''
    else:
        script = """
tell application "Google Chrome"
    close active tab of front window
    return "closed"
end tell
"""

    result = await run_applescript(script)

    return {
        "success": result["success"] and result["stdout"] == "closed",
        "confirmation": "Tab closed." if result["success"] else "Failed to close tab.",
    }


class BrowserAutomation:
    """Browser automation wrapper."""

    async def open(self, url: str, browser: str = "chrome") -> Dict[str, Any]:
        return await open_url(url, browser)

    async def search(self, query: str, browser: str = "chrome") -> Dict[str, Any]:
        return await search_google(query, browser)

    async def youtube(self, query: str, browser: str = "chrome") -> Dict[str, Any]:
        return await search_youtube(query, browser)

    async def github(self, query: str, browser: str = "chrome") -> Dict[str, Any]:
        return await search_github(query, browser)

    async def get_tabs(self, browser: str = "chrome") -> List[Dict[str, str]]:
        if browser.lower() == "safari":
            return await get_safari_tabs()
        return await get_chrome_tabs()

    async def close_tab(
        self, url_contains: str = "", browser: str = "chrome"
    ) -> Dict[str, Any]:
        return await close_chrome_tab(url_contains)


# Singleton
_browser_instance = None


def get_browser_automation():
    """Get the browser automation singleton."""
    global _browser_instance
    if _browser_instance is None:
        _browser_instance = BrowserAutomation()
    return _browser_instance
