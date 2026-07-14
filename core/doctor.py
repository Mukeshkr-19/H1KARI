"""Doctor/status checks for the local HIKARI workspace."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from core.path_literals import DOT_HIKARI, ENV_FILE, HIKARI_MEMORY_DB, HIKARI_PRIVATE


REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = REPO_ROOT.parent
PRIVATE_ROOT = PROJECT_ROOT / HIKARI_PRIVATE
EXPECTED_BRAIN_TARGET = PRIVATE_ROOT / "live-brain"
PUBLIC_PRIVATE_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        rf"^{re.escape(ENV_FILE)}$",
        r"\.db$",
        r"\.sqlite$",
        r"\.sqlite3$",
        r"^data/",
        r"voiceprint",
        r"voice_auth",
        r"^logs/",
        re.escape(DOT_HIKARI),
        re.escape(HIKARI_PRIVATE),
        r"HIKARI_ROADMAP",
        r"WORK_DONE",
        r"\.claw-workflow",
        r"^\.idea/",
    ]
]


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _run_command(
    command: Sequence[str],
    cwd: Path = REPO_ROOT,
    timeout: int = 30,
    input_text: str | None = None,
) -> CommandResult:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(
            result.returncode,
            _output_text(result.stdout),
            _output_text(result.stderr),
        )
    except FileNotFoundError as exc:
        return CommandResult(127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            124,
            _output_text(exc.stdout),
            _output_text(exc.stderr),
            timed_out=True,
        )


def _ok(name: str, detail: str) -> Check:
    return Check(name, "ok", detail)


def _warn(name: str, detail: str) -> Check:
    return Check(name, "warn", detail)


def _fail(name: str, detail: str) -> Check:
    return Check(name, "fail", detail)


def _command_check(
    name: str,
    command: Sequence[str],
    cwd: Path = REPO_ROOT,
    timeout: int = 60,
    input_text: str | None = None,
) -> Check:
    result = _run_command(command, cwd=cwd, timeout=timeout, input_text=input_text)
    printable = " ".join(shlex.quote(part) for part in command)

    if result.timed_out:
        return _fail(name, f"Timed out after {timeout}s: {printable}")

    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip().splitlines()
        tail = output[-1] if output else "no output"
        return _fail(name, f"{printable} exited {result.returncode}: {tail}")

    return _ok(name, f"Passed: {printable}")


def _tracked_private_matches(files: Iterable[str]) -> list[str]:
    matches = []
    for path in files:
        if any(pattern.search(path) for pattern in PUBLIC_PRIVATE_PATTERNS):
            matches.append(path)
    return sorted(matches)


def _git_ls_files() -> list[str]:
    result = _run_command(["git", "ls-files"])
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _check_python_version() -> Check:
    version = sys.version_info
    label = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) == (3, 12):
        return _ok("Python version", f"Running Python {label}")
    return _warn("Python version", f"Running Python {label}; HIKARI baseline is Python 3.12")


def _check_git_status() -> Check:
    result = _run_command(["git", "status", "--short", "--branch"])
    if result.returncode != 0:
        return _fail("Git status", "Not a Git repo or git is unavailable")

    lines = result.stdout.splitlines()
    branch = lines[0] if lines else "unknown branch"
    dirty = [line for line in lines[1:] if line.strip()]
    if dirty:
        return _warn("Git status", f"{branch}; {len(dirty)} local change(s)")
    return _ok("Git status", f"{branch}; clean")


def _check_required_paths() -> list[Check]:
    required = [
        "hikari.py",
        "core/orchestrator.py",
        "core/server.py",
        "core/neural_memory_bridge.py",
        "core/neural_memory/db/memory_schema.sql",
        "agents",
        "services",
        "skills",
        "tests",
        "hikari-frontend/package.json",
        "bin/Hikari",
        "scripts/install-hikari-cli.sh",
        "scripts/uninstall-hikari-cli.sh",
        ".env.example",
        ".gitignore",
        "README.md",
        "AGENTS.md",
    ]

    missing = [path for path in required if not (REPO_ROOT / path).exists()]
    if missing:
        return [_fail("Required repo paths", "Missing: " + ", ".join(missing))]
    return [_ok("Required repo paths", f"{len(required)} expected paths found")]


def _check_private_layout() -> list[Check]:
    checks: list[Check] = []
    private_ready = PRIVATE_ROOT.exists()

    if private_ready:
        checks.append(_ok("Private data folder", str(PRIVATE_ROOT)))
    else:
        checks.append(
            _warn(
                "Private data folder",
                (
                    f"Not found at {PRIVATE_ROOT} "
                    f"(optional for a source-only H1KARI clone; add sibling "
                    f"{HIKARI_PRIVATE}/ when you need a live neural brain)"
                ),
            )
        )

    brain_link = Path.home() / ".hikari" / "brain"
    if brain_link.is_symlink():
        target = brain_link.resolve()
        if private_ready and target == EXPECTED_BRAIN_TARGET:
            checks.append(_ok("Brain symlink", f"{brain_link} -> {target}"))
        elif private_ready:
            checks.append(
                _warn(
                    "Brain symlink",
                    f"{brain_link} -> {target} (expected {EXPECTED_BRAIN_TARGET})",
                )
            )
        else:
            checks.append(_ok("Brain symlink", f"{brain_link} -> {target}"))
    elif brain_link.exists():
        checks.append(_warn("Brain symlink", f"{brain_link} exists but is not a symlink"))
    else:
        checks.append(
            _warn(
                "Brain symlink",
                f"Not configured ({brain_link}); optional until you link a live brain",
            )
        )

    if not private_ready:
        return checks

    backup_script = PRIVATE_ROOT / "scripts" / "backup-hikari-brain.sh"
    if backup_script.exists() and os.access(backup_script, os.X_OK):
        checks.append(_ok("Brain backup script", str(backup_script)))
    elif backup_script.exists():
        checks.append(_warn("Brain backup script", f"Exists but is not executable: {backup_script}"))
    else:
        checks.append(
            _warn(
                "Brain backup script",
                f"Not found at {backup_script} (add when private data folder is set up)",
            )
        )

    return checks


def _check_public_privacy() -> Check:
    matches = _tracked_private_matches(_git_ls_files())
    if matches:
        return _fail("Public Git privacy scan", "Tracked private-looking files: " + ", ".join(matches))
    return _ok("Public Git privacy scan", "No tracked private/runtime files matched")


def _check_duplicate_tracked_files() -> Check:
    result = _run_command(["git", "ls-files"])
    if result.returncode != 0:
        return _fail("Tracked duplicate scan", "Could not list tracked files")

    files = [REPO_ROOT / line for line in result.stdout.splitlines() if line.strip()]
    hashes: dict[str, list[str]] = {}
    for path in files:
        if not path.is_file():
            continue
        import hashlib

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes.setdefault(digest, []).append(str(path.relative_to(REPO_ROOT)))

    duplicates = [paths for paths in hashes.values() if len(paths) > 1]
    if duplicates:
        rendered = "; ".join(", ".join(paths) for paths in duplicates)
        return _warn("Tracked duplicate scan", f"Exact duplicate tracked content: {rendered}")
    return _ok("Tracked duplicate scan", "No exact duplicate tracked files")


def _check_brain_v2_episode_store() -> Check:
    """Read-only probe of Brain v2 episode DB (no conversation text)."""
    from core.brain_v2.status import collect_brain_v2_status, is_brain_v2_enabled

    if not is_brain_v2_enabled():
        return _warn("Brain v2", "disabled (HIKARI_DISABLE_BRAIN_V2=1)")
    stats = collect_brain_v2_status()
    if stats.get("error"):
        return _fail("Brain v2", stats["error"])
    if not stats.get("db_exists"):
        return _warn(
            "Brain v2",
            (
                f"enabled; episode DB not created yet at {stats.get('db_path')} "
                "(normal until first chat session or brain-v2 CLI)"
            ),
        )
    return _ok(
        "Brain v2",
        (
            f"{stats.get('raw_episodes', 0)} episodes, "
            f"{stats.get('transcript_segments', 0)} segments, "
            f"{stats.get('pending_candidates', 0)} pending, "
            f"{stats.get('rejected_candidates', 0)} rejected, "
            f"{stats.get('accepted_memories', 0)} accepted"
        ),
    )


def _check_neural_memory_readonly() -> Check:
    """Read-only probe of the live brain DB (no writes)."""
    db_path = EXPECTED_BRAIN_TARGET / HIKARI_MEMORY_DB
    if not db_path.is_file():
        if not PRIVATE_ROOT.exists():
            return _ok(
                "Neural memory DB",
                "Not configured (optional until private live-brain is set up; Brain v2 still works)",
            )
        return _warn(
            "Neural memory DB",
            f"Not initialized yet at {db_path} (normal before first private brain setup)",
        )

    try:
        import sqlite3

        uri = f"file:{db_path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                return _fail(
                    "Neural memory DB",
                    f"integrity_check: {integrity[0] if integrity else 'unknown'}",
                )
            nodes = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE is_archived = 0"
            ).fetchone()[0]
            edges = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE is_archived = 0"
            ).fetchone()[0]
            persons = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE node_type = 'PERSON' AND is_archived = 0"
            ).fetchone()[0]
            facts = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE node_type = 'FACT' AND is_archived = 0"
            ).fetchone()[0]
        return _ok(
            "Neural memory DB",
            f"{db_path.name}: ok; {nodes} nodes ({persons} persons, {facts} facts), {edges} edges",
        )
    except sqlite3.Error as exc:
        return _fail("Neural memory DB", f"Read-only open failed: {exc}")


def _check_frontend_layout() -> list[Check]:
    frontend = REPO_ROOT / "hikari-frontend"
    checks: list[Check] = []

    if (frontend / "package.json").exists():
        checks.append(_ok("Frontend package", str(frontend / "package.json")))
    else:
        checks.append(_warn("Frontend package", "hikari-frontend/package.json is missing"))

    if (frontend / "node_modules").exists():
        checks.append(_ok("Frontend dependencies", "node_modules present"))
    else:
        checks.append(_warn("Frontend dependencies", "node_modules missing; run npm install in hikari-frontend"))

    return checks


def _check_repo_root() -> Check:
    name = REPO_ROOT.name
    return _ok("Repo root", f"{REPO_ROOT} ({name})")


def _collect_full_command_checks() -> list[Check]:
    """Run the explicit full-doctor command plan."""
    return [
        _command_check(
            "CLI help",
            [sys.executable, "hikari.py", "--help"],
            timeout=20,
        ),
        _command_check(
            "Text status",
            [sys.executable, "hikari.py", "--text"],
            timeout=40,
            input_text="status\nexit\n",
        ),
        _command_check(
            "Python tests",
            [sys.executable, "-m", "pytest", "tests", "-q"],
            timeout=120,
        ),
        _command_check(
            "Frontend lint",
            ["npm", "run", "lint"],
            cwd=REPO_ROOT / "hikari-frontend",
            timeout=120,
        ),
        _command_check(
            "Frontend build",
            ["npm", "run", "build"],
            cwd=REPO_ROOT / "hikari-frontend",
            timeout=180,
        ),
    ]


def collect_checks(full: bool = False) -> list[Check]:
    checks: list[Check] = []
    checks.append(_check_python_version())
    checks.append(_check_git_status())
    checks.append(_check_repo_root())
    checks.extend(_check_required_paths())
    checks.extend(_check_private_layout())
    checks.append(_check_neural_memory_readonly())
    checks.append(_check_brain_v2_episode_store())
    checks.append(_check_public_privacy())
    checks.append(_check_duplicate_tracked_files())
    checks.extend(_check_frontend_layout())

    if full:
        checks.extend(_collect_full_command_checks())

    return checks


def format_checks(checks: Sequence[Check]) -> str:
    lines = ["HIKARI Doctor", "=" * 13]
    for check in checks:
        label = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}[check.status]
        lines.append(f"[{label}] {check.name}: {check.detail}")

    failures = sum(1 for check in checks if check.status == "fail")
    warnings = sum(1 for check in checks if check.status == "warn")
    if failures:
        summary = f"FAILED: {failures} failure(s), {warnings} warning(s)"
    elif warnings:
        summary = f"OK WITH WARNINGS: {warnings} warning(s)"
    else:
        summary = "OK: all checks passed"

    lines.extend(["", summary])
    return "\n".join(lines)


def run_doctor(full: bool = False) -> int:
    checks = collect_checks(full=full)
    print(format_checks(checks))
    return 1 if any(check.status == "fail" for check in checks) else 0
