"""Block private paths, runtime DB refs, and secrets in tracked/untracked public source."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.path_literals import HOME_DOT_HIKARI, HIKARI_PRIVATE
from tests.privacy_scan import (
    REPO_ROOT,
    SCANNER_SOURCE_REL_FILES,
    collect_public_source_files,
    find_violations,
    privacy_rules,
    scan_file,
    scanner_source_is_generic,
)


def test_no_privacy_violations_in_public_source():
    """Fail before commit if any tracked or untracked public file hits the denylist."""
    violations = find_violations(collect_public_source_files())
    if violations:
        header = (
            "Private paths, runtime DB files, or secret patterns found in public source "
            "(tracked or untracked). Use generic placeholders in docs and fragment-built "
            "paths in code; never commit live brain paths or credentials:\n"
        )
        pytest.fail(header + "\n".join(violations))


def test_privacy_scan_includes_untracked_brain_v2_files():
    """Untracked files under tests/ must be scanned (not only git ls-files tracked)."""
    paths = collect_public_source_files()
    rels = {p.relative_to(REPO_ROOT).as_posix() for p in paths}
    assert "tests/test_brain_v2_eval.py" in rels or any(
        r.startswith("tests/test_brain_v2_") for r in rels
    )


def test_privacy_scan_includes_scanner_definition_files():
    """Scanner sources are scanned (not excluded from collect_public_source_files)."""
    paths = collect_public_source_files()
    rels = {p.relative_to(REPO_ROOT).as_posix() for p in paths}
    for rel in SCANNER_SOURCE_REL_FILES:
        assert rel in rels, f"{rel} must be included in public privacy scan scope"


def test_privacy_scan_includes_protocol_contract():
    paths = collect_public_source_files()
    rels = {p.relative_to(REPO_ROOT).as_posix() for p in paths}
    assert "protocol/hikari-v1.json" in rels


def test_privacy_scanner_source_is_generic():
    """Scanner definition files must not embed legacy name lists or tuple encodings."""
    assert scanner_source_is_generic()


def test_scan_file_redacts_match_in_snippet(tmp_path):
    rule = next(r for r in privacy_rules() if r.rule_id == "path_dot_hikari_home")
    sample = tmp_path / "sample.py"
    sample.write_text(f"# doc mentions live brain at {HOME_DOT_HIKARI}\n", encoding="utf-8")

    hits = scan_file(sample, rules=[rule])

    assert len(hits) == 1
    line_no, rule_id, category, snippet = hits[0]
    assert line_no == 1
    assert rule_id == "path_dot_hikari_home"
    assert category == "private_path"
    assert HOME_DOT_HIKARI not in snippet
    assert "[REDACTED]" in snippet


def test_find_violations_format(tmp_path):
    rule = next(r for r in privacy_rules() if r.rule_id == "path_hikari_private")
    sample = tmp_path / "leak.md"
    sample.write_text(f"data lives in {HIKARI_PRIVATE}/\n", encoding="utf-8")

    violations = find_violations([sample])

    assert len(violations) == 1
    assert "leak.md:1: path_hikari_private (private_path)" in violations[0]
    assert HIKARI_PRIVATE not in violations[0]
    assert "[REDACTED]" in violations[0]


def test_find_violations_never_echoes_api_secret(tmp_path):
    rule = next(r for r in privacy_rules() if r.rule_id == "secret_sk_live")
    secret = "sk-" + ("a" * 24)
    sample = tmp_path / "keys.env"
    sample.write_text(f"OPENAI={secret}\n", encoding="utf-8")

    hits = scan_file(sample, rules=[rule])
    assert hits
    assert secret not in hits[0][3]

    violations = find_violations([sample])
    assert violations
    assert secret not in violations[0]


def test_violation_snippet_redacts_every_sensitive_match(tmp_path):
    private_path = "/" + "Users" + "/private-account/"
    secret = "sk-" + ("a" * 24)
    sample = tmp_path / "multiple.txt"
    sample.write_text(f"{private_path} token={secret}\n", encoding="utf-8")

    violations = find_violations([sample])

    assert violations
    assert private_path not in violations[0]
    assert secret not in violations[0]
    assert violations[0].count("[REDACTED]") == 2


def _privacy_scan_sandbox(tmp_path: Path) -> Path:
    repo = tmp_path / "public-repo"
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "tests/privacy_scan.py", tests_dir / "privacy_scan.py")
    return repo


def test_privacy_scan_cli_succeeds_for_safe_public_source(tmp_path):
    repo = _privacy_scan_sandbox(tmp_path)
    dotenv = "." + "env"
    safe_doc = "\n".join(
        [
            f"cp {dotenv}.example {dotenv}",
            f"The ignored local `{dotenv}` file is not committed.",
        ]
    )
    (repo / "README.md").write_text(safe_doc, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(repo / "tests/privacy_scan.py")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "Privacy scan passed."
    assert result.stderr == ""


def test_privacy_scan_cli_fails_with_redacted_violation(tmp_path):
    repo = _privacy_scan_sandbox(tmp_path)
    private_path = "/" + "Users" + "/private-account/brain.txt"
    (repo / "README.md").write_text(private_path, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(repo / "tests/privacy_scan.py")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "README.md:1: path_macos_users (private_path)" in result.stdout
    assert "[REDACTED]" in result.stdout
    assert private_path not in result.stdout
    assert result.stderr == ""


def test_privacy_scan_still_flags_unapproved_dotenv_reference(tmp_path):
    rule = next(r for r in privacy_rules() if r.rule_id == "env_dotenv")
    dotenv = "." + "env"
    sample = tmp_path / "setup.md"
    sample.write_text(f"Upload {dotenv} to the server.\n", encoding="utf-8")

    hits = scan_file(sample, rules=[rule])

    assert len(hits) == 1
    assert hits[0][1] == "env_dotenv"
    assert dotenv not in hits[0][3]


def test_privacy_scan_rejects_dangerous_dotenv_instruction_with_safe_words(tmp_path):
    rule = next(r for r in privacy_rules() if r.rule_id == "env_dotenv")
    dotenv = "." + "env"
    sample = tmp_path / "setup.md"
    sample.write_text(
        f"Upload the ignored local {dotenv} file to the server.\n",
        encoding="utf-8",
    )

    hits = scan_file(sample, rules=[rule])

    assert len(hits) == 1
    assert hits[0][1] == "env_dotenv"
    assert dotenv not in hits[0][3]


def test_privacy_scan_allows_safe_dotenv_prose_only_in_markdown(tmp_path):
    rule = next(r for r in privacy_rules() if r.rule_id == "env_dotenv")
    dotenv = "." + "env"
    sample = tmp_path / "setup.py"
    sample.write_text(
        f"# The ignored local {dotenv} file is not committed.\n",
        encoding="utf-8",
    )

    hits = scan_file(sample, rules=[rule])

    assert len(hits) == 1
    assert hits[0][1] == "env_dotenv"


def test_privacy_scan_allows_copy_command_only_in_markdown(tmp_path):
    rule = next(r for r in privacy_rules() if r.rule_id == "env_dotenv")
    dotenv = "." + "env"
    command = f"cp {dotenv}.example {dotenv}\n"
    markdown = tmp_path / "setup.md"
    source = tmp_path / "setup.sh"
    markdown.write_text(command, encoding="utf-8")
    source.write_text(command, encoding="utf-8")

    assert scan_file(markdown, rules=[rule]) == []
    source_hits = scan_file(source, rules=[rule])
    assert len(source_hits) == 1
    assert source_hits[0][1] == "env_dotenv"
