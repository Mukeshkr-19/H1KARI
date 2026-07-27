"""Safe archive metadata reader backend.

Uses only the Python standard library ``tarfile`` and ``zipfile`` to enumerate
archive entry metadata without extracting files.  Rejects symlinks, hardlinks,
device nodes, absolute paths, traversal, NUL bytes, Unicode confusables, case
collisions, excessive nesting, compression bombs and executable magic.

This backend is disabled by default; it does nothing at import time and only
reads archive bytes when ``read_entries`` is called with an explicit byte
buffer.
"""

from __future__ import annotations

import io
import stat
import tarfile
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from core.phase6_adapters.skill_staging import (
    ArchiveEntry,
    ArchiveEntryKind,
    ArchiveEntryReaderInterface,
    SkillStagingAdapterConfig,
)


class _WrongArchiveFormat(Exception):
    """Internal signal to try the next archive format."""

    pass


class _ArchiveRejected(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __str__(self) -> str:
        return f"_ArchiveRejected({self.reason})"


@dataclass(frozen=True)
class _AcceptedEntry:
    """Metadata collected during pass 1 for entries that may need content."""

    path: str
    kind: ArchiveEntryKind
    uncompressed_size: int
    compressed_size: int
    mode: int
    link_target: Optional[str]
    zip_info: Optional[zipfile.ZipInfo] = None
    tar_info: Optional[tarfile.TarInfo] = None


class LiveArchiveEntryReader(ArchiveEntryReaderInterface):
    """Production archive reader using stdlib tarfile and zipfile.

    Safety notes:
    - ``zipfile.ZipFile.infolist()`` pre-materialises the central directory,
      so the input archive byte cap is also the memory bound for ZIP metadata.
    - Members are never extracted to the filesystem; full content is read into
      memory only because the ``ArchiveEntryReaderInterface`` contract requires
      it.
    - Two-pass processing: pass 1 validates all metadata, pass 2 streams content.
    """

    def __init__(self, config: SkillStagingAdapterConfig) -> None:
        if not isinstance(config, SkillStagingAdapterConfig):
            raise ValueError("invalid SkillStagingAdapterConfig")
        self._config = config

    def read_entries(
        self,
        archive_bytes: bytes,
        *,
        _cancelled: Optional[Callable[[], bool]] = None,
    ) -> Tuple[ArchiveEntry, ...]:
        if not isinstance(archive_bytes, bytes):
            return ()
        if len(archive_bytes) > self._config.max_total_bytes:
            raise ValueError("archive_total_size_exceeded")
        # Try zip first, then tar.
        try:
            return self._read_zip(archive_bytes, _cancelled=_cancelled)
        except _WrongArchiveFormat:
            pass
        try:
            return self._read_tar(archive_bytes, _cancelled=_cancelled)
        except _WrongArchiveFormat:
            raise ValueError("unsupported_archive_format") from None

    def _check_cancel(self, _cancelled: Optional[Callable[[], bool]]) -> None:
        if _cancelled is not None and _cancelled():
            raise ValueError("cancelled")

    def _read_zip(
        self,
        archive_bytes: bytes,
        *,
        _cancelled: Optional[Callable[[], bool]] = None,
    ) -> Tuple[ArchiveEntry, ...]:
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as zf:
                accepted, aggregate_uncompressed = self._enumerate_zip(
                    zf, _cancelled=_cancelled
                )
                return self._read_zip_content(
                    zf, accepted, aggregate_uncompressed, _cancelled=_cancelled
                )
        except zipfile.BadZipFile as exc:
            raise _WrongArchiveFormat() from exc

    def _enumerate_zip(
        self,
        zf: zipfile.ZipFile,
        *,
        _cancelled: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[_AcceptedEntry], int]:
        accepted: list[_AcceptedEntry] = []
        seen: set[str] = set()
        seen_lower: set[str] = set()
        seen_nfkc: set[str] = set()
        aggregate = 0
        count = 0

        for info in zf.infolist():
            self._check_cancel(_cancelled)
            count += 1
            if count > self._config.max_files:
                raise ValueError("file_count_exceeded")

            kind = self._classify_zip(info)
            if kind in (ArchiveEntryKind.SYMLINK, ArchiveEntryKind.HARDLINK, ArchiveEntryKind.DEVICE, ArchiveEntryKind.OTHER):
                raise ValueError(f"{kind.value}_rejected")

            # Reject encrypted or unsupported members.
            if info.flag_bits & 0x1:
                raise ValueError("encrypted_entry_rejected")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
                raise ValueError("unsupported_compression")

            path = self._normalize_path(info.filename)

            if kind is ArchiveEntryKind.REGULAR and info.file_size > self._config.max_file_bytes:
                raise ValueError("size_exceeded")

            aggregate += info.file_size
            if aggregate > self._config.max_total_bytes:
                raise ValueError("archive_total_size_exceeded")

            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > self._config.max_compression_ratio:
                    raise ValueError("compression_bomb")
            elif info.file_size > 0:
                raise ValueError("compression_bomb")

            self._register_path(path, seen, seen_lower, seen_nfkc)
            self._check_prefix_conflicts(accepted, path, kind)

            accepted.append(
                _AcceptedEntry(
                    path=path,
                    kind=kind,
                    uncompressed_size=info.file_size,
                    compressed_size=info.compress_size,
                    mode=0o644,
                    link_target=None,
                    zip_info=info,
                )
            )

        if len(accepted) > self._config.max_files:
            raise ValueError("file_count_exceeded")
        return accepted, aggregate

    def _read_zip_content(
        self,
        zf: zipfile.ZipFile,
        accepted: List[_AcceptedEntry],
        aggregate_uncompressed: int,
        *,
        _cancelled: Optional[Callable[[], bool]] = None,
    ) -> Tuple[ArchiveEntry, ...]:
        entries: list[ArchiveEntry] = []
        aggregate_read = 0
        for entry in accepted:
            self._check_cancel(_cancelled)
            if entry.kind is not ArchiveEntryKind.REGULAR:
                entries.append(
                    ArchiveEntry(
                        normalized_path=entry.path,
                        kind=entry.kind,
                        content=None,
                        uncompressed_size=entry.uncompressed_size,
                        compressed_size=entry.compressed_size,
                        mode=entry.mode,
                        executable=False,
                        link_target=None,
                    )
                )
                continue

            info = entry.zip_info
            assert info is not None
            aggregate = [aggregate_read]
            content = self._read_zipped_content(zf, info, _cancelled=_cancelled, _aggregate_read=aggregate)
            aggregate_read = aggregate[0]
            if len(content) != info.file_size:
                raise ValueError("size_mismatch")
            if aggregate_read > self._config.max_total_bytes:
                raise ValueError("archive_total_size_exceeded")

            entries.append(
                ArchiveEntry(
                    normalized_path=entry.path,
                    kind=entry.kind,
                    content=content,
                    uncompressed_size=entry.uncompressed_size,
                    compressed_size=entry.compressed_size,
                    mode=entry.mode,
                    executable=self._is_executable(entry.path, content),
                    link_target=None,
                )
            )
        # Defense in depth: pass1 aggregate should already enforce this.
        if aggregate_read > self._config.max_total_bytes:
            raise ValueError("archive_total_size_exceeded")
        return tuple(entries)

    def _read_tar(
        self,
        archive_bytes: bytes,
        *,
        _cancelled: Optional[Callable[[], bool]] = None,
    ) -> Tuple[ArchiveEntry, ...]:
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as tf:
                accepted, aggregate_uncompressed = self._enumerate_tar(
                    tf, len(archive_bytes), _cancelled=_cancelled
                )
                return self._read_tar_content(
                    tf, accepted, aggregate_uncompressed, _cancelled=_cancelled
                )
        except tarfile.TarError as exc:
            raise _WrongArchiveFormat() from exc

    def _enumerate_tar(
        self,
        tf: tarfile.TarFile,
        archive_size: int,
        *,
        _cancelled: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[_AcceptedEntry], int]:
        accepted: list[_AcceptedEntry] = []
        seen: set[str] = set()
        seen_lower: set[str] = set()
        seen_nfkc: set[str] = set()
        aggregate = 0
        count = 0

        for member in tf:
            self._check_cancel(_cancelled)
            count += 1
            if count > self._config.max_files:
                raise ValueError("file_count_exceeded")

            kind = self._classify_tar(member)
            if kind in (ArchiveEntryKind.SYMLINK, ArchiveEntryKind.HARDLINK, ArchiveEntryKind.DEVICE, ArchiveEntryKind.OTHER):
                raise ValueError(f"{kind.value}_rejected")

            path = self._normalize_path(member.name)

            if kind is ArchiveEntryKind.REGULAR and member.size > self._config.max_file_bytes:
                raise ValueError("size_exceeded")

            aggregate += member.size
            if aggregate > self._config.max_total_bytes:
                raise ValueError("archive_total_size_exceeded")

            self._register_path(path, seen, seen_lower, seen_nfkc)
            self._check_prefix_conflicts(accepted, path, kind)

            accepted.append(
                _AcceptedEntry(
                    path=path,
                    kind=kind,
                    uncompressed_size=member.size,
                    compressed_size=0,
                    mode=member.mode & 0o7777,
                    link_target=member.linkname if member.islnk() or member.issym() else None,
                    tar_info=member,
                )
            )

        if len(accepted) > self._config.max_files:
            raise ValueError("file_count_exceeded")

        # Conservative aggregate expansion ratio against the whole archive input.
        if archive_size > 0 and aggregate > 0:
            ratio = aggregate / archive_size
            if ratio > self._config.max_compression_ratio:
                raise ValueError("compression_bomb")

        return accepted, aggregate

    def _read_tar_content(
        self,
        tf: tarfile.TarFile,
        accepted: List[_AcceptedEntry],
        aggregate_uncompressed: int,
        *,
        _cancelled: Optional[Callable[[], bool]] = None,
    ) -> Tuple[ArchiveEntry, ...]:
        entries: list[ArchiveEntry] = []
        aggregate_read = 0
        for entry in accepted:
            self._check_cancel(_cancelled)
            if entry.kind is not ArchiveEntryKind.REGULAR:
                entries.append(
                    ArchiveEntry(
                        normalized_path=entry.path,
                        kind=entry.kind,
                        content=None,
                        uncompressed_size=entry.uncompressed_size,
                        compressed_size=0,
                        mode=entry.mode,
                        executable=False,
                        link_target=entry.link_target,
                    )
                )
                continue

            member = entry.tar_info
            assert member is not None
            aggregate = [aggregate_read]
            content = self._read_tar_member(tf, member, _cancelled=_cancelled, _aggregate_read=aggregate)
            # _stream_read already advanced the aggregate; do not double-count.
            aggregate_read = aggregate[0]
            if len(content) != member.size:
                raise ValueError("size_mismatch")
            if aggregate_read > self._config.max_total_bytes:
                raise ValueError("archive_total_size_exceeded")

            entries.append(
                ArchiveEntry(
                    normalized_path=entry.path,
                    kind=entry.kind,
                    content=content,
                    uncompressed_size=entry.uncompressed_size,
                    compressed_size=0,
                    mode=entry.mode,
                    executable=self._is_executable(entry.path, content),
                    link_target=None,
                )
            )
        return tuple(entries)

    def _read_zipped_content(
        self,
        zf: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        *,
        _cancelled: Optional[Callable[[], bool]] = None,
        _aggregate_read: Optional[list[int]] = None,
    ) -> bytes:
        with zf.open(info) as f:
            return self._stream_read(f, info.file_size, _cancelled=_cancelled, aggregate_read=_aggregate_read)

    def _read_tar_member(
        self,
        tf: tarfile.TarFile,
        member: tarfile.TarInfo,
        *,
        _cancelled: Optional[Callable[[], bool]] = None,
        _aggregate_read: Optional[list[int]] = None,
    ) -> bytes:
        f = tf.extractfile(member)
        if f is None:
            raise ValueError("tar_content_unavailable")
        return self._stream_read(f, member.size, _cancelled=_cancelled, aggregate_read=_aggregate_read)

    def _stream_read(
        self,
        f,
        total_size: int,
        *,
        _cancelled: Optional[Callable[[], bool]] = None,
        chunk_size: int = 8192,
        aggregate_read: Optional[list[int]] = None,
    ) -> bytes:
        buffer = bytearray()
        remaining = total_size
        while remaining > 0:
            self._check_cancel(_cancelled)
            to_read = min(chunk_size, remaining)
            chunk = f.read(to_read)
            if not chunk:
                break
            buffer.extend(chunk)
            remaining -= len(chunk)
            if len(buffer) > total_size:
                raise ValueError("size_mismatch")
            if aggregate_read is not None:
                aggregate_read[0] += len(chunk)
                if aggregate_read[0] > self._config.max_total_bytes:
                    raise ValueError("archive_total_size_exceeded")
        return bytes(buffer)

    def _normalize_path(self, path: str) -> str:
        if not isinstance(path, str):
            raise ValueError("path_must_be_string")
        if "\x00" in path:
            raise ValueError("nul_byte_in_path")
        # Reject control characters.
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
            raise ValueError("control_chars_in_path")
        # Unicode normalization/confusables.
        try:
            normalized = unicodedata.normalize("NFC", path)
        except Exception as exc:
            raise ValueError("unicode_normalize_error") from exc
        if path != normalized:
            raise ValueError("unicode_confusable")
        if any(unicodedata.combining(ch) > 0 for ch in path):
            raise ValueError("unicode_confusable")
        # Reject bidirectional and format characters that can spoof paths.
        bidi_cats = {"RLE", "LRE", "RLO", "LRO", "PDF", "RLM", "LRM", "ALM", "LRI", "RLI", "FSI", "PDI"}
        if any(unicodedata.bidirectional(ch) in bidi_cats for ch in path):
            raise ValueError("bidi_control_in_path")
        if any(unicodedata.category(ch) == "Cf" for ch in path):
            raise ValueError("format_char_in_path")
        # Absolute path rejection.
        if path.startswith("/") or path.startswith("\\") or (len(path) > 2 and path[1] == ":"):
            raise ValueError("absolute_path")
        # Traversal.
        parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
        if ".." in parts:
            raise ValueError("path_traversal")
        if len(parts) > self._config.max_path_depth:
            raise ValueError("excessive_nesting")
        if not parts:
            raise ValueError("empty_path")
        return "/".join(parts)

    def _register_path(
        self,
        path: str,
        seen: set[str],
        seen_lower: set[str],
        seen_nfkc: set[str],
    ) -> None:
        if path in seen:
            raise ValueError("case_collision")
        seen.add(path)
        lower = path.lower()
        if lower in seen_lower:
            raise ValueError("case_collision")
        seen_lower.add(lower)
        nfkc = unicodedata.normalize("NFKC", path)
        if nfkc in seen_nfkc:
            raise ValueError("unicode_confusable")
        seen_nfkc.add(nfkc)

    def _check_prefix_conflicts(
        self,
        accepted: List[_AcceptedEntry],
        path: str,
        kind: ArchiveEntryKind,
    ) -> None:
        # A file cannot share a prefix with an existing directory and vice versa.
        for entry in accepted:
            existing = entry.path
            if path == existing:
                continue
            if kind is ArchiveEntryKind.DIRECTORY:
                if entry.kind is not ArchiveEntryKind.DIRECTORY and (path == existing or existing.startswith(path + "/")):
                    raise ValueError("path_prefix_conflict")
            elif entry.kind is ArchiveEntryKind.DIRECTORY and (path.startswith(existing + "/")):
                raise ValueError("path_prefix_conflict")

    @staticmethod
    def _classify_zip(info: zipfile.ZipInfo) -> ArchiveEntryKind:
        # ZIP external_attr high 16 bits are Unix mode.
        mode = (info.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            return ArchiveEntryKind.SYMLINK
        if stat.S_ISDIR(mode):
            return ArchiveEntryKind.DIRECTORY
        if stat.S_ISREG(mode) or mode == 0:
            return ArchiveEntryKind.REGULAR
        if stat.S_ISBLK(mode) or stat.S_ISCHR(mode):
            return ArchiveEntryKind.DEVICE
        return ArchiveEntryKind.OTHER

    @staticmethod
    def _classify_tar(member: tarfile.TarInfo) -> ArchiveEntryKind:
        if member.issym():
            return ArchiveEntryKind.SYMLINK
        if member.islnk():
            return ArchiveEntryKind.HARDLINK
        if member.isdir():
            return ArchiveEntryKind.DIRECTORY
        if member.isfile():
            return ArchiveEntryKind.REGULAR
        if member.isblk() or member.ischr():
            return ArchiveEntryKind.DEVICE
        return ArchiveEntryKind.OTHER

    @staticmethod
    def _is_executable(path: str, content: bytes | None) -> bool:
        """Bounded prefix inspection for executable magic."""
        if content is None or not content:
            return False
        prefix = content[:16]
        if prefix.startswith((b"\x7fELF", b"MZ", b"\x00asm", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")):
            return True
        if prefix.startswith(b"#!"):
            return True
        return False

    def __repr__(self) -> str:
        return "LiveArchiveEntryReader()"
