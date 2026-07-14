"""Voice status must be informative without loading models or reading biometrics."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from core import voice_status


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_status_reports_packages_caches_and_enrollment_without_reading(
    tmp_path: Path,
    monkeypatch,
):
    installed = {"whisper", "faster_whisper", "speech_recognition"}
    monkeypatch.setattr(
        voice_status,
        "find_spec",
        lambda module: object() if module in installed else None,
    )
    (tmp_path / ".cache" / "whisper").mkdir(parents=True)
    (tmp_path / ".cache" / "whisper" / "base.pt").touch()
    faster_cache = (
        tmp_path
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--Systran--faster-whisper-base"
    )
    faster_cache.mkdir(parents=True)
    legacy = tmp_path / "private-legacy"
    legacy.mkdir()
    (legacy / "voice_auth.json").write_text("not parsed", encoding="utf-8")

    status = voice_status.collect_voice_status(
        environ={"HIKARI_LEGACY_DATA_DIR": str(legacy)},
        home=tmp_path,
    )

    assert status["models"]["openai_whisper_base"]["offline_ready"] is True
    assert status["models"]["faster_whisper_base"]["offline_ready"] is True
    assert status["models"]["speechbrain_ecapa"]["offline_ready"] is False
    assert status["models"]["speechbrain_ecapa"]["enrollment_present"] is True
    assert status["policies"]["google_audio_egress"] is True


def test_formatter_discloses_egress_and_no_load_contract(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(voice_status, "find_spec", lambda _module: None)
    status = voice_status.collect_voice_status(environ={}, home=tmp_path)

    report = voice_status.format_voice_status(status)

    assert "read-only; no models loaded" in report
    assert "contents not read" in report
    assert "send captured audio off-device" in report


def test_voice_status_cli_does_not_import_model_packages(tmp_path: Path):
    marker = tmp_path / "model-imported"
    module_source = (
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['HIKARI_TEST_MODEL_MARKER']).write_text('imported')\n"
    )
    (tmp_path / "whisper.py").write_text(module_source, encoding="utf-8")
    (tmp_path / "faster_whisper.py").write_text(module_source, encoding="utf-8")
    (tmp_path / "speechbrain.py").write_text(module_source, encoding="utf-8")
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["HIKARI_TEST_MODEL_MARKER"] = str(marker)
    env["PYTHONPATH"] = os.pathsep.join((str(tmp_path), str(REPO_ROOT)))
    env.pop("HIKARI_BRAIN_DIR", None)
    env.pop("HIKARI_LEGACY_DATA_DIR", None)

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "hikari.py"), "--voice-status"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "Voice backend status" in result.stdout
    assert "Google fallback" in result.stdout
    assert not marker.exists()
