"""Production composition tests for the Phase 4 pairing runtime."""

from __future__ import annotations

import ast
import os
import pathlib
import re
import stat
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

from core.pairing.bootstrap import (
    PAIRING_DB_NAME,
    PairingBootstrapError,
    _production_challenge_id,
    _production_device_id,
    _production_digest_key,
    _production_secret_code,
    create_pairing_runtime,
    pairing_db_path,
)
from core.pairing.runtime import PairingRuntime
from core.protocol import validate_server_message


REPO_ROOT = Path(__file__).resolve().parent.parent
CHALLENGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
CODE_PATTERN = re.compile(r"^[0-9A-F]{10}$")


class _Clock:
    def __init__(self, start: float = 2000.0) -> None:
        self._value = start

    def __call__(self) -> float:
        return self._value


class _Sink:
    def __init__(self) -> None:
        self.codes: list[str] = []

    def __call__(self, code: str) -> None:
        self.codes.append(code)


def test_create_pairing_runtime_uses_injected_dependencies(tmp_path: Path) -> None:
    clock = _Clock()
    sink = _Sink()
    challenge_ids = deque(["challenge-a"])
    device_ids = deque(["device-a"])
    codes = deque(["AABBCC"])
    digest = b"injected-digest-key-32-bytes-long!!"

    runtime = create_pairing_runtime(
        db_path=tmp_path / "pairing" / "devices.db",
        clock=clock,
        challenge_id_factory=lambda: challenge_ids.popleft(),
        device_id_factory=lambda: device_ids.popleft(),
        secret_code_factory=lambda: codes.popleft(),
        digest_key=digest,
        display_sink=sink,
    )

    assert isinstance(runtime, PairingRuntime)
    assert runtime._clock is clock
    assert runtime._display_sink is sink

    message = runtime.prepare("request-1")
    assert validate_server_message(message) is None
    assert message["challenge_id"] == "challenge-a"
    assert sink.codes == ["AABBCC"]
    confirmed = runtime.confirm("request-1", "challenge-a", "AABBCC")
    assert confirmed["device_id"] == "device-a"


def test_production_ids_and_codes_are_random_and_canonical() -> None:
    challenge_ids = {_production_challenge_id() for _ in range(32)}
    device_ids = {_production_device_id() for _ in range(32)}
    codes = {_production_secret_code() for _ in range(32)}
    keys = {_production_digest_key() for _ in range(8)}

    assert len(challenge_ids) == 32
    assert len(device_ids) == 32
    assert len(codes) == 32
    assert len(keys) == 8
    assert all(CHALLENGE_ID_PATTERN.fullmatch(value) for value in challenge_ids)
    assert all(DEVICE_ID_PATTERN.fullmatch(value) for value in device_ids)
    assert all(1 <= len(value) <= 128 for value in device_ids)
    assert all(CODE_PATTERN.fullmatch(value) for value in codes)
    assert all(len(key) == 32 for key in keys)


def test_invalid_factory_ids_fail_closed(tmp_path: Path) -> None:
    runtime = create_pairing_runtime(
        db_path=tmp_path / "devices.db",
        clock=_Clock(),
        challenge_id_factory=lambda: "BAD ID",
        device_id_factory=lambda: "device-1",
        secret_code_factory=lambda: "ABC123",
        digest_key=b"k" * 32,
        display_sink=_Sink(),
    )
    message = runtime.prepare("request-1")
    assert validate_server_message(message) is None
    assert message == {
        "type": "pairing_error",
        "request_id": "request-1",
        "code": "unavailable",
    }


def test_default_database_is_private_and_not_under_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_home = tmp_path / "private-home"
    working_directory = tmp_path / "working-directory"
    working_directory.mkdir()
    monkeypatch.setenv("HIKARI_HOME", str(state_home))
    monkeypatch.chdir(working_directory)

    runtime = create_pairing_runtime(
        clock=_Clock(),
        display_sink=_Sink(),
        digest_key=b"d" * 32,
        challenge_id_factory=lambda: "challenge-1",
        device_id_factory=lambda: "device-1",
        secret_code_factory=lambda: "ABC123",
    )
    db_path = pairing_db_path()

    assert isinstance(runtime, PairingRuntime)
    assert db_path == state_home / "pairing" / PAIRING_DB_NAME
    assert db_path.is_file()
    assert REPO_ROOT not in db_path.parents
    assert working_directory not in db_path.parents
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_imports_do_not_create_pairing_state(tmp_path: Path) -> None:
    state_home = tmp_path / "private-home"
    env = {**os.environ, "HIKARI_HOME": str(state_home)}

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import hikari; import core.pairing.bootstrap",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not (state_home / "pairing").exists()


def test_bootstrap_failure_is_safe(tmp_path: Path, monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise OSError(f"PRIVATE_PATH_{tmp_path}/secret.db")

    monkeypatch.setattr(
        "core.pairing.bootstrap.DeviceSessionStore",
        boom,
    )
    with pytest.raises(PairingBootstrapError) as exc_info:
        create_pairing_runtime(
            db_path=tmp_path / "devices.db",
            clock=_Clock(),
            display_sink=_Sink(),
            digest_key=b"d" * 32,
        )
    message = str(exc_info.value)
    assert message == "pairing bootstrap failed"
    assert "PRIVATE_PATH_" not in message
    assert str(tmp_path) not in message
    assert "secret.db" not in message
    assert repr(exc_info.value) == "PairingBootstrapError()"


def test_lazy_database_creation_only_on_factory(tmp_path: Path, monkeypatch) -> None:
    state_home = tmp_path / "private-home"
    monkeypatch.setenv("HIKARI_HOME", str(state_home))
    assert not pairing_db_path().exists()
    create_pairing_runtime(
        clock=_Clock(),
        display_sink=_Sink(),
        digest_key=b"d" * 32,
        challenge_id_factory=lambda: "challenge-1",
        device_id_factory=lambda: "device-1",
        secret_code_factory=lambda: "ABC123",
    )
    assert pairing_db_path().is_file()


def test_bootstrap_has_no_forbidden_side_effect_imports() -> None:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "pairing"
        / "bootstrap.py"
    )
    forbidden = {
        "subprocess",
        "socket",
        "asyncio",
        "requests",
        "http",
        "urllib",
        "threading",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported)
