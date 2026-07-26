"""Read-only repository adapter for Phase 6 developer mode.

This module performs no writes, no subprocess, no Git commands, and no code
imports. It walks a caller-supplied root, validates every path, and returns a
bounded, deterministic ``RepositoryIndex``.
"""

from __future__ import annotations

import ast as _ast
import fnmatch
import hashlib
import os
from pathlib import Path
from typing import Optional, Tuple

from core.phase6_developer.contracts import (
    FileKind,
    FileSnapshot,
    ImportRecord,
    ReferenceRecord,
    RelationshipEdge,
    RelationshipType,
    RepositoryIndex,
    RepositoryPolicy,
    RepositoryReason,
    RepositoryRoot,
    SymbolRecord,
)


class RepositoryScanError(ValueError):
    """Raised when a repository scan hits a hard policy limit.

    The exception carries a fixed privacy-safe reason code and an optional
    partial index of everything read up to the failure point.
    """

    def __init__(
        self,
        reason: RepositoryReason,
        partial: Optional[RepositoryIndex] = None,
    ) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.partial = partial

    def __repr__(self) -> str:
        return f"RepositoryScanError(reason={self.reason.value!r})"


# --- Low-level helpers ----------------------------------------------------------------


def _contains_control_chars(text: str) -> bool:
    return any(ord(ch) < 32 and ch not in {"\n", "\r", "\t"} for ch in text)


def _is_binary(content: bytes) -> bool:
    """Conservative binary detection.

    A file is treated as binary if it contains a NUL byte or if more than 30%
    of its bytes are non-printable ASCII in the first 8 KB.
    """
    if b"\x00" in content:
        return True
    if len(content) == 0:
        return False
    sample = content[:8192]
    non_printable = sum(1 for b in sample if b < 32 and b not in (9, 10, 13))
    return non_printable > len(sample) * 0.30


def _classify_file(relative_path: str) -> FileKind:
    lower = relative_path.lower()
    if lower.endswith(".py"):
        return FileKind.PYTHON
    if lower.endswith(".js") or lower.endswith(".mjs") or lower.endswith(".cjs"):
        return FileKind.JAVASCRIPT
    if lower.endswith(".ts") or lower.endswith(".tsx"):
        return FileKind.TYPESCRIPT
    if lower.endswith(".md"):
        return FileKind.MARKDOWN
    if lower.endswith((".txt", ".rst", ".log")):
        return FileKind.TEXT
    if lower.endswith((".json", ".yaml", ".yml", ".toml", ".ini", ".cfg")):
        return FileKind.CONFIG
    return FileKind.OTHER


def _is_credential_filename(name: str) -> bool:
    lower = name.lower()
    if lower.startswith("." + "env"):
        return True
    if "credentials" in lower or "secrets" in lower or "private" in lower:
        return True
    return False


def _is_excluded(path: Path, relative_path: str, policy: RepositoryPolicy) -> bool:
    parts = path.parts
    if ".git" in parts:
        return True
    name = path.name
    for pattern in policy.ignore_patterns:
        if pattern.startswith("*") or pattern.endswith("*"):
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative_path, pattern):
                return True
        else:
            if any(part == pattern or fnmatch.fnmatch(part, pattern) for part in parts):
                return True
            if fnmatch.fnmatch(name, pattern):
                return True
    if _is_credential_filename(name):
        return True
    if policy.allowed_extensions is not None:
        ext = path.suffix.lower()
        if ext not in policy.allowed_extensions:
            return True
    return False


# --- Path containment -------------------------------------------------------------------


def _validate_requested_path(
    root: RepositoryRoot,
    requested: Path,
    follow_symlinks: bool = False,
) -> Path:
    """Resolve a requested path against the repository root.

    Raises RepositoryScanError if the path is invalid or escapes the root.
    """
    if not isinstance(requested, Path):
        raise RepositoryScanError(RepositoryReason.INVALID_PATH)
    abs_root = root.path.resolve()
    raw_candidate = requested.expanduser()
    if not raw_candidate.is_absolute():
        raw_candidate = abs_root / raw_candidate
    is_symlink = raw_candidate.is_symlink()
    if is_symlink and not follow_symlinks:
        raise RepositoryScanError(RepositoryReason.SYMLINK_NOT_ALLOWED)
    try:
        candidate = raw_candidate.resolve()
    except OSError:
        raise RepositoryScanError(RepositoryReason.INVALID_PATH) from None

    if os.path.commonpath([str(abs_root), str(candidate)]) != str(abs_root):
        reason = RepositoryReason.SYMLINK_ESCAPE if is_symlink else RepositoryReason.PATH_OUTSIDE_ROOT
        raise RepositoryScanError(reason)

    if "\\.git" in str(candidate) or "/.git" in str(candidate):
        raise RepositoryScanError(RepositoryReason.DOTGIT_TRAVERSAL)

    return candidate


