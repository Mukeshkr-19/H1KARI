"""Pure, immutable contracts for the Phase 6 developer-mode intelligence layer.

All public types are frozen. No I/O, no side effects, no subprocess, no network,
and no Git execution occur in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Optional, Tuple

# --- Canonical validation helpers ---------------------------------------------------------

_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-][A-Za-z0-9_. /-]{0,254}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_REF_RE = re.compile(r"^[a-zA-Z0-9_.-/]{1,255}$")

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_MAX_REASON_LENGTH = 120
_MAX_TEXT_LENGTH = 4096
_MAX_EXCERPT_LINES = 12
_MAX_EXCERPT_CHARS = 2048
_MAX_HITS = 100
_MAX_QUERY_LENGTH = 256


class RepositoryReason(StrEnum):
    """Fixed, privacy-safe reason codes for repository decisions."""

    OK = "ok"
    INVALID_PATH = "invalid_path"
    PATH_OUTSIDE_ROOT = "path_outside_root"
    CONTROL_CHARACTERS = "control_characters"
    SYMLINK_NOT_ALLOWED = "symlink_not_allowed"
    SYMLINK_ESCAPE = "symlink_escape"
    DOTGIT_TRAVERSAL = "dotgit_traversal"
    PRIVATE_FILE_EXCLUDED = "private_file_excluded"
    BINARY_FILE = "binary_file"
    OVERSIZED_FILE = "oversized_file"
    TOO_MANY_FILES = "too_many_files"
    TOTAL_SIZE_EXCEEDED = "total_size_exceeded"
    DEPTH_EXCEEDED = "depth_exceeded"
    LINE_LENGTH_EXCEEDED = "line_length_exceeded"
    MALFORMED_REQUEST = "malformed_request"
    EMPTY_QUERY = "empty_query"


class RelationshipType(StrEnum):
    """Supported repository relationship edge types."""

    DEFINES = "defines"
    IMPORTS = "imports"
    CALLS = "calls"
    REFERENCES = "references"
    DOCUMENTS = "documents"
    TESTS = "tests"


class FileKind(StrEnum):
    """High-level file classification."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    MARKDOWN = "markdown"
    CONFIG = "config"
    TEXT = "text"
    OTHER = "other"
    BINARY = "binary"


class GitOperationClass(StrEnum):
    """Classification of Git operation side-effect risk."""

    READ_ONLY = "read_only"
    REVERSIBLE_MUTATION = "reversible_mutation"
    HISTORY_MUTATION = "history_mutation"
    DESTRUCTIVE = "destructive"
    REMOTE_MUTATION = "remote_mutation"
    UNKNOWN = "unknown"


class GitPolicyOutcome(StrEnum):
    """Fixed Git policy outcomes."""

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class GitPolicyReason(StrEnum):
    """Fixed, privacy-safe Git policy reason codes."""

    READ_ONLY = "read_only"
    APPROVAL_REQUIRED = "approval_required"
    UNKNOWN_OPERATION = "unknown_operation"
    SHELL_METACHARACTERS = "shell_metacharacters"
    INVALID_REQUEST = "invalid_request"
    SUBCOMMAND_REQUIRED = "subcommand_required"
    DIRTY_WORKTREE = "dirty_worktree"
    FORCE_PUSH = "force_push"
    HISTORY_REWRITE = "history_rewrite"
    DESTRUCTIVE_OPERATION = "destructive_operation"
    REMOTE_OPERATION = "remote_operation"
    BROAD_ROOT_TARGET = "broad_root_target"
    UNRESOLVED_VARIABLE = "unresolved_variable"


class SandboxOutcome(StrEnum):
    """Fixed sandbox policy outcomes."""

    ALLOW = "allow"
    DENY = "deny"


class SandboxReason(StrEnum):
    """Fixed, privacy-safe sandbox policy reason codes."""

    ALLOWED = "allowed"
    UNKNOWN_EXECUTABLE = "unknown_executable"
    SUBCOMMAND_DENIED = "subcommand_denied"
    INTERPRETER_WITHOUT_SCRIPT = "interpreter_without_script"
    SCRIPT_NOT_IN_READ_ROOTS = "script_not_in_read_roots"
    SHELL_METACHARACTERS = "shell_metacharacters"
    REDIRECTION_OR_SUBSTITUTION = "redirection_or_substitution"
    PATH_ESCAPE = "path_escape"
    NETWORK_DENIED = "network_denied"
    ENVIRONMENT_NOT_ALLOWED = "environment_not_allowed"
    TIMEOUT_EXCEEDED = "timeout_exceeded"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    MEMORY_LIMIT_EXCEEDED = "memory_limit_exceeded"
    UNSAFE_FLAG = "unsafe_flag"
    INVALID_REQUEST = "invalid_request"
    PATH_NOT_IN_SCOPE = "path_not_in_scope"
    REVIEWED_SCRIPT_REQUIRED = "reviewed_script_required"
    MUTATION_REQUIRES_SEPARATE_POLICY = "mutation_requires_separate_policy"


