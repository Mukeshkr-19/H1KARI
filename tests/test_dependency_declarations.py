"""Regression checks for dependency names with unsafe import collisions."""

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]


def _requirement_names() -> list[str]:
    names = []
    for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower())
    return names


def test_voice_backends_have_unambiguous_distribution_names():
    names = _requirement_names()

    assert "whisper" not in names
    assert names.count("openai-whisper") == 1
    assert names.count("faster-whisper") == 1
