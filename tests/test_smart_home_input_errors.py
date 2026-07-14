"""Smart-home input fallbacks must not hide unrelated runtime failures."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core.smart_home import SmartHome


def test_thermostat_rejects_non_numeric_temperature():
    result = asyncio.run(SmartHome().set_thermostat("warm"))

    assert result == "Please specify a temperature (e.g., 'set thermostat to 72')"


def test_thermostat_does_not_misreport_runtime_failure_as_bad_input():
    smart_home = SmartHome()

    with patch.object(
        smart_home,
        "run_shortcut",
        new_callable=AsyncMock,
        side_effect=RuntimeError("runtime failure"),
    ), pytest.raises(RuntimeError, match="runtime failure"):
        asyncio.run(smart_home.set_thermostat(72))
