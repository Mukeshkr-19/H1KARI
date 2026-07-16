"""Bounded local reader for an already-authorized Phase 1 text document."""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final


MAX_DOCUMENT_BYTES: Final = 100_000
_READ_CHUNK_BYTES: Final = 64 * 1024


class DocumentReadError(ValueError):
    """A stable, content-free document validation or read failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TextDocument:
    canonical_path: Path
    text: str
    size_bytes: int


def read_text_document(
    path: str | os.PathLike[str],
    *,
    max_bytes: int = MAX_DOCUMENT_BYTES,
) -> TextDocument:
    """Read one selected UTF-8 ``.txt`` file after authorization."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")

    try:
        selected = Path(path).expanduser()
        if "\x00" in str(selected):
            raise ValueError
    except (TypeError, ValueError, RuntimeError) as exc:
        raise DocumentReadError(
            "invalid_path", "Selected document path is invalid"
        ) from exc
    _reject_symlink_components(selected)
    initial = _selected_stat(selected, missing_code="missing")
    if stat.S_ISLNK(initial.st_mode):
        raise DocumentReadError("symlink", "Symbolic links are not supported")
    if not stat.S_ISREG(initial.st_mode):
        raise DocumentReadError("not_regular", "Selected path is not a regular file")
    if selected.suffix.lower() != ".txt":
        raise DocumentReadError(
            "unsupported_type", "Only UTF-8 .txt documents are supported"
        )
    if initial.st_size > max_bytes:
        raise DocumentReadError("too_large", "Selected document exceeds the size limit")

    try:
        canonical = selected.resolve(strict=True)
    except FileNotFoundError as exc:
        raise DocumentReadError(
            "changed", "Selected document changed before it could be read"
        ) from exc
    except PermissionError as exc:
        raise DocumentReadError(
            "permission", "Permission denied while resolving the selected document"
        ) from exc
    except ValueError as exc:
        raise DocumentReadError(
            "invalid_path", "Selected document path is invalid"
        ) from exc
    except OSError as exc:
        raise DocumentReadError(
            "invalid_path", "Selected document path could not be resolved"
        ) from exc

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(selected, flags)
    except FileNotFoundError as exc:
        raise DocumentReadError(
            "changed", "Selected document changed before it could be read"
        ) from exc
    except PermissionError as exc:
        raise DocumentReadError(
            "permission", "Permission denied while opening the selected document"
        ) from exc
    except ValueError as exc:
        raise DocumentReadError(
            "invalid_path", "Selected document path is invalid"
        ) from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise DocumentReadError("symlink", "Symbolic links are not supported") from exc
        raise DocumentReadError("read_failed", "Selected document could not be opened") from exc

    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise DocumentReadError("not_regular", "Selected path is not a regular file")
        if _identity(initial) != _identity(opened):
            raise DocumentReadError(
                "changed", "Selected document changed before it could be read"
            )
        if opened.st_size > max_bytes:
            raise DocumentReadError("too_large", "Selected document exceeds the size limit")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise DocumentReadError("too_large", "Selected document exceeds the size limit")

        after = os.fstat(descriptor)
        if _snapshot(opened) != _snapshot(after) or total != after.st_size:
            raise DocumentReadError(
                "changed", "Selected document changed while it was being read"
            )

        final_selected = _selected_stat(selected, missing_code="changed")
        if stat.S_ISLNK(final_selected.st_mode) or _identity(
            final_selected
        ) != _identity(after):
            raise DocumentReadError(
                "changed", "Selected document changed while it was being read"
            )
        try:
            if selected.resolve(strict=True) != canonical:
                raise DocumentReadError(
                    "changed", "Selected document changed while it was being read"
                )
        except FileNotFoundError as exc:
            raise DocumentReadError(
                "changed", "Selected document changed while it was being read"
            ) from exc
        except ValueError as exc:
            raise DocumentReadError(
                "invalid_path", "Selected document path is invalid"
            ) from exc
        raw = b"".join(chunks)
    except PermissionError as exc:
        raise DocumentReadError("permission", "Permission denied while reading the selected document") from exc
    except OSError as exc:
        raise DocumentReadError("read_failed", "Selected document could not be read") from exc
    finally:
        os.close(descriptor)

    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DocumentReadError("invalid_utf8", "Selected document is not valid UTF-8") from exc
    return TextDocument(canonical_path=canonical, text=text, size_bytes=len(raw))


def _selected_stat(path: Path, *, missing_code: str) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        message = (
            "Selected document does not exist"
            if missing_code == "missing"
            else "Selected document changed while it was being read"
        )
        raise DocumentReadError(missing_code, message) from exc
    except PermissionError as exc:
        raise DocumentReadError(
            "permission", "Permission denied while inspecting the selected document"
        ) from exc
    except (OSError, ValueError) as exc:
        raise DocumentReadError("invalid_path", "Selected document path is invalid") from exc


def _reject_symlink_components(path: Path) -> None:
    """Reject a selection reached through any symbolic-link path component."""
    try:
        absolute = path if path.is_absolute() else Path.cwd() / path
        current = Path(absolute.anchor)
        for component in absolute.parts[1:-1]:
            current /= component
            if stat.S_ISLNK(current.lstat().st_mode):
                raise DocumentReadError("symlink", "Symbolic links are not supported")
    except DocumentReadError:
        raise
    except FileNotFoundError:
        return
    except PermissionError as exc:
        raise DocumentReadError(
            "permission", "Permission denied while inspecting the selected document"
        ) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise DocumentReadError(
            "invalid_path", "Selected document path is invalid"
        ) from exc


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _snapshot(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