# --- Validators ---------------------------------------------------------------------------


def _validate_identifier(value: Optional[str], field: str) -> None:
    if value is None or not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")


def _validate_bounded_text(value: Optional[str], field: str, max_length: int = _MAX_TEXT_LENGTH) -> str:
    if value is None or not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > max_length:
        raise ValueError(f"{field} exceeds maximum length")
    if _CONTROL_CHAR_RE.search(value):
        raise ValueError(f"{field} contains control characters")
    return value


# --- Repository contracts -----------------------------------------------------------------


@dataclass(frozen=True)
class RepositoryRoot:
    """Validated, absolute repository root.

    The constructor ensures the path is absolute, resolved, and a directory.
    """

    path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise ValueError("path must be a pathlib.Path")
        resolved = self.path.expanduser().resolve()
        if not resolved.is_absolute():
            raise ValueError("repository root must be absolute")
        if not resolved.is_dir():
            raise ValueError("repository root must be a directory")
        object.__setattr__(self, "path", resolved)

    def __repr__(self) -> str:
        return "RepositoryRoot(...)"


@dataclass(frozen=True)
class RepositoryPolicy:
    """Bounded, immutable policy for repository traversal and indexing."""

    max_files: int = 10_000
    max_total_bytes: int = 100_000_000
    max_file_bytes: int = 5_000_000
    max_depth: int = 32
    max_line_length: int = 8_192
    ignore_patterns: Tuple[str, ...] = (
        ".git",
        "." + "env",
        "*." + "env",
        "node_modules",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".DS_Store",
        "*credentials*",
        "*secrets*",
        "*private*",
    )
    follow_symlinks: bool = False
    allowed_extensions: Optional[Tuple[str, ...]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.max_files, int) or not 0 < self.max_files <= 10_000:
            raise ValueError("max_files must be positive")
        if not isinstance(self.max_total_bytes, int) or not 0 < self.max_total_bytes <= 100_000_000:
            raise ValueError("max_total_bytes must be positive")
        if not isinstance(self.max_file_bytes, int) or not 0 < self.max_file_bytes <= 5_000_000:
            raise ValueError("max_file_bytes must be positive")
        if not isinstance(self.max_depth, int) or not 0 < self.max_depth <= 64:
            raise ValueError("max_depth must be positive")
        if not isinstance(self.max_line_length, int) or not 0 < self.max_line_length <= 32_768:
            raise ValueError("max_line_length must be positive")
        if not isinstance(self.ignore_patterns, tuple) or not all(
            isinstance(p, str) for p in self.ignore_patterns
        ):
            raise ValueError("ignore_patterns must be a tuple of strings")
        if self.allowed_extensions is not None and not isinstance(self.allowed_extensions, tuple):
            raise ValueError("allowed_extensions must be a tuple or None")

    def __repr__(self) -> str:
        return (
            f"RepositoryPolicy(max_files={self.max_files}, "
            f"max_file_bytes={self.max_file_bytes})"
        )


@dataclass(frozen=True)
class FileSnapshot:
    """Bounded snapshot of a single repository file.

    The excerpt is intentionally short to avoid leaking whole-file contents.
    """

    relative_path: str
    kind: FileKind
    size_bytes: int
    is_binary: bool
    lines: Tuple[str, ...]
    line_count: int
    sha256_hex: str

    def __post_init__(self) -> None:
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise ValueError("relative_path is required")
        if not isinstance(self.kind, FileKind):
            raise ValueError("invalid file kind")
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if not isinstance(self.line_count, int) or self.line_count < 0:
            raise ValueError("line_count must be non-negative")
        if not isinstance(self.lines, tuple):
            raise ValueError("lines must be a tuple")
        if len(self.lines) > _MAX_EXCERPT_LINES:
            raise ValueError("too many excerpt lines")
        if not isinstance(self.sha256_hex, str) or len(self.sha256_hex) != 64:
            raise ValueError("sha256_hex must be 64 hex chars")

    def __repr__(self) -> str:
        return (
            f"FileSnapshot(kind={self.kind.value!r}, "
            f"size_bytes={self.size_bytes}, line_count={self.line_count})"
        )


