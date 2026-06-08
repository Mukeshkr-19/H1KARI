"""Block private paths, runtime DB refs, and secrets in tracked/untracked public source."""

from __future__ import annotations

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
