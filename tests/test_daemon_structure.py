"""The always-on daemon must not start loops merely by being imported."""

from __future__ import annotations

import ast
from pathlib import Path


DAEMON_PATH = Path(__file__).parents[1] / "services" / "hikari_daemon.py"


def _tree():
    return ast.parse(DAEMON_PATH.read_text(encoding="utf-8"))


def test_daemon_has_no_top_level_infinite_loop_and_one_entrypoint():
    tree = _tree()

    assert not any(isinstance(node, ast.While) for node in tree.body)
    main_guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and any(
            isinstance(part, ast.Constant) and part.value == "__main__"
            for part in ast.walk(node.test)
        )
    ]
    assert len(main_guards) == 1


def test_daemon_loop_is_owned_by_listen_function():
    tree = _tree()
    listen = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "listen_always"
    )

    assert any(isinstance(node, ast.While) for node in ast.walk(listen))


def test_wake_and_active_audio_are_speaker_checked():
    tree = _tree()
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    for name in ("_listen_for_wake_word", "_listen_for_active_command"):
        calls = [
            node
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "verify_speaker"
        ]
        assert len(calls) == 1


def test_enrolled_speaker_verification_errors_fail_closed():
    tree = _tree()
    verify = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "verify_speaker"
    )
    guarded = next(node for node in verify.body if isinstance(node, ast.Try))

    for handler in guarded.handlers:
        returns = [node for node in ast.walk(handler) if isinstance(node, ast.Return)]
        assert returns
        assert all(
            isinstance(node.value, ast.Constant) and node.value.value is False
            for node in returns
        )
