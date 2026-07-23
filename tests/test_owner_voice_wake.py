"""Contracts for owner-enrolled HIKARI wake-word mode."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_cli_routes_daemon_to_speaker_locked_service() -> None:
    source = (ROOT / "hikari.py").read_text(encoding="utf-8")

    run_start = source.index("def run_daemon():")
    run_end = source.index("def run_voice_enrollment():", run_start)
    block = source[run_start:run_end]
    assert "services.hikari_daemon" in block
    assert "services.hikari_service" not in block
    assert '"--enroll-voice"' in source


def test_install_prompts_for_explicit_owner_enrollment() -> None:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "Enroll your voice now?" in source
    assert "hikari.py\" --enroll-voice" in source
    assert "Wake-word mode stays locked" in source


def test_login_agent_uses_locked_daemon_and_requires_enrollment() -> None:
    source = (ROOT / "scripts" / "install-hikari-login-agent.sh").read_text(
        encoding="utf-8"
    )

    assert 'DAEMON="$REPO_ROOT/services/hikari_daemon.py"' in source
    assert "--check-enrollment" in source
    assert "Enroll your voice now?" in source
    assert "Owner voice is not enrolled" in source
    assert "hikari_simple.py" not in source


def test_voice_daemon_has_no_open_activation_fallback() -> None:
    source = (ROOT / "services" / "hikari_daemon.py").read_text(encoding="utf-8")

    assert "activation is currently open" not in source
    assert "No enrolled voice; activation is currently open" not in source
    assert "Missing enrollment or unavailable verification always fails closed" in source
    assert "create_conversation_session_store" in source
    assert 'process_input(text, source="voice")' in source


def test_enrollment_and_daemon_docs_use_public_cli() -> None:
    source = (ROOT / "docs" / "QUICKSTART.md").read_text(encoding="utf-8")

    assert ".venv/bin/python hikari.py --enroll-voice" in source
    assert ".venv/bin/python hikari.py --daemon" in source
    assert "refuses to start until the local owner voice is" in source


def test_cli_install_uses_the_reviewed_login_agent_script() -> None:
    source = (ROOT / "hikari.py").read_text(encoding="utf-8")

    start = source.index("def install_service():")
    end = source.index("def run_repo_script", start)
    block = source[start:end]
    assert "install-hikari-login-agent.sh" in block
    assert "com.hikari.ai.plist" not in block
