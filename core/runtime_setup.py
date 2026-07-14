"""Explicit, no-download initialization for private HIKARI runtime state."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from core.path_literals import HIKARI_PRIVATE
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


def _load_runtime_config(root: Path) -> dict:
    config_path = root / CONFIG_NAME
    if config_path.is_symlink() or not config_path.is_file():
        raise RuntimeError("runtime config must be an existing regular file")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("version") != 1 or not isinstance(config.get("created_paths"), list):
        raise RuntimeError("runtime config has an unsupported or invalid schema")
    return config


def backup_runtime_home(
    *,
    root: Path | None = None,
    destination: Path | None = None,
) -> Path:
    root = hikari_home() if root is None else Path(root).expanduser().resolve()
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("runtime home must be an existing regular directory")
    _load_runtime_config(root)

    if destination is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = root / "backups" / f"runtime-{stamp}"
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise RuntimeError(f"backup destination already exists: {destination}")

    backups_dir = (root / "backups").resolve()
    if destination.is_relative_to(root) and not destination.is_relative_to(backups_dir):
        raise RuntimeError("backup destination inside runtime home must be under backups")
    if not destination.parent.is_dir():
        raise RuntimeError(f"backup parent does not exist: {destination.parent}")

    def ignore_backups(path: str, names: list[str]) -> list[str]:
        if Path(path).resolve() == root.resolve() and "backups" in names:
            return ["backups"]
        return []

    shutil.copytree(root, destination, symlinks=True, ignore=ignore_backups)
    return destination


def runtime_migration_plan(*, root: Path | None = None, repo_root: Path | None = None) -> dict:
    """Inspect legacy layout shapes without reading private file contents or writing."""
    root = hikari_home() if root is None else Path(root).expanduser().resolve()
    if repo_root is None:
        repo_root = Path(os.environ.get("HIKARI_REPO_ROOT", Path(__file__).resolve().parents[1]))
    repo_root = Path(repo_root).expanduser().resolve()
    legacy_brain = repo_root.parent / HIKARI_PRIVATE / "live-brain"
    brain = root / "brain"

    if brain.is_symlink():
        state = "legacy brain symlink detected"
        source = brain.resolve()
        actions = [
            "Back up the symlink target with its existing private backup procedure.",
            "Review target ownership and available disk space.",
            "Replace the symlink only in a separately approved migration apply step.",
        ]
    elif brain.is_dir():
        state = "runtime brain directory already exists"
        source = brain
        actions = ["Back up HIKARI_HOME before adopting the existing directory in place."]
    elif legacy_brain.is_dir():
        state = "legacy sibling brain available"
        source = legacy_brain
        actions = [
            "Back up the legacy private brain.",
            "Review ownership and available disk space.",
            "Copy only in a separately approved migration apply step.",
        ]
    else:
        state = "no legacy brain source detected"
        source = None
        actions = ["Use --init for a fresh private runtime layout."]

    return {"root": root, "state": state, "source": source, "actions": actions}


def rollback_initialization(token: str, *, root: Path | None = None) -> list[Path]:
    if token != "ROLLBACK":
        raise ValueError("rollback token must be exactly ROLLBACK")
    root = hikari_home() if root is None else Path(root).expanduser().resolve()
    config = _load_runtime_config(root)
    config_path = root / CONFIG_NAME

    allowed = {"." if path == root else str(path.relative_to(root)): path for path in _layout(root)}
    relative_created = config["created_paths"]
    if (
        not all(isinstance(path, str) for path in relative_created)
        or len(relative_created) != len(set(relative_created))
        or any(path not in allowed for path in relative_created)
    ):
        raise RuntimeError("runtime config contains unsafe rollback paths")
    created = [allowed[path] for path in relative_created]
    expected = set(created) | {config_path}

    for path in created:
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError(f"created rollback path is no longer a regular directory: {path}")
        unexpected = [child for child in path.iterdir() if child not in expected]
        if unexpected:
            raise RuntimeError(f"rollback refused because created path contains data: {path}")

    config_path.unlink()
    removed = [config_path]
    for path in sorted(created, key=lambda item: len(item.parts), reverse=True):
        path.rmdir()
        removed.append(path)
    return removed


def format_migration_plan(plan: dict) -> str:
    lines = [
        "HIKARI runtime migration plan (read-only)",
        "=" * 41,
        f"Runtime home: {plan['root']}",
        f"State: {plan['state']}",
        f"Source: {plan['source'] or 'none'}",
        "No files were read, copied, moved, or removed.",
        "Actions:",
    ]
    lines.extend(f"- {action}" for action in plan["actions"])
    return "\n".join(lines)