@dataclass(frozen=True)
class SymbolRecord:
    """A named symbol discovered in a repository file."""

    name: str
    kind: str
    relative_path: str
    line_start: int
    line_end: Optional[int]
    parent: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("symbol name is required")
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError("symbol kind is required")
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise ValueError("relative_path is required")
        if not isinstance(self.line_start, int) or self.line_start <= 0:
            raise ValueError("line_start must be positive")
        if self.line_end is not None and not isinstance(self.line_end, int):
            raise ValueError("line_end must be an integer or None")
        if self.parent is not None and not isinstance(self.parent, str):
            raise ValueError("parent must be a string or None")

    def __repr__(self) -> str:
        return f"SymbolRecord(kind={self.kind!r}, line={self.line_start})"


@dataclass(frozen=True)
class ImportRecord:
    """A Python or JS/TS import discovered in a file."""

    module: Optional[str]
    name: str
    is_from: bool
    relative_path: str
    line: int

    def __post_init__(self) -> None:
        if self.module is not None and not isinstance(self.module, str):
            raise ValueError("module must be a string or None")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("import name is required")
        if not isinstance(self.is_from, bool):
            raise ValueError("is_from must be boolean")
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise ValueError("relative_path is required")
        if not isinstance(self.line, int) or self.line <= 0:
            raise ValueError("line must be positive")

    def __repr__(self) -> str:
        return f"ImportRecord(name={self.name!r}, line={self.line})"


@dataclass(frozen=True)
class ReferenceRecord:
    """A reference from one symbol to another."""

    source_symbol: str
    target_symbol: str
    edge_type: RelationshipType
    relative_path: str
    line: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_symbol, str) or not self.source_symbol:
            raise ValueError("source_symbol is required")
        if not isinstance(self.target_symbol, str) or not self.target_symbol:
            raise ValueError("target_symbol is required")
        if not isinstance(self.edge_type, RelationshipType):
            raise ValueError("invalid edge type")
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise ValueError("relative_path is required")
        if not isinstance(self.line, int) or self.line <= 0:
            raise ValueError("line must be positive")

    def __repr__(self) -> str:
        return f"ReferenceRecord(edge={self.edge_type.value!r}, line={self.line})"


@dataclass(frozen=True)
class RelationshipEdge:
    """A normalized, provenance-bearing relationship edge."""

    source_id: str
    target_id: str
    edge_type: RelationshipType
    provenance: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError("source_id is required")
        if not isinstance(self.target_id, str) or not self.target_id:
            raise ValueError("target_id is required")
        if not isinstance(self.edge_type, RelationshipType):
            raise ValueError("invalid edge type")
        if not isinstance(self.provenance, str) or not self.provenance:
            raise ValueError("provenance is required")

    def __repr__(self) -> str:
        return f"RelationshipEdge(edge={self.edge_type.value!r})"


@dataclass(frozen=True)
class RepositoryIndex:
    """Immutable repository index built from a deterministic read-only scan."""

    files: Tuple[FileSnapshot, ...]
    symbols: Tuple[SymbolRecord, ...]
    imports: Tuple[ImportRecord, ...]
    references: Tuple[ReferenceRecord, ...]
    edges: Tuple[RelationshipEdge, ...]
    total_bytes: int

    def __post_init__(self) -> None:
        if not all(isinstance(f, FileSnapshot) for f in self.files):
            raise ValueError("files must be FileSnapshot instances")
        if not all(isinstance(s, SymbolRecord) for s in self.symbols):
            raise ValueError("symbols must be SymbolRecord instances")
        if not all(isinstance(i, ImportRecord) for i in self.imports):
            raise ValueError("imports must be ImportRecord instances")
        if not all(isinstance(r, ReferenceRecord) for r in self.references):
            raise ValueError("references must be ReferenceRecord instances")
        if not all(isinstance(e, RelationshipEdge) for e in self.edges):
            raise ValueError("edges must be RelationshipEdge instances")
        if not isinstance(self.total_bytes, int) or self.total_bytes < 0:
            raise ValueError("total_bytes must be non-negative")

    def __repr__(self) -> str:
        return (
            f"RepositoryIndex(files={len(self.files)}, "
            f"symbols={len(self.symbols)}, edges={len(self.edges)})"
        )


