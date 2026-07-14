"""Disruptive system actions require an exact user confirmation phrase."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.system import SystemAgent


@pytest.mark.parametrize(
    ("command", "confirmation"),
    [
        ("sleep", "confirm sleep"),
        ("restart", "confirm restart"),
        ("shutdown", "confirm shutdown"),
        ("empty trash", "confirm empty trash"),
    ],
)
def test_disruptive_action_stops_before_side_effects(command, confirmation):
    agent = SystemAgent()

    with patch("agents.system.subprocess.run") as run, patch(
        "agents.system.os.listdir"
    ) as listdir:
        result = agent.system_control(command)

    assert result == f"Confirmation required: say '{confirmation}'."
    run.assert_not_called()
    listdir.assert_not_called()


def test_exact_restart_confirmation_runs_the_action():
    agent = SystemAgent()

    with patch("agents.system.osascript_disabled", return_value=False), patch(
        "agents.system.subprocess.run", return_value=MagicMock(returncode=0)
    ) as run:
        result = agent.system_control("confirm restart")

    assert result == "Restarting..."
    run.assert_called_once_with(
        ["osascript", "-e", 'tell app "System Events" to restart'],
        timeout=5,
    )


def test_lock_screen_remains_directly_available():
    agent = SystemAgent()

    with patch("agents.system.subprocess.run") as run:
        result = agent.system_control("lock screen")

    assert result == "Locking screen..."
    run.assert_called_once()


def test_confirmed_shutdown_is_reachable_through_agent_router():
    agent = SystemAgent()

    with patch("agents.system.osascript_disabled", return_value=False), patch(
        "agents.system.subprocess.run", return_value=MagicMock(returncode=0)
    ) as run:
        result = agent.handle("confirm shutdown")

    assert result == "Shutting down..."
    run.assert_called_once()
