"""Voice enrollment must temporarily own the microphone and restore the daemon."""

from __future__ import annotations

from types import SimpleNamespace

import hikari
from services import hikari_daemon


def _result(code: int):
    return SimpleNamespace(returncode=code)


def test_enrollment_pauses_and_restarts_running_login_agent(monkeypatch):
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return _result(0)

    monkeypatch.setattr(hikari.sys, "platform", "darwin")
    monkeypatch.setattr(hikari.os, "getuid", lambda: 501)
    monkeypatch.setattr(hikari.subprocess, "run", run)
    monkeypatch.setattr(hikari_daemon, "initialize_audio_backends", lambda: True)
    monkeypatch.setattr(hikari_daemon, "enroll_voice", lambda: True)

    assert hikari.run_voice_enrollment() == 0
    assert calls[0] == [
        "launchctl",
        "print",
        "gui/501/com.hikari.assistant",
    ]
    assert calls[1] == [
        "launchctl",
        "bootout",
        "gui/501/com.hikari.assistant",
    ]
    assert calls[2][0:3] == ["launchctl", "bootstrap", "gui/501"]
    assert calls[2][3].endswith("Library/LaunchAgents/com.hikari.assistant.plist")


def test_enrollment_does_not_start_agent_that_was_not_running(monkeypatch):
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return _result(1)

    monkeypatch.setattr(hikari.sys, "platform", "darwin")
    monkeypatch.setattr(hikari.subprocess, "run", run)
    monkeypatch.setattr(hikari_daemon, "initialize_audio_backends", lambda: True)
    monkeypatch.setattr(hikari_daemon, "enroll_voice", lambda: True)

    assert hikari.run_voice_enrollment() == 0
    assert len(calls) == 1
    assert calls[0][0:2] == ["launchctl", "print"]


def test_enrollment_restores_listener_after_capture_failure(monkeypatch):
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return _result(0)

    monkeypatch.setattr(hikari.sys, "platform", "darwin")
    monkeypatch.setattr(hikari.subprocess, "run", run)
    monkeypatch.setattr(hikari_daemon, "initialize_audio_backends", lambda: True)
    monkeypatch.setattr(hikari_daemon, "enroll_voice", lambda: False)

    assert hikari.run_voice_enrollment() == 1
    assert [call[1] for call in calls] == ["print", "bootout", "bootstrap"]
