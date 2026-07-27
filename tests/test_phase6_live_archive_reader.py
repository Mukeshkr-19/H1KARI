"""Tests for the live safe archive metadata reader.

Uses synthetic in-memory archives only.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from core.phase6_adapters.skill_staging import SkillStagingAdapterConfig
from core.phase6_live.skill_staging import LiveArchiveEntryReader


def _reader() -> LiveArchiveEntryReader:
    return LiveArchiveEntryReader(SkillStagingAdapterConfig())


def test_empty_archive() -> None:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w"):
        pass
    entries = _reader().read_entries(b.getvalue())
    assert entries == ()


def test_simple_zip_reading() -> None:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("skill.py", b"print('ok')")
    entries = _reader().read_entries(b.getvalue())
    assert len(entries) == 1
    assert entries[0].normalized_path == "skill.py"
    assert entries[0].kind.value == "regular"
    assert entries[0].content == b"print('ok')"


def test_simple_tar_reading() -> None:
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w") as tf:
        data = b"print('ok')"
        info = tarfile.TarInfo(name="skill.py")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    entries = _reader().read_entries(b.getvalue())
    assert len(entries) == 1
    assert entries[0].normalized_path == "skill.py"
    assert entries[0].content == b"print('ok')"


def test_rejects_traversal_zip() -> None:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("../escape.txt", b"bad")
    with pytest.raises(ValueError):
        _reader().read_entries(b.getvalue())


def test_rejects_absolute_path_tar() -> None:
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w") as tf:
        data = b"bad"
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError):
        _reader().read_entries(b.getvalue())


def test_rejects_nul_byte() -> None:
    # Archive formats truncate NUL in filenames, so test the path normalizer directly.
    with pytest.raises(ValueError):
        _reader()._normalize_path("ski\x00ll.py")


def test_rejects_symlink_tar() -> None:
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w") as tf:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        tf.addfile(info)
    with pytest.raises(ValueError):
        _reader().read_entries(b.getvalue())


def test_rejects_hardlink_tar() -> None:
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w") as tf:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.LNKTYPE
        info.linkname = "target"
        tf.addfile(info)
    with pytest.raises(ValueError):
        _reader().read_entries(b.getvalue())


def test_rejects_excessive_nesting() -> None:
    b = io.BytesIO()
    config = SkillStagingAdapterConfig(max_path_depth=2)
    reader = LiveArchiveEntryReader(config)
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("a/b/c/d/skill.py", b"bad")
    with pytest.raises(ValueError):
        reader.read_entries(b.getvalue())


def test_total_size_bound() -> None:
    b = io.BytesIO()
    config = SkillStagingAdapterConfig(max_total_bytes=50, max_file_bytes=50)
    reader = LiveArchiveEntryReader(config)
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("skill.py", b"xx")
    with pytest.raises(ValueError):
        reader.read_entries(b.getvalue())


def test_cancellation_not_needed_for_small_archives() -> None:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("skill.py", b"x")
    entries = _reader().read_entries(b.getvalue())
    assert entries[0].content == b"x"


def test_reader_repr_content_free() -> None:
    assert "LiveArchiveEntryReader()" in repr(_reader())


def test_aggregate_size_bound_enforced() -> None:
    b = io.BytesIO()
    config = SkillStagingAdapterConfig(max_total_bytes=10, max_file_bytes=10)
    reader = LiveArchiveEntryReader(config)
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("a.txt", b"1234567890")  # Exactly 10 bytes.
        zf.writestr("b.txt", b"x")  # Would exceed aggregate.
    with pytest.raises(ValueError):
        reader.read_entries(b.getvalue())


def test_compression_ratio_bound_rejects_bomb(tmp_path: Path) -> None:
    b = io.BytesIO()
    config = SkillStagingAdapterConfig(max_compression_ratio=2.0, max_total_bytes=1_000_000, max_file_bytes=1_000_000)
    reader = LiveArchiveEntryReader(config)
    # ZIP stores deflate; highly compressible data expands ratio.
    with zipfile.ZipFile(b, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge.txt", "0" * 1_000_000)
    with pytest.raises(ValueError):
        reader.read_entries(b.getvalue())


def test_cancellation_during_enumeration() -> None:
    b = io.BytesIO()
    cancelled = [False]

    def is_cancelled() -> bool:
        cancelled[0] = True
        return True

    reader = LiveArchiveEntryReader(SkillStagingAdapterConfig())
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("skill.py", b"x")
    with pytest.raises(ValueError):
        reader.read_entries(b.getvalue(), _cancelled=is_cancelled)
    assert cancelled[0]


def test_no_extraction_to_filesystem(tmp_path: Path) -> None:
    b = io.BytesIO()
    target = tmp_path / "extracted"
    reader = LiveArchiveEntryReader(SkillStagingAdapterConfig())
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("file.txt", b"data")
    entries = reader.read_entries(b.getvalue())
    assert len(entries) == 1
    assert not target.exists()
