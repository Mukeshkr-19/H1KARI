"""Action result parsing should fall back only for malformed values."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from core import action_system


def test_volume_and_brightness_fall_back_for_malformed_output():
    with patch.object(
        action_system, "run_applescript", new_callable=AsyncMock
    ) as run:
        run.return_value = {"success": True, "stdout": "not-a-number"}

        assert asyncio.run(action_system.get_system_volume()) == 50
        assert asyncio.run(action_system.get_brightness()) == 50


def test_system_info_returns_successful_applescript_output():
    with patch.object(
        action_system, "run_applescript", new_callable=AsyncMock
    ) as run:
        run.return_value = {"success": True, "stdout": "macOS system data"}

        result = asyncio.run(action_system.get_system_info())

    assert result == {
        "success": True,
        "system": "macOS system data",
        "confirmation": "System info retrieved.",
    }
