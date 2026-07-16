from __future__ import annotations

import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from hikari import run_document_cli


ROOT = Path(__file__).resolve().parents[1]


def _args(**updates):
    values = {
        "explain_document": None,
        "document_task": None,
        "document_follow_up": None,
        "document_provider": None,
        "confirm_document": None,
    }
    values.update(updates)
    return Namespace(**values)


def _runtime():
    documents = MagicMock()
    documents.prepare.return_value = SimpleNamespace(
        task_id="task-1", status="queued", error_code=None, explanation=None, provider=None
    )
    documents.confirm_and_explain.return_value = SimpleNamespace(
        task_id="task-1", status="completed", error_code=None,
        explanation="Explanation", provider="ollama",
    )
    documents.reconnect.return_value = SimpleNamespace(
        task_id="task-1", status="running", error_code=None, explanation=None, provider=None
    )
    documents.follow_up.return_value = documents.confirm_and_explain.return_value
    tasks = MagicMock()
    tasks.get_task.return_value = SimpleNamespace(selected_path="/tmp/report.txt")
    return SimpleNamespace(documents=documents, tasks=tasks)


def test_prepare_is_safe_without_confirmation(capsys):
    runtime = _runtime()
    code = run_document_cli(_args(explain_document="/tmp/report.txt"), runtime)

    assert code == 0
    runtime.documents.prepare.assert_called_once()
    runtime.documents.confirm_and_explain.assert_not_called()
    output = capsys.readouterr().out
    assert "Selected document: /tmp/report.txt" in output
    assert "Confirmation required" in output


def test_exact_confirmation_shows_path_and_providers_before_explanation(capsys):
    runtime = _runtime()
    code = run_document_cli(_args(
        explain_document="/tmp/report.txt",
        document_provider=["ollama", "google"],
        confirm_document="READ_AND_SEND",
    ), runtime)

    assert code == 0
    runtime.documents.confirm_and_explain.assert_called_once()
    output = capsys.readouterr().out
    assert output.index("Selected document") < output.index("Explanation")
    assert output.index("Selected providers") < output.index("Explanation")


def test_wrong_confirmation_token_fails_closed(capsys):
    runtime = _runtime()
    code = run_document_cli(_args(
        document_task="task-1",
        document_provider=["ollama"],
        confirm_document="read_and_send",
    ), runtime)

    assert code == 2
    runtime.documents.prepare.assert_not_called()
    runtime.documents.confirm_and_explain.assert_not_called()
    runtime.tasks.get_task.assert_not_called()
    assert "Invalid document confirmation token" in capsys.readouterr().err


def test_wrong_confirmation_token_does_not_prepare_a_new_task():
    runtime = _runtime()
    code = run_document_cli(_args(
        explain_document="/tmp/report.txt",
        document_provider=["ollama"],
        confirm_document="WRONG",
    ), runtime)

    assert code == 2
    runtime.documents.prepare.assert_not_called()


def test_missing_provider_fails_before_runtime_use():
    runtime = _runtime()
    code = run_document_cli(_args(
        explain_document="/tmp/report.txt", confirm_document="READ_AND_SEND"
    ), runtime)

    assert code == 2
    runtime.documents.prepare.assert_not_called()


def test_missing_task_id_fails_before_runtime_use():
    runtime = _runtime()
    assert run_document_cli(_args(document_task=""), runtime) == 2
    runtime.documents.reconnect.assert_not_called()


def test_status_does_not_require_confirmation():
    runtime = _runtime()
    assert run_document_cli(_args(document_task="task-1"), runtime) == 0
    runtime.documents.reconnect.assert_called_once()


def test_existing_task_shows_selected_path_before_confirmed_action(capsys):
    runtime = _runtime()
    code = run_document_cli(_args(
        document_task="task-1",
        document_provider=["ollama"],
        confirm_document="READ_AND_SEND",
    ), runtime)

    assert code == 0
    output = capsys.readouterr().out
    assert output.index("Selected document") < output.index("Explanation")


def test_follow_up_without_exact_confirmation_fails_closed():
    runtime = _runtime()
    code = run_document_cli(_args(
        document_task="task-1", document_follow_up="Why?", document_provider=["ollama"]
    ), runtime)
    assert code == 2
    runtime.documents.follow_up.assert_not_called()


def test_subprocess_prepare_does_not_read_or_contact_a_provider(tmp_path):
    document = tmp_path / "private.txt"
    document.write_text("must remain unread during prepare", encoding="utf-8")
    home = tmp_path / "home"
    env = os.environ.copy()
    env["HIKARI_HOME"] = str(home)

    result = subprocess.run(
        [sys.executable, str(ROOT / "hikari.py"), "--explain-document", str(document)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert "Confirmation required" in result.stdout
    assert "must remain unread" not in result.stdout + result.stderr


def test_invalid_confirmation_does_not_create_runtime_home(tmp_path):
    home = tmp_path / "home"
    env = os.environ.copy()
    env["HIKARI_HOME"] = str(home)

    result = subprocess.run(
        [
            sys.executable, str(ROOT / "hikari.py"),
            "--explain-document", str(tmp_path / "report.txt"),
            "--document-provider", "ollama",
            "--confirm-document", "WRONG",
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20,
    )

    assert result.returncode == 2
    assert not home.exists()


def test_confirmed_action_without_provider_does_not_create_runtime_home(tmp_path):
    home = tmp_path / "home"
    env = os.environ.copy()
    env["HIKARI_HOME"] = str(home)

    result = subprocess.run(
        [
            sys.executable, str(ROOT / "hikari.py"),
            "--document-task", "task-1", "--confirm-document", "READ_AND_SEND",
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20,
    )

    assert result.returncode == 2
    assert not home.exists()


def test_empty_task_id_does_not_create_runtime_home(tmp_path):
    home = tmp_path / "home"
    env = os.environ.copy()
    env["HIKARI_HOME"] = str(home)

    result = subprocess.run(
        [sys.executable, str(ROOT / "hikari.py"), "--document-task", ""],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20,
    )

    assert result.returncode == 2
    assert not home.exists()


def test_document_mode_rejects_other_runtime_and_action_modes(tmp_path):
    env = os.environ.copy()
    env["HIKARI_HOME"] = str(tmp_path / "home")

    for other in ("--server", "--doctor", "--brain-v2-status", "--tasks-list", "--text"):
        result = subprocess.run(
            [sys.executable, str(ROOT / "hikari.py"), "--document-task", "task-1", other],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 2, other

    assert not (tmp_path / "home").exists()
