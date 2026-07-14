"""Regression checks for dependency names with unsafe import collisions."""

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LOCK = REPO_ROOT / "requirements-macos-arm64-py312.lock"
DEV_LOCK = REPO_ROOT / "requirements-dev-macos-arm64-py312.lock"


def _requirement_names() -> list[str]:
    names = []
    for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower()
            names.append(re.sub(r"[-_.]+", "-", name))
    return names


def _locked_packages(path: Path) -> set[str]:
    packages = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        assert not line.startswith("#")
        assert "==" in line
        packages.add(line.split("==", maxsplit=1)[0])
    return packages


def test_voice_backends_have_unambiguous_distribution_names():
    names = _requirement_names()

    assert "whisper" not in names
    assert names.count("openai-whisper") == 1
    assert names.count("faster-whisper") == 1


def test_known_unused_packages_are_not_declared():
    names = set(_requirement_names())

    assert names.isdisjoint(
        {
            "beautifulsoup4",
            "cohere",
            "parameterized",
            "pyttsx3",
            "pyyaml",
            "types-requests",
            "wikipedia",
        }
    )


def test_transitive_packages_are_resolver_owned():
    names = set(_requirement_names())

    assert names.isdisjoint(
        {
            "annotated-types",
            "anyio",
            "charset-normalizer",
            "distro",
            "fastavro",
            "filelock",
            "fsspec",
            "h11",
            "httpcore",
            "httpx",
            "httpx-sse",
            "huggingface-hub",
            "idna",
            "jiter",
            "packaging",
            "pydantic",
            "pydantic-core",
            "six",
            "sniffio",
            "soupsieve",
            "tokenizers",
            "tqdm",
            "typing-extensions",
            "urllib3",
        }
    )


def test_platform_locks_are_exact_and_used_by_installer():
    runtime = _locked_packages(RUNTIME_LOCK)
    development = _locked_packages(DEV_LOCK)

    assert runtime < development
    assert "pytest" in development
    assert set(_requirement_names()) <= runtime
    assert RUNTIME_LOCK.name in (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
