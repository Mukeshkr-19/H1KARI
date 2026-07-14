"""Explicit, no-download initialization for private HIKARI runtime state."""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.runtime_paths import hikari_home


CONFIG_NAME = "runtime.json"
VOICE_BACKENDS = {
    "openai-whisper": {
        "model": "base",
        "download": "May download the base model on first voice use.",
        "audio_egress": True,
        "egress": "Current core voice fallback may send captured audio to Google.",
    },
    "faster-whisper": {
        "model": "base",
        "download": "May download the base model on first daemon voice use.",
        "audio_egress": True,
        "egress": "Current daemon fallback may send captured audio to Google.",
    },
    "google-speech": {
        "model": None,
        "download": "No local speech model is selected.",
        "audio_egress": True,
        "egress": "Captured audio is sent off-device for recognition.",
    },
}


def _selection(startup_mode: str, voice_backend: str | None) -> dict:
    if startup_mode not in {"text", "voice"}:
        raise ValueError("startup mode must be text or voice")
    if startup_mode == "text":
        if voice_backend is not None:
            raise ValueError("voice backend requires startup mode voice")
        return {
            "startup_mode": "text",
            "voice": {
                "backend": None,
                "model": None,
                "download": "No voice model is downloaded during initialization.",
                "audio_egress": False,
                "egress": "Text startup captures no audio.",
            },
        }
    if voice_backend not in VOICE_BACKENDS:
        raise ValueError("voice startup requires an explicit supported backend")
    return {"startup_mode": "voice", "voice": {"backend": voice_backend, **VOICE_BACKENDS[voice_backend]}}


def _layout(root: Path) -> tuple[Path, ...]:
    brain = root / "brain"
    return (
        root,
        brain,
        brain / "brain_v2",
        brain / "tasks",
        brain / "legacy-data",
        root / "backups",
    )


def initialization_plan(
    startup_mode: str,
    voice_backend: str | None = None,
    *,
    root: Path | None = None,
) -> dict:
    root = hikari_home() if root is None else Path(root).expanduser().resolve()
    selection = _selection(startup_mode, voice_backend)
    create = []
    existing = []
    blockers = []

    if root.is_symlink():
        blockers.append("runtime home is a symlink; use the migration planner")
    if (root / "hikari.py").is_file() and (root / "core").is_dir():
        blockers.append("runtime home is a HIKARI code checkout")
    if not root.exists() and not root.parent.is_dir():
        blockers.append(f"parent directory does not exist: {root.parent}")

    for path in _layout(root):
        if path.is_symlink():
            blockers.append(f"path is a symlink: {path}")
        elif path.exists() and not path.is_dir():
            blockers.append(f"path is not a directory: {path}")
        elif path.exists():
            existing.append(path)
        else:
            create.append(path)

    return {
        "root": root,
        "config": root / CONFIG_NAME,
        "selection": selection,
        "create": create,
        "existing": existing,
        "blockers": blockers,
    }


def initialize_runtime_home(
    startup_mode: str,
    voice_backend: str | None = None,
    *,
    root: Path | None = None,
) -> dict:
    plan = initialization_plan(startup_mode, voice_backend, root=root)
    if plan["blockers"]:
        raise RuntimeError("; ".join(plan["blockers"]))

    config_path = plan["config"]
    selection = plan["selection"]
    if config_path.is_symlink() or (config_path.exists() and not config_path.is_file()):
        raise RuntimeError("runtime config must be a regular file")
    if config_path.exists():
        current = json.loads(config_path.read_text(encoding="utf-8"))
        if current.get("version") != 1 or not isinstance(current.get("created_paths"), list):
            raise RuntimeError("runtime config has an unsupported or invalid schema")
        if current.get("startup_mode") != selection["startup_mode"] or current.get("voice") != selection["voice"]:
            raise RuntimeError("runtime home is already initialized with different startup settings")
        missing = [path for path in _layout(plan["root"]) if not path.is_dir()]
        if missing:
            raise RuntimeError(f"initialized runtime layout is incomplete: {missing[0]}")
        return {**plan, "created": [], "already_initialized": True}

    created = []
    config_created = False
    try:
        for path in plan["create"]:
            path.mkdir(mode=0o700)
            created.append(path)
        relative_created = ["." if path == plan["root"] else str(path.relative_to(plan["root"])) for path in created]
        payload = {"version": 1, **selection, "created_paths": relative_created}
        fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        config_created = True
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
    except Exception:
        if config_created and config_path.exists():
            config_path.unlink()
        for path in reversed(created):
            path.rmdir()
        raise

    return {**plan, "created": created, "already_initialized": False}


def format_initialization(result: dict, *, applied: bool) -> str:
    selection = result["selection"]
    voice = selection["voice"]
    lines = [
        "HIKARI runtime initialization" if applied else "HIKARI runtime initialization plan",
        "=" * 36,
        f"Runtime home: {result['root']}",
        f"Startup mode: {selection['startup_mode']}",
        f"Voice backend: {voice['backend'] or 'none'}",
        f"Model download: {voice['download']}",
        f"Audio egress: {'possible' if voice['audio_egress'] else 'none'} — {voice['egress']}",
        "No model is imported or downloaded by this command.",
    ]
    if result["blockers"]:
        lines.append("Blockers: " + "; ".join(result["blockers"]))
    elif applied and result.get("already_initialized"):
        lines.append("Result: already initialized with matching settings; no changes made.")
    else:
        paths = result.get("created", result["create"])
        label = "Created" if applied else "Would create"
        lines.append(f"{label}: " + (", ".join(str(path) for path in paths) or "nothing"))
    return "\n".join(lines)