# --- File reading -----------------------------------------------------------------------


def _read_file_content(
    root: RepositoryRoot,
    file_path: Path,
    policy: RepositoryPolicy,
    relative_path: Optional[str] = None,
) -> Optional[Tuple[FileSnapshot, str]]:
    """Read a single file and return both its bounded snapshot and full text."""
    try:
        abs_path = file_path.resolve()
    except OSError:
        return None

    if relative_path is None:
        try:
            relative_path = abs_path.relative_to(root.path).as_posix()
        except ValueError:
            return None

    if _contains_control_chars(relative_path):
        return None

    depth = len(Path(relative_path).parts)
    if depth > policy.max_depth:
        return None

    try:
        size = abs_path.stat().st_size
    except OSError:
        return None

    if size > policy.max_file_bytes:
        raise RepositoryScanError(RepositoryReason.OVERSIZED_FILE)

    try:
        content = abs_path.read_bytes()
    except (OSError, UnicodeDecodeError):
        return None

    digest = hashlib.sha256(content).hexdigest()

    if _is_binary(content):
        snapshot = FileSnapshot(
            relative_path=relative_path,
            kind=FileKind.BINARY,
            size_bytes=size,
            is_binary=True,
            lines=(),
            line_count=0,
            sha256_hex=digest,
        )
        return snapshot, ""

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        snapshot = FileSnapshot(
            relative_path=relative_path,
            kind=FileKind.BINARY,
            size_bytes=size,
            is_binary=True,
            lines=(),
            line_count=0,
            sha256_hex=digest,
        )
        return snapshot, ""

    raw_lines = text.splitlines()
    for line in raw_lines:
        if len(line) > policy.max_line_length:
            raise RepositoryScanError(RepositoryReason.LINE_LENGTH_EXCEEDED)

    kind = _classify_file(relative_path)
    excerpt: Tuple[str, ...] = tuple(raw_lines[:12])
    snapshot = FileSnapshot(
        relative_path=relative_path,
        kind=kind,
        size_bytes=size,
        is_binary=False,
        lines=excerpt,
        line_count=len(raw_lines),
        sha256_hex=digest,
    )
    return snapshot, text


# --- Indexing ---------------------------------------------------------------------------


