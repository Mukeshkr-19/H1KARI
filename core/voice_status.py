"""Read-only voice backend, model cache, and audio-egress status."""

from __future__ import annotations

from importlib.util import find_spec
import os
from pathlib import Path
from typing import Mapping

from core.runtime_paths import hikari_home


FASTER_WHISPER_REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
SPEECHBRAIN_ECAPA_REVISION = "0f99f2d0ebe89ac095bcc5903c4dd8f72b367286"
OPENAI_WHISPER_BASE_SHA256 = (
    "ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e"
)


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

    from core.runtime_setup import get_voice_backend_name

    configured_backend = get_voice_backend_name(root=state_home)
    core_backend = configured_backend or "openai-whisper"
    daemon_backend = configured_backend or "faster-whisper"

    def backend_policy(backend: str) -> str:
        if backend == "google-speech":
            return "Google Speech Recognition; captured audio is sent off-device"
        label = "OpenAI Whisper base" if backend == "openai-whisper" else "faster-whisper base"
        return f"{label} locally; no silent cloud fallback"

    packages = {
        "speech_recognition": _installed("speech_recognition"),
        "openai_whisper": _installed("whisper"),
        "faster_whisper": _installed("faster_whisper"),
        "speechbrain": _installed("speechbrain"),
        "pyaudio": _installed("pyaudio"),
    }
    models = {
        "openai_whisper_base": {
            "sha256": OPENAI_WHISPER_BASE_SHA256,
            "package_available": packages["openai_whisper"],
            "cache_path": _display_path(whisper_cache, home),
            "cache_present": whisper_cache.is_file(),
        },
        "faster_whisper_base": {
            "revision": FASTER_WHISPER_REVISION,
            "package_available": packages["faster_whisper"],
            "cache_path": _display_path(faster_cache, home),
            "cache_present": faster_cache.is_dir(),
        },
        "speechbrain_ecapa": {
            "model_id": "speechbrain/spkrec-ecapa-voxceleb",
            "revision": SPEECHBRAIN_ECAPA_REVISION,
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
            "configured_backend": configured_backend,
            "core_voice": backend_policy(core_backend),
            "wake_daemon": backend_policy(daemon_backend),
            "simple_service": "Google Speech Recognition (explicit cloud selection only)",
            "google_audio_egress": True,
            "adapter_local_only": "Local backends fail with a bounded error instead of falling back to cloud STT",
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
        "Google Speech Recognition sends audio off-device only when explicitly selected.",
        "Local backends fail with a bounded error; they never silently fall back to cloud STT.",
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
        if "sha256" in model:
            lines.append(f"  reviewed sha256: {model['sha256']}")
        if "revision" in model:
            lines.append(f"  reviewed revision: {model['revision']}")
        lines.append(f"  cache path: {model['cache_path']}")

    speaker = status["models"]["speechbrain_ecapa"]
    lines.extend(
        [
            f"Speaker enrollment file present: {yes(speaker['enrollment_present'])} "
            "(contents not read)",
        ]
    )
    return "\n".join(lines)