@dataclass(frozen=True)
class RepositoryQuery:
    """Caller-supplied deterministic query against a repository index."""

    query_text: str
    kind: Optional[str] = None
    max_hits: int = 20
    exact_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.query_text, str):
            raise ValueError("query_text must be a string")
        if len(self.query_text) > _MAX_QUERY_LENGTH:
            raise ValueError("query_text exceeds maximum length")
        if not isinstance(self.max_hits, int) or not 0 < self.max_hits <= _MAX_HITS:
            raise ValueError("max_hits must be positive")
        if not isinstance(self.exact_only, bool):
            raise ValueError("exact_only must be boolean")

    def __repr__(self) -> str:
        return (
            f"RepositoryQuery(kind={self.kind!r}, "
            f"max_hits={self.max_hits}, exact_only={self.exact_only})"
        )


@dataclass(frozen=True)
class ScoreBreakdown:
    """Transparent, deterministic score components for a single hit."""

    exact_match: int = 0
    token_match: int = 0
    substring_match: int = 0
    relationship_bonus: int = 0

    def total(self) -> float:
        return (
            self.exact_match * 1.0
            + self.token_match * 0.5
            + self.substring_match * 0.2
            + self.relationship_bonus * 0.3
        )

    def __repr__(self) -> str:
        return f"ScoreBreakdown(total={self.total():.2f})"


@dataclass(frozen=True)
class RepositoryHit:
    """A single ranked search result with bounded provenance."""

    relative_path: str
    symbol: Optional[str]
    line_start: int
    line_end: int
    excerpt: Tuple[str, ...]
    score: float
    score_breakdown: ScoreBreakdown
    provenance: str

    def __post_init__(self) -> None:
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise ValueError("relative_path is required")
        if self.symbol is not None and not isinstance(self.symbol, str):
            raise ValueError("symbol must be a string or None")
        if not isinstance(self.line_start, int) or self.line_start <= 0:
            raise ValueError("line_start must be positive")
        if not isinstance(self.line_end, int) or self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        if not isinstance(self.excerpt, tuple):
            raise ValueError("excerpt must be a tuple")
        if len(self.excerpt) > _MAX_EXCERPT_LINES:
            raise ValueError("excerpt too long")
        if not isinstance(self.score, (int, float)) or self.score < 0:
            raise ValueError("score must be non-negative")
        if not isinstance(self.score_breakdown, ScoreBreakdown):
            raise ValueError("score_breakdown required")
        if not isinstance(self.provenance, str) or not self.provenance:
            raise ValueError("provenance is required")

    def __repr__(self) -> str:
        return (
            f"RepositoryHit(score={self.score:.2f}, "
            f"line={self.line_start}, symbol={self.symbol!r})"
        )


@dataclass(frozen=True)
class RepositoryQueryResult:
    """Result of a repository query."""

    hits: Tuple[RepositoryHit, ...]
    reason: RepositoryReason

    def __post_init__(self) -> None:
        if not isinstance(self.hits, tuple):
            raise ValueError("hits must be a tuple")
        if not all(isinstance(h, RepositoryHit) for h in self.hits):
            raise ValueError("hits must be RepositoryHit instances")
        if not isinstance(self.reason, RepositoryReason):
            raise ValueError("reason must be a RepositoryReason")

    def __repr__(self) -> str:
        return f"RepositoryQueryResult(hits={len(self.hits)}, reason={self.reason.value!r})"


# --- Git policy contracts -----------------------------------------------------------------


@dataclass(frozen=True)
class GitStateSnapshot:
    """Caller-supplied snapshot of a Git working tree state."""

    branch: Optional[str]
    head: Optional[str]
    has_untracked: bool
    has_unstaged: bool
    has_staged: bool
    has_unmerged: bool
    ahead_remote: int = 0
    behind_remote: int = 0

    def is_dirty(self) -> bool:
        return self.has_untracked or self.has_unstaged or self.has_staged or self.has_unmerged

    def __repr__(self) -> str:
        return (
            f"GitStateSnapshot(dirty={self.is_dirty()}, "
            f"ahead={self.ahead_remote}, behind={self.behind_remote})"
        )


@dataclass(frozen=True)
class GitOperationRequest:
    """Caller-supplied Git operation request.

    ``argv`` is an exact argument vector (never a shell string). The policy
    evaluator treats a missing or non-``git`` executable as a denial; the
    constructor remains permissive so callers receive a deterministic policy
    decision rather than a construction-time exception.
    """

    argv: Tuple[str, ...]
    cwd: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.argv, tuple):
            raise ValueError("argv must be a tuple")
        if not self.argv:
            raise ValueError("argv must not be empty")
        if not all(isinstance(a, str) for a in self.argv):
            raise ValueError("argv elements must be strings")
        if self.cwd is not None and not isinstance(self.cwd, str):
            raise ValueError("cwd must be a string or None")

    def __repr__(self) -> str:
        operation = self.argv[1] if len(self.argv) > 1 else "missing"
        return f"GitOperationRequest(operation={operation!r})"


