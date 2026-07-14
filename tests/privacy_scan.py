"""Public-source privacy scan helpers (tracked + untracked, generic denylist only)."""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Pattern, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

SCAN_PREFIXES = (
    "core/",
    "tests/",
    "docs/",
    "services/",
    "security/",
    "skills/",
    "agents/",
    "bin/",
    "config/",
    "protocol/",
    "scripts/",
    "hikari-frontend/",
)

EXPLICIT_ROOT_FILES = (
    "hikari.py",
    "README.md",
    "AGENTS.md",
    "QUICKSTART.md",
    "ARCHITECTURE.md",
)


def _join(*parts: str) -> str:
    return "".join(parts)


SKIP_PATH_MARKERS = (
    ".git/",
    ".venv/",
    "node_modules/",
    _join("HIKARI", "-private/"),
    "__pycache__/",
    ".pytest_cache/",
    "/dist/",
    "/build/",
    "/.next/",
    "/coverage/",
    ".mypy_cache/",
    ".ruff_cache/",
)

BINARY_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".sqlite",
    ".db",
    ".pyc",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".bin",
    ".lock",
)

SCANNER_SOURCE_REL_FILES = [
    "tests/privacy_scan.py",
    "tests/test_privacy_terms.py",
]

# Allowlisted tuple-of-strings only inside these function names (generic denylist builders).
_ALLOWED_TUPLE_FN_NAMES = frozenset(
    {
        "_literal_rules",
        "_regex_rules",
        "_join",
        "_episodes_db_pattern",
        "_dotenv_pattern",
    }
)


@dataclass(frozen=True)
class PrivacyRule:
    rule_id: str
    category: str
    pattern: Pattern[str]


def _literal_rules() -> Tuple[Tuple[str, str, str], ...]:
    """Needles for substring rules; literals built from fragments (no private names)."""
    return (
        ("path_hikari_private", "private_path", _join("HIKARI", "-private")),
        ("path_dot_hikari_home", "private_path", _join("~/", ".hikari")),
        ("path_dot_hikari_brain", "private_path", _join(".hikari", "/brain")),
        ("db_hikari_memory", "runtime_db", _join("hikari_", "memory.db")),
    )


def _episodes_db_pattern() -> str:
    needle = _join("episodes", ".db")
    return rf"(?<![a-zA-Z0-9_-]){re.escape(needle)}"


def _dotenv_pattern() -> str:
    dotenv = re.escape(_join(".", "env"))
    return rf"(?<![\w./]){dotenv}(?![\w./]|\.example|\*)"


def _regex_rules() -> Tuple[Tuple[str, str, str], ...]:
    """(rule_id, category, pattern_source) for compiled regex rules."""
    return (
        ("env_dotenv", "credentials", _dotenv_pattern()),
        ("env_credentials_json", "credentials", r"credentials\.json"),
        ("db_episodes", "runtime_db", _episodes_db_pattern()),
        ("secret_openai_api_key", "api_secret", r"OPENAI_API_KEY\s*="),
        ("secret_anthropic_api_key", "api_secret", r"ANTHROPIC_API_KEY\s*="),
        ("secret_sk_live", "api_secret", r"\bsk-[a-zA-Z0-9]{20,}"),
        ("secret_ghp", "api_secret", r"\bghp_[a-zA-Z0-9]{20,}"),
        ("secret_xoxb", "api_secret", r"\bxoxb-[a-zA-Z0-9-]{10,}"),
        ("secret_akia", "api_secret", r"\bAKIA[0-9A-Z]{16}"),
        ("path_macos_users", "private_path", r"/Users/[a-zA-Z0-9._-]+/"),
    )


def privacy_rules() -> Tuple[PrivacyRule, ...]:
    rules: List[PrivacyRule] = []
    for rule_id, category, needle in _literal_rules():
        rules.append(
            PrivacyRule(
                rule_id=rule_id,
                category=category,
                pattern=re.compile(re.escape(needle), re.IGNORECASE),
            )
        )
    for rule_id, category, source in _regex_rules():
        rules.append(
            PrivacyRule(
                rule_id=rule_id,
                category=category,
                pattern=re.compile(source, re.IGNORECASE),
            )
        )
    return tuple(rules)


PRIVACY_RULES: Tuple[PrivacyRule, ...] = privacy_rules()


def _should_scan_rel(rel: str) -> bool:
    if any(marker in rel for marker in SKIP_PATH_MARKERS):
        return False
    if rel in EXPLICIT_ROOT_FILES:
        return True
    return any(rel.startswith(prefix) for prefix in SCAN_PREFIXES)


