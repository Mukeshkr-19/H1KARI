"""Browser automation must quote untrusted AppleScript strings."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from core import browser_automation


def test_open_url_quotes_applescript_metacharacters():
    url = 'https://example.test/\\"\nend tell\ndo shell script "bad"'

    with patch.object(
        browser_automation, "run_applescript", new_callable=AsyncMock
    ) as run:
        run.return_value = {"success": True}
        asyncio.run(browser_automation.open_url(url))

    script = run.await_args.args[0]
    assert '\\\\' in script
    assert '\\"' in script
    assert '\\nend tell\\ndo shell script' in script
    assert "\nend tell\n" not in script


def test_close_tab_quotes_url_filter():
    url_filter = 'safe" then\nclose every window\n--'

    with patch.object(
        browser_automation, "run_applescript", new_callable=AsyncMock
    ) as run:
        run.return_value = {"success": True, "stdout": "closed"}
        result = asyncio.run(browser_automation.close_chrome_tab(url_filter))

    script = run.await_args.args[0]
    assert 'contains "safe\\" then\\nclose every window\\n--" then' in script
    assert "\nclose every window\n" not in script
    assert result["success"] is True
