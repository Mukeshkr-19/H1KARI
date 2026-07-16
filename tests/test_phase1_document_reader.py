"""Focused checks for the bounded Phase 1 local text reader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.document_reader import DocumentReadError, MAX_DOCUMENT_BYTES, read_text_document


def _assert_code(path, code: str) -> None:
    with pytest.raises(DocumentReadError) as exc_info:
        read_text_document(path)
    assert exc_info.value.code == code


def test_reads_exact_utf8_text_file_and_returns_canonical_path(tmp_path):
    selected = tmp_path / "note.txt"
    selected.write_text("Hello, HIKARI.\n", encoding="utf-8")

    document = read_text_document(selected)

    assert document.canonical_path == selected.resolve()
    assert document.text == "Hello, HIKARI.\n"
    assert document.size_bytes == len("Hello, HIKARI.\n".encode())


def test_rejects_missing_path(tmp_path):
    _assert_code(tmp_path / "missing.txt", "missing")


def test_rejects_embedded_nul_as_content_free_invalid_path():
    private_detail = "private-document-name"

    with pytest.raises(DocumentReadError) as exc_info:
        read_text_document(f"/tmp/{private_detail}\x00.txt")

    assert exc_info.value.code == "invalid_path"
    assert private_detail not in str(exc_info.value)
    assert "\x00" not in str(exc_info.value)


def test_normalizes_value_error_while_resolving_path(tmp_path, monkeypatch):
    selected = tmp_path / "resolve.txt"
    selected.write_text("safe", encoding="utf-8")

    def malformed_resolve(*_args, **_kwargs):
        raise ValueError("private resolution detail")

    monkeypatch.setattr(Path, "resolve", malformed_resolve)
    with pytest.raises(DocumentReadError) as exc_info:
        read_text_document(selected)

    assert exc_info.value.code == "invalid_path"
    assert "private resolution detail" not in str(exc_info.value)


def test_normalizes_value_error_while_opening_path(tmp_path, monkeypatch):
    selected = tmp_path / "open.txt"
    selected.write_text("safe", encoding="utf-8")

    def malformed_open(*_args, **_kwargs):
        raise ValueError("private open detail")

    monkeypatch.setattr(os, "open", malformed_open)
    with pytest.raises(DocumentReadError) as exc_info:
        read_text_document(selected)

    assert exc_info.value.code == "invalid_path"
    assert "private open detail" not in str(exc_info.value)


def test_normalizes_value_error_during_final_path_validation(tmp_path, monkeypatch):
    selected = tmp_path / "final.txt"
    selected.write_text("safe", encoding="utf-8")
    original_resolve = Path.resolve
    calls = 0

    def malformed_final_resolve(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("private final detail")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", malformed_final_resolve)
    with pytest.raises(DocumentReadError) as exc_info:
        read_text_document(selected)

    assert exc_info.value.code == "invalid_path"
    assert "private final detail" not in str(exc_info.value)


def test_rejects_non_regular_path(tmp_path):
    _assert_code(tmp_path, "not_regular")


def test_rejects_symbolic_link(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("private", encoding="utf-8")
    link = tmp_path / "selected.txt"
    link.symlink_to(target)

    _assert_code(link, "symlink")


def test_rejects_symbolic_link_in_parent_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "selected.txt").write_text("private", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    _assert_code(linked / "selected.txt", "symlink")


@pytest.mark.parametrize("name", ["note.md", "note.pdf", "note.docx"])
def test_rejects_unsupported_document_types(tmp_path, name):
    selected = tmp_path / name
    selected.write_text("text", encoding="utf-8")

    _assert_code(selected, "unsupported_type")


def test_rejects_oversized_document(tmp_path):
    selected = tmp_path / "large.txt"
    selected.write_bytes(b"x" * (MAX_DOCUMENT_BYTES + 1))

    _assert_code(selected, "too_large")


def test_rejects_invalid_utf8_without_returning_content(tmp_path):
    selected = tmp_path / "binary.txt"
    selected.write_bytes(b"valid-prefix\xffprivate-tail")

    with pytest.raises(DocumentReadError) as exc_info:
        read_text_document(selected)

    assert exc_info.value.code == "invalid_utf8"
    assert "private-tail" not in str(exc_info.value)


def test_rejects_permission_error(tmp_path, monkeypatch):
    selected = tmp_path / "private.txt"
    selected.write_text("private", encoding="utf-8")

    def deny_open(*_args, **_kwargs):
        raise PermissionError("private operating-system detail")

    monkeypatch.setattr(os, "open", deny_open)
    with pytest.raises(DocumentReadError) as exc_info:
        read_text_document(selected)

    assert exc_info.value.code == "permission"
    assert "operating-system detail" not in str(exc_info.value)


def test_rejects_document_changed_during_read(tmp_path, monkeypatch):
    selected = tmp_path / "changing.txt"
    selected.write_text("before", encoding="utf-8")
    real_read = os.read
    changed = False

    def changing_read(descriptor: int, count: int) -> bytes:
        nonlocal changed
        chunk = real_read(descriptor, count)
        if not changed:
            changed = True
            selected.write_text("after-change", encoding="utf-8")
        return chunk

    monkeypatch.setattr(os, "read", changing_read)
    _assert_code(selected, "changed")


def test_custom_limit_is_enforced(tmp_path):
    selected = tmp_path / "small.txt"
    selected.write_bytes(b"1234")

    with pytest.raises(DocumentReadError) as exc_info:
        read_text_document(selected, max_bytes=3)

    assert exc_info.value.code == "too_large"
