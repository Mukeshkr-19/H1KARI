"""Reject conversation, patch, and generation residue from public source files."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Pattern, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.privacy_scan import REPO_ROOT, collect_public_source_files


def _join(*parts: str) -> str:
    return "".join(parts)


@dataclass(frozen=True)
class ArtifactRule:
    rule_id: str
    pattern: Pattern[str]


def artifact_rules() -> tuple[ArtifactRule, ...]:
    sources = (
        ("generation_attribution", _join(r"generated", r"(?:-|\s)+", r"by")),
        ("copied_instruction", _join(r"copied", r"\s+", r"prompt")),
        ("tool_artifact_path", _join(r"agent", r"-", r"tools/")),
        ("patch_header", _join(r"\*\*\*\s+", r"begin", r"\s+patch")),
        ("patch_footer", _join(r"\*\*\*\s+", r"end", r"\s+patch")),
        ("tool_call_record", _join(r"tool", r"[_ -]", r"call", r"[_ -]", r"output")),
        ("chat_export_role", _join(r"^", r"assistant", r"\s+to=")),
        ("orchestration_residue", _join(r"orchestration", r"\s+", r"details")),
    )
    return tuple(
        ArtifactRule(rule_id, re.compile(source, re.IGNORECASE | re.MULTILINE))
        for rule_id, source in sources
    )


def find_artifact_violations(
    paths: Sequence[Path], rules: Sequence[ArtifactRule] | None = None
) -> list[str]:
    active_rules = tuple(rules or artifact_rules())
    violations: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule in active_rules:
                if rule.pattern.search(line):
                    try:
                        rel = path.relative_to(REPO_ROOT).as_posix()
                    except ValueError:
                        rel = path.name
                    violations.append(f"{rel}:{line_number}: {rule.rule_id}")
    return violations


def main() -> int:
    violations = find_artifact_violations(collect_public_source_files())
    if violations:
        print("Public artifact scan failed:")
        print("\n".join(violations))
        return 1
    print("Public artifact scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
