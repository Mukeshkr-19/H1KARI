"""The listening service must finish speaking before it listens again."""

from __future__ import annotations

from unittest.mock import patch

from services.hikari_service import HIKARI_Daemon


def test_service_speech_waits_for_say_process():
    daemon = object.__new__(HIKARI_Daemon)

    with patch("services.hikari_service.sys.platform", "darwin"), patch(
        "services.hikari_service.subprocess.run"
    ) as run:
        daemon.speak("hello <there>")

    run.assert_called_once_with(
        ["say", "-r", "180", "hello there"],
        timeout=30,
        check=False,
    )