def _git_paths(extra_args: List[str]) -> Set[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", *extra_args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _walk_scan_scope() -> Set[str]:
    """Filesystem fallback when git is unavailable (respects same scope filters)."""
    rel_paths: Set[str] = set()
    for name in EXPLICIT_ROOT_FILES:
        if (REPO_ROOT / name).is_file():
            rel_paths.add(name)
    for prefix in SCAN_PREFIXES:
        root = REPO_ROOT / prefix.rstrip("/")
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if _should_scan_rel(rel) and not rel.lower().endswith(BINARY_SUFFIXES):
                rel_paths.add(rel)
    return rel_paths


def _collect_rel_paths() -> Set[str]:
    """Tracked + untracked (non-ignored) paths under scan scope."""
    rel_paths = _git_paths([])
    rel_paths |= _git_paths(["-o", "--exclude-standard"])
    if not rel_paths:
        rel_paths = _walk_scan_scope()
    return rel_paths


def collect_public_source_files() -> List[Path]:
    """Tracked and untracked (non-ignored) public text files under scan scope."""
    paths: List[Path] = []
    seen: Set[str] = set()
    for rel in sorted(_collect_rel_paths()):
        if not rel or rel in seen:
            continue
        if not _should_scan_rel(rel):
            continue
        if rel.lower().endswith(BINARY_SUFFIXES):
            continue
        seen.add(rel)
        path = REPO_ROOT / rel
        if path.is_file():
            paths.append(path)
    return paths


def _redact_snippet(line: str, match: re.Match[str], *, max_len: int = 120) -> str:
    redacted = line[: match.start()] + "[REDACTED]" + line[match.end() :]
    redacted = redacted.strip()
    if len(redacted) > max_len:
        return redacted[: max_len - 3] + "..."
    return redacted


def scan_file(path: Path, rules: Sequence[PrivacyRule] | None = None) -> List[Tuple[int, str, str, str]]:
    """Return (line_no, rule_id, category, redacted_snippet) hits."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    active_rules = rules if rules is not None else PRIVACY_RULES
    hits: List[Tuple[int, str, str, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule in active_rules:
            match = rule.pattern.search(line)
            if match is None:
                continue
            hits.append(
                (
                    line_no,
                    rule.rule_id,
                    rule.category,
                    _redact_snippet(line, match),
                )
            )
            break
    return hits


def format_violation(rel: Path | str, line_no: int, rule_id: str, category: str, snippet: str) -> str:
    return f"{rel}:{line_no}: {rule_id} ({category}) — {snippet}"


def find_violations(paths: Iterable[Path] | None = None) -> List[str]:
    """Return human-readable violation lines (secrets never echoed verbatim)."""
    violations: List[str] = []
    for path in paths or collect_public_source_files():
        for line_no, rule_id, category, snippet in scan_file(path):
            try:
                rel = path.relative_to(REPO_ROOT)
            except ValueError:
                rel = path
            violations.append(format_violation(rel, line_no, rule_id, category, snippet))
    return violations


def scanner_source_is_generic() -> bool:
    """True when scanner sources avoid legacy name lists and hidden string-tuple encodings."""
    legacy_markers = [
        _join("_BANNED", "_PARTS"),
        _join("BANNED", "_TERMS"),
        _join("banned", "_terms("),
        _join("forbidden", "_fragment_groups"),
        _join("SCAN", "_SKIP"),
    ]
    for rel in SCANNER_SOURCE_REL_FILES:
        path = REPO_ROOT / rel
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in legacy_markers):
            return False
        if _has_disallowed_string_tuple_fragments(text):
            return False
    return True


def _has_disallowed_string_tuple_fragments(source: str) -> bool:
    """Reject nested tuples of string constants outside allowlisted builder functions."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True

    allowed_nodes: Set[ast.AST] = set()

    class _AllowlistVisitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    allowed_nodes.add(node.value)
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node.name in _ALLOWED_TUPLE_FN_NAMES:
                for child in ast.walk(node):
                    allowed_nodes.add(child)
            self.generic_visit(node)

    _AllowlistVisitor().visit(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple):
            continue
        if node in allowed_nodes:
            continue
        string_parts = [
            elt.value
            for elt in node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]
        if len(string_parts) >= 2:
            return True
        if len(string_parts) == 1 and len(string_parts[0]) <= 3:
            return True
    return False