def _index_python(
    file: FileSnapshot,
    content: str,
) -> Tuple[Tuple[SymbolRecord, ...], Tuple[ImportRecord, ...], Tuple[ReferenceRecord, ...], Tuple[RelationshipEdge, ...]]:
    symbols: list[SymbolRecord] = []
    imports: list[ImportRecord] = []
    references: list[ReferenceRecord] = []
    edges: list[RelationshipEdge] = []

    try:
        tree = _ast.parse(content)
    except SyntaxError:
        return tuple(symbols), tuple(imports), tuple(references), tuple(edges)

    is_test_file = file.relative_path.startswith("tests/") or Path(file.relative_path).name.startswith("test_")

    def call_name(node: _ast.AST) -> Optional[str]:
        if isinstance(node, _ast.Name):
            return node.id
        if isinstance(node, _ast.Attribute):
            parent = call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return None

    class Visitor(_ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[str] = []

        def source_id(self) -> str:
            return f"{file.relative_path}::{'.'.join(self.scope)}" if self.scope else file.relative_path

        def add_definition(self, node: _ast.AST, name: str, kind: str) -> None:
            qualified = ".".join((*self.scope, name)) if self.scope else name
            parent = ".".join(self.scope) or None
            symbols.append(SymbolRecord(
                name=qualified,
                kind=kind,
                relative_path=file.relative_path,
                line_start=node.lineno,
                line_end=getattr(node, "end_lineno", node.lineno),
                parent=parent,
            ))
            edges.append(RelationshipEdge(
                source_id=self.source_id(),
                target_id=f"{file.relative_path}::{qualified}",
                edge_type=RelationshipType.DEFINES,
                provenance=f"{file.relative_path}:{node.lineno}",
            ))

        def visit_ClassDef(self, node: _ast.ClassDef) -> None:
            self.add_definition(node, node.name, "class")
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node: _ast.FunctionDef) -> None:
            self.add_definition(node, node.name, "method" if self.scope else "function")
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def add_import(self, module: Optional[str], name: str, is_from: bool, line: int) -> None:
            imports.append(ImportRecord(module, name, is_from, file.relative_path, line))
            target = f"{module}.{name}" if module else name
            edges.append(RelationshipEdge(
                file.relative_path, target, RelationshipType.IMPORTS,
                f"{file.relative_path}:{line}",
            ))
            if is_test_file:
                edges.append(RelationshipEdge(
                    file.relative_path, target, RelationshipType.TESTS,
                    f"{file.relative_path}:{line}",
                ))

        def visit_Import(self, node: _ast.Import) -> None:
            for alias in node.names:
                self.add_import(None, alias.name, False, node.lineno)

        def visit_ImportFrom(self, node: _ast.ImportFrom) -> None:
            module = node.module or ""
            for alias in node.names:
                self.add_import(module, alias.name, True, node.lineno)

        def visit_Call(self, node: _ast.Call) -> None:
            target = call_name(node.func)
            if target:
                source = ".".join(self.scope) or file.relative_path
                references.append(ReferenceRecord(
                    source, target, RelationshipType.CALLS,
                    file.relative_path, node.lineno,
                ))
                edges.append(RelationshipEdge(
                    self.source_id(), target, RelationshipType.CALLS,
                    f"{file.relative_path}:{node.lineno}",
                ))
            self.generic_visit(node)

    Visitor().visit(tree)

    return tuple(symbols), tuple(imports), tuple(references), tuple(edges)


def _index_markdown(file: FileSnapshot, content: str) -> Tuple[Tuple[SymbolRecord, ...], Tuple[RelationshipEdge, ...]]:
    import re

    symbols: list[SymbolRecord] = []
    edges: list[RelationshipEdge] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()[:120]
            name = heading.replace(" ", "_")[:120] or f"heading_{line_no}"
            symbols.append(
                SymbolRecord(
                    name=name,
                    kind="heading",
                    relative_path=file.relative_path,
                    line_start=line_no,
                    line_end=line_no,
                )
            )
            edges.append(
                RelationshipEdge(
                    source_id=file.relative_path,
                    target_id=f"{file.relative_path}::{name}",
                    edge_type=RelationshipType.DOCUMENTS,
                    provenance=f"{file.relative_path}:{line_no}",
                )
            )
        for match in re.finditer(r"\[[^\]]{1,200}\]\(([^)\s]{1,255})\)", line):
            target = match.group(1).split("#", 1)[0]
            if (
                target
                and "://" not in target
                and not target.startswith(("/", "~"))
                and ".." not in Path(target).parts
                and not _contains_control_chars(target)
            ):
                edges.append(RelationshipEdge(
                    source_id=file.relative_path,
                    target_id=target,
                    edge_type=RelationshipType.DOCUMENTS,
                    provenance=f"{file.relative_path}:{line_no}",
                ))
    return tuple(symbols), tuple(edges)


def _index_jsts(file: FileSnapshot, content: str) -> Tuple[Tuple[SymbolRecord, ...], Tuple[RelationshipEdge, ...]]:
    """Conservative, limited JS/TS declaration extraction.

    This is intentionally not a full parser. It extracts only simple top-level
    declarations that match a small set of safe regular expressions.
    """
    import re

    symbols: list[SymbolRecord] = []
    edges: list[RelationshipEdge] = []

    class_pattern = re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)\b")
    function_pattern = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?:\*)?\s*([A-Za-z_$][A-Za-z0-9_$]*)\b")
    const_pattern = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\b")

    for line_no, line in enumerate(content.splitlines(), start=1):
        for pattern, kind in (
            (class_pattern, "class"),
            (function_pattern, "function"),
            (const_pattern, "constant"),
        ):
            match = pattern.match(line)
            if match:
                name = match.group(1)
                symbols.append(
                    SymbolRecord(
                        name=name,
                        kind=kind,
                        relative_path=file.relative_path,
                        line_start=line_no,
                        line_end=line_no,
                    )
                )
                edges.append(
                    RelationshipEdge(
                        source_id=file.relative_path,
                        target_id=f"{file.relative_path}::{name}",
                        edge_type=RelationshipType.DEFINES,
                        provenance=f"{file.relative_path}:{line_no}",
                    )
                )
                break

    return tuple(symbols), tuple(edges)