@dataclass(frozen=True)
class GitPolicyDecision:
    """Immutable decision from the Git policy planner."""

    outcome: GitPolicyOutcome
    classification: GitOperationClass
    reason: GitPolicyReason

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, GitPolicyOutcome):
            raise ValueError("invalid outcome")
        if not isinstance(self.classification, GitOperationClass):
            raise ValueError("invalid classification")
        if not isinstance(self.reason, GitPolicyReason):
            raise ValueError("invalid reason")

    def __repr__(self) -> str:
        return (
            f"GitPolicyDecision(outcome={self.outcome.value!r}, "
            f"classification={self.classification.value!r})"
        )


# --- Sandbox policy contracts -------------------------------------------------------------


@dataclass(frozen=True)
class SandboxCommandRequest:
    """Caller-supplied sandbox command request.

    ``argv`` is an exact argument vector (never a shell string).
    """

    argv: Tuple[str, ...]
    read_roots: Tuple[str, ...] = ()
    write_roots: Tuple[str, ...] = ()
    network_allowed: bool = False
    env_allowlist: Tuple[str, ...] = ()
    timeout_seconds: Optional[int] = None
    max_output_bytes: Optional[int] = None
    max_memory_bytes: Optional[int] = None
    cwd: Optional[str] = None
    reviewed_scripts: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.argv, tuple):
            raise ValueError("argv must be a tuple")
        if not self.argv:
            raise ValueError("argv must not be empty")
        if not all(isinstance(a, str) for a in self.argv):
            raise ValueError("argv elements must be strings")
        if not isinstance(self.read_roots, tuple) or not all(
            isinstance(r, str) for r in self.read_roots
        ):
            raise ValueError("read_roots must be a tuple of strings")
        if not isinstance(self.write_roots, tuple) or not all(
            isinstance(w, str) for w in self.write_roots
        ):
            raise ValueError("write_roots must be a tuple of strings")
        if not isinstance(self.network_allowed, bool):
            raise ValueError("network_allowed must be boolean")
        if not isinstance(self.env_allowlist, tuple) or not all(
            isinstance(e, str) for e in self.env_allowlist
        ):
            raise ValueError("env_allowlist must be a tuple of strings")
        for env in self.env_allowlist:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env):
                raise ValueError("invalid env_allowlist entry")
        if self.cwd is not None and (
            not isinstance(self.cwd, str) or not self.cwd.startswith("/")
        ):
            raise ValueError("cwd must be an absolute path or None")
        if not isinstance(self.reviewed_scripts, tuple) or not all(
            isinstance(script, str) and script.startswith("/")
            for script in self.reviewed_scripts
        ):
            raise ValueError("reviewed_scripts must contain absolute paths")
        if self.timeout_seconds is not None and (
            not isinstance(self.timeout_seconds, int) or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_bytes is not None and (
            not isinstance(self.max_output_bytes, int) or self.max_output_bytes <= 0
        ):
            raise ValueError("max_output_bytes must be positive")
        if self.max_memory_bytes is not None and (
            not isinstance(self.max_memory_bytes, int) or self.max_memory_bytes <= 0
        ):
            raise ValueError("max_memory_bytes must be positive")

    def __repr__(self) -> str:
        return f"SandboxCommandRequest(argv_count={len(self.argv)})"


@dataclass(frozen=True)
class SandboxPolicyDecision:
    """Immutable sandbox policy decision."""

    outcome: SandboxOutcome
    reason: SandboxReason

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, SandboxOutcome):
            raise ValueError("invalid outcome")
        if not isinstance(self.reason, SandboxReason):
            raise ValueError("invalid reason")

    def __repr__(self) -> str:
        return f"SandboxPolicyDecision(outcome={self.outcome.value!r})"


# --- Change intent ------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeIntent:
    """A caller-supplied intended code change (not executed)."""

    change_type: str
    relative_path: str
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.change_type, str) or not self.change_type:
            raise ValueError("change_type is required")
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise ValueError("relative_path is required")
        _validate_bounded_text(self.description, "description")

    def __repr__(self) -> str:
        return f"ChangeIntent(change_type={self.change_type!r})"
