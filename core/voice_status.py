"""Read-only voice backend, model cache, and audio-egress status."""

from __future__ import annotations

from importlib.util import find_spec
import os
from pathlib import Path
from typing import Mapping

from core.runtime_paths import hikari_home


def _installed(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _display_path(path: Path, home: Path) -> str:
    try:
        return str(Path("~") / path.relative_to(home))
    except ValueError:
        return str(path)


def collect_voice_status(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> dict:
    """Inspect package and cache metadata without importing models or reading profiles."""
    env = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)

    whisper_cache = home / ".cache" / "whisper" / "base.pt"
    hf_home = Path(env.get("HF_HOME", home / ".cache" / "huggingface"))
    hf_cache = Path(
        env.get(
            "HF_HUB_CACHE",
            env.get("HUGGINGFACE_HUB_CACHE", hf_home / "hub"),
        )
    )
    faster_cache = hf_cache / "models--Systran--faster-whisper-base"

    state_home = hikari_home(environ=env, home=home)
    brain_dir = Path(env.get("HIKARI_BRAIN_DIR", state_home / "brain"))
    legacy_dir = Path(env.get("HIKARI_LEGACY_DATA_DIR", brain_dir / "legacy-data"))
    speaker_cache = legacy_dir / "hf_cache" / "speechbrain_spkrec_ecapa"
    enrollment_file = legacy_dir / "voice_auth.json"

    packages = {
        "speech_recognition": _installed("speech_recognition"),
        "openai_whisper": _installed("whisper"),
        "faster_whisper": _installed("faster_whisper"),
        "speechbrain": _installed("speechbrain"),
        "pyaudio": _installed("pyaudio"),
    }
    models = {
        "openai_whisper_base": {
            "package_available": packages["openai_whisper"],
            "cache_path": _display_path(whisper_cache, home),
            "cache_present": whisper_cache.is_file(),
        },
        "faster_whisper_base": {
            "package_available": packages["faster_whisper"],
            "cache_path": _display_path(faster_cache, home),
            "cache_present": faster_cache.is_dir(),
        },
        "speechbrain_ecapa": {
            "model_id": "speechbrain/spkrec-ecapa-voxceleb",
            "package_available": packages["speechbrain"],
            "cache_path": _display_path(speaker_cache, home),
            "cache_present": speaker_cache.is_dir(),
            "enrollment_present": enrollment_file.is_file(),
        },
    }
    for model in models.values():
        model["offline_ready"] = bool(
            model["package_available"] and model["cache_present"]
        )

    return {
        "packages": packages,
        "models": models,
        "policies": {
            "core_voice": "OpenAI Whisper base locally, then Google Speech fallback",
            "wake_daemon": "faster-whisper base locally, then Google Speech fallback",
            "simple_service": "Google Speech Recognition",
            "google_audio_egress": True,
        },
    }


def format_voice_status(status: dict | None = None) -> str:
    """Format a concise local report with no model load or biometric read."""
    status = collect_voice_status() if status is None else status

    def yes(value: bool) -> str:
        return "yes" if value else "no"

    lines = [
        "Voice backend status (read-only; no models loaded)",
        "==================================================",
        f"core.voice: {status['policies']['core_voice']}",
        f"wake daemon: {status['policies']['wake_daemon']}",
        f"simple service: {status['policies']['simple_service']}",
        "",
    ]
    labels = {
        "openai_whisper_base": "OpenAI Whisper base",
        "faster_whisper_base": "faster-whisper base",
        "speechbrain_ecapa": "SpeechBrain ECAPA",
    }
    for key, label in labels.items():
        model = status["models"][key]
        lines.append(
            f"{label}: package={yes(model['package_available'])}; "
            f"cache={yes(model['cache_present'])}; "
            f"offline-ready={yes(model['offline_ready'])}"
        )
        lines.append(f"  cache path: {model['cache_path']}")

    speaker = status["models"]["speechbrain_ecapa"]
    lines.extend(
        [
            f"Speaker enrollment file present: {yes(speaker['enrollment_present'])} "
            "(contents not read)",
            "Google fallback may send captured audio off-device when local recognition "
            "is unavailable or fails.",
        ]
    )
    return "\n".join(lines)
