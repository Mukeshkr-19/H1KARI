from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.tasks.scheduler import MacOSReminderScheduler, task_scheduler_enabled


def test_legacy_reminder_side_effect_cannot_be_enabled_by_environment(monkeypatch):
    monkeypatch.setenv("HIKARI_ENABLE_TASK_SCHEDULER", "1")

    with patch("core.tasks.scheduler.subprocess.run") as run:
        result = MacOSReminderScheduler().schedule_reminder(title="private task")

    assert task_scheduler_enabled() is False
    assert result.ok is False
    assert result.error == "scheduler_disabled"
    run.assert_not_called()


def test_orchestrator_source_does_not_import_or_instantiate_legacy_tool_surfaces():
    source = (Path(__file__).resolve().parents[1] / "core" / "orchestrator.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "agents.research",
        "agents.files",
        "agents.system",
        "get_action_system",
        "get_desktop_awareness",
        "get_browser_automation",
        "get_mac_integration",
        "get_mac_control",
        "get_smart_home",
        "get_build_executor",
        "setup_default_scheduler",
    )

    assert not any(name in source for name in forbidden)