def _index_file(
    file: FileSnapshot,
    content: str,
) -> Tuple[Tuple[SymbolRecord, ...], Tuple[ImportRecord, ...], Tuple[ReferenceRecord, ...], Tuple[RelationshipEdge, ...]]:
    symbols: list[SymbolRecord] = []
    imports: list[ImportRecord] = []
    references: list[ReferenceRecord] = []
    edges: list[RelationshipEdge] = []

    if file.is_binary:
        return tuple(symbols), tuple(imports), tuple(references), tuple(edges)

    if file.kind in (FileKind.PYTHON,):
        s, i, r, e = _index_python(file, content)
        symbols.extend(s)
        imports.extend(i)
        references.extend(r)
        edges.extend(e)
    elif file.kind in (FileKind.JAVASCRIPT, FileKind.TYPESCRIPT):
        s, e = _index_jsts(file, content)
        symbols.extend(s)
        edges.extend(e)
    elif file.kind in (FileKind.MARKDOWN,):
        s, e = _index_markdown(file, content)
        symbols.extend(s)
        edges.extend(e)

    return tuple(symbols), tuple(imports), tuple(references), tuple(edges)


# --- Public API -------------------------------------------------------------------------


def scan_repository(root: RepositoryRoot, policy: RepositoryPolicy) -> RepositoryIndex:
    """Walk ``root`` according to ``policy`` and return a deterministic index.

    Raises ``RepositoryScanError`` when a hard limit is exceeded.
    """
    if not isinstance(root, RepositoryRoot):
        raise RepositoryScanError(RepositoryReason.INVALID_PATH)
    if not isinstance(policy, RepositoryPolicy):
        raise RepositoryScanError(RepositoryReason.MALFORMED_REQUEST)

    files: list[FileSnapshot] = []
    symbols: list[SymbolRecord] = []
    imports: list[ImportRecord] = []
    references: list[ReferenceRecord] = []
    edges: list[RelationshipEdge] = []
    total_bytes = 0

    try:
        entries = sorted(root.path.rglob("*"))
    except OSError as exc:
        raise RepositoryScanError(RepositoryReason.INVALID_PATH) from exc

    for entry in entries:
        if entry.is_symlink():
            if not policy.follow_symlinks:
                continue
            try:
                target = entry.resolve()
            except OSError:
                continue
            if os.path.commonpath([str(root.path.resolve()), str(target)]) != str(root.path.resolve()):
                continue
        if not entry.is_file():
            continue

        try:
            relative_path = entry.relative_to(root.path).as_posix()
        except ValueError:
            continue

        if _is_excluded(entry, relative_path, policy):
            continue

        if len(files) >= policy.max_files:
            raise RepositoryScanError(
                RepositoryReason.TOO_MANY_FILES,
                partial=_build_index(files, symbols, imports, references, edges, total_bytes),
            )

        total_bytes += entry.stat().st_size
        if total_bytes > policy.max_total_bytes:
            raise RepositoryScanError(
                RepositoryReason.TOTAL_SIZE_EXCEEDED,
                partial=_build_index(files, symbols, imports, references, edges, total_bytes),
            )

        try:
            result = _read_file_content(root, entry, policy, relative_path=relative_path)
        except RepositoryScanError:
            raise
        except Exception:
            continue

        if result is None:
            continue

        snapshot, content = result
        files.append(snapshot)

        if not snapshot.is_binary:
            s, i, r, e = _index_file(snapshot, content)
            symbols.extend(s)
            imports.extend(i)
            references.extend(r)
            edges.extend(e)

    return _build_index(files, symbols, imports, references, edges, total_bytes)


def _build_index(
    files: list[FileSnapshot],
    symbols: list[SymbolRecord],
    imports: list[ImportRecord],
    references: list[ReferenceRecord],
    edges: list[RelationshipEdge],
    total_bytes: int,
) -> RepositoryIndex:
    return RepositoryIndex(
        files=tuple(sorted(files, key=lambda f: f.relative_path)),
        symbols=tuple(symbols),
        imports=tuple(imports),
        references=tuple(references),
        edges=tuple(edges),
        total_bytes=total_bytes,
    )
