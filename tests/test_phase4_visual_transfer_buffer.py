"""Deterministic tests for the bounded in-memory visual-transfer buffer.

Covers: begin/receive flow, aggregate memory cap, one-active-per-handoff,
cross-session non-disclosure, idempotent terminal returns, cleanup on
complete/fail/cancel/expiry/disconnect, clock boundaries, buffer clearing,
and content-free repr/errors. No third-party deps; stdlib only.
"""

from __future__ import annotations

import struct

import pytest

from core.visual_transfer.buffer import VisualTransferBuffer, _PendingTransfer
from core.visual_transfer.contracts import (
    AGGREGATE_MEMORY_CAP_BYTES,
    ContractValidationError,
    TRANSFER_TTL_SECONDS,
    VisualTransferDeclaration,
    VisualTransferErrorCode,
    VisualTransferState,
)


# --- Helpers -----------------------------------------------------------------


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + (b"\x00" * 4)


def _png(width: int = 1, height: int = 1) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", b"") + _chunk(b"IEND", b"")


def _decl(
    frame: bytes,
    *,
    transfer_id: str = "t-1",
    handoff_id: str = "h-1",
    width: int = 1,
    height: int = 1,
    mime: str = "image/png",
) -> VisualTransferDeclaration:
    return VisualTransferDeclaration(
        transfer_id=transfer_id,
        handoff_id=handoff_id,
        mime=mime,
        declared_byte_length=len(frame),
        declared_width=width,
        declared_height=height,
        frame_count=1,
    )


class _Clock:
    """Controllable clock for deterministic TTL/clock-boundary tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _buffer(clock=None, *, cap: int = AGGREGATE_MEMORY_CAP_BYTES) -> VisualTransferBuffer:
    return VisualTransferBuffer(clock=clock or _Clock(), aggregate_cap_bytes=cap)


# --- Constructor / injection -------------------------------------------------


def test_buffer_rejects_non_callable_clock() -> None:
    with pytest.raises(TypeError):
        VisualTransferBuffer(clock="not callable")  # type: ignore[arg-type]


def test_buffer_rejects_non_int_cap() -> None:
    with pytest.raises(TypeError):
        VisualTransferBuffer(clock=_Clock(), aggregate_cap_bytes="8")  # type: ignore[arg-type]


def test_buffer_rejects_bool_cap() -> None:
    with pytest.raises(TypeError):
        VisualTransferBuffer(clock=_Clock(), aggregate_cap_bytes=True)  # type: ignore[arg-type]


def test_buffer_rejects_zero_cap() -> None:
    with pytest.raises(TypeError):
        VisualTransferBuffer(clock=_Clock(), aggregate_cap_bytes=0)


def test_buffer_rejects_negative_cap() -> None:
    with pytest.raises(TypeError):
        VisualTransferBuffer(clock=_Clock(), aggregate_cap_bytes=-1)


# --- begin / receive happy path ---------------------------------------------


def test_begin_returns_pending_and_indexes_transfer() -> None:
    buf = _buffer()
    frame = _png()
    state, error = buf.begin("scope-1", "t-1", _decl(frame))
    assert state is VisualTransferState.PENDING
    assert error is None
    assert buf.active_count() == 1


def test_receive_completes_and_clears_buffer() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    state, metadata, error = buf.receive("scope-1", "t-1", frame)
    assert state is VisualTransferState.COMPLETED
    assert error is None
    assert metadata is not None
    assert metadata.mime == "image/png"
    assert metadata.width == 1
    assert metadata.height == 1
    assert metadata.sha256.startswith("sha256.")
    # Buffer cleared on success; the pending record remains (for status) but
    # holds no bytes.
    assert buf.aggregate_bytes() == 0


def test_status_after_completion_returns_metadata() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    buf.receive("scope-1", "t-1", frame)
    state, metadata, error = buf.status("scope-1", "t-1")
    assert state is VisualTransferState.COMPLETED
    assert error is None
    assert metadata is not None


# --- Aggregate memory cap ---------------------------------------------------


def test_begin_rejects_when_declared_size_exceeds_cap() -> None:
    # Cap smaller than the smallest valid PNG; any declaration must fail.
    buf = _buffer(cap=10)
    frame = _png()
    state, error = buf.begin("scope-1", "t-1", _decl(frame))
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.RATE_LIMITED
    assert buf.active_count() == 0


def test_begin_rejects_when_aggregate_would_exceed_cap() -> None:
    # Two transfers whose combined declared sizes exceed the cap.
    frame = _png()
    cap = len(frame) * 2 - 1
    buf = _buffer(cap=cap)
    first = buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    second = buf.begin("scope-1", "t-2", _decl(frame, handoff_id="h-2"))
    assert first[0] is VisualTransferState.PENDING
    assert second[0] is VisualTransferState.FAILED
    assert second[1] is VisualTransferErrorCode.RATE_LIMITED
    assert buf.active_count() == 1


def test_aggregate_bytes_reflects_in_flight_declarations() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    assert buf.aggregate_bytes() == len(frame)
    buf.receive("scope-1", "t-1", frame)
    assert buf.aggregate_bytes() == 0


# --- One active transfer per exact scoped handoff ---------------------------


def test_begin_rejects_second_active_for_same_scoped_handoff() -> None:
    buf = _buffer()
    frame = _png()
    first = buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    second = buf.begin("scope-1", "t-2", _decl(frame, handoff_id="h-1"))
    assert first[0] is VisualTransferState.PENDING
    assert second[0] is VisualTransferState.FAILED
    assert second[1] is VisualTransferErrorCode.RATE_LIMITED
    assert buf.active_count() == 1


def test_begin_allows_same_handoff_across_different_scopes() -> None:
    buf = _buffer()
    frame = _png()
    a = buf.begin("scope-a", "t-1", _decl(frame, handoff_id="h-1"))
    b = buf.begin("scope-b", "t-2", _decl(frame, handoff_id="h-1"))
    assert a[0] is VisualTransferState.PENDING
    assert b[0] is VisualTransferState.PENDING
    assert buf.active_count() == 2


def test_begin_allows_new_transfer_for_handoff_after_completion() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    buf.receive("scope-1", "t-1", frame)
    # Handoff slot freed by completion; a new transfer for the same handoff
    # is accepted.
    state, error = buf.begin("scope-1", "t-2", _decl(frame, handoff_id="h-1"))
    assert state is VisualTransferState.PENDING
    assert error is None


def test_begin_idempotent_for_same_transfer_id_returns_current_state() -> None:
    buf = _buffer()
    frame = _png()
    decl = _decl(frame, handoff_id="h-1")
    first = buf.begin("scope-1", "t-1", decl)
    second = buf.begin("scope-1", "t-1", decl)
    assert first == second
    assert second[0] is VisualTransferState.PENDING
    assert buf.active_count() == 1


def test_begin_idempotent_re_begin_with_different_handoff_fails() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    state, error = buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-2"))
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.UNAVAILABLE


# --- Cross-session non-disclosure -------------------------------------------


def test_receive_unknown_transfer_id_returns_not_found() -> None:
    buf = _buffer()
    frame = _png()
    state, metadata, error = buf.receive("scope-1", "no-such", frame)
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert metadata is None


def test_status_cross_scope_returns_not_found() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    state, metadata, error = buf.status("scope-2", "t-1")
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert metadata is None
    # The real transfer is unaffected.
    assert buf.active_count() == 1


def test_cancel_cross_scope_returns_not_found() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    state, error = buf.cancel("scope-2", "t-1")
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert buf.active_count() == 1


def test_receive_cross_scope_returns_not_found() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    state, metadata, error = buf.receive("scope-2", "t-1", frame)
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert metadata is None
    assert buf.active_count() == 1


# --- Invalid scope / transfer_id --------------------------------------------


def test_begin_rejects_invalid_actor_scope() -> None:
    buf = _buffer()
    frame = _png()
    with pytest.raises(ContractValidationError):
        buf.begin("Bad Scope", "t-1", _decl(frame))


def test_begin_rejects_invalid_transfer_id() -> None:
    buf = _buffer()
    frame = _png()
    with pytest.raises(ContractValidationError):
        buf.begin("scope-1", "bad id", _decl(frame))


def test_receive_rejects_invalid_actor_scope() -> None:
    buf = _buffer()
    with pytest.raises(ContractValidationError):
        buf.receive("Bad Scope", "t-1", b"")


def test_status_rejects_invalid_actor_scope() -> None:
    buf = _buffer()
    with pytest.raises(ContractValidationError):
        buf.status("Bad Scope", "t-1")


def test_cancel_rejects_invalid_actor_scope() -> None:
    buf = _buffer()
    with pytest.raises(ContractValidationError):
        buf.cancel("Bad Scope", "t-1")


def test_clear_session_rejects_invalid_actor_scope() -> None:
    buf = _buffer()
    with pytest.raises(ContractValidationError):
        buf.clear_session("Bad Scope")


# --- receive failure modes ---------------------------------------------------


def test_receive_rejects_non_bytes_frame() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    state, metadata, error = buf.receive("scope-1", "t-1", "not bytes")
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.INVALID_REQUEST
    assert metadata is None
    assert buf.active_count() == 0
    assert buf.aggregate_bytes() == 0


def test_receive_rejects_bytearray_frame() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    state, metadata, error = buf.receive("scope-1", "t-1", bytearray(frame))
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.INVALID_REQUEST


def test_receive_rejects_size_mismatch() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    state, metadata, error = buf.receive("scope-1", "t-1", frame + b"extra")
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.SIZE_EXCEEDED
    assert buf.active_count() == 0
    assert buf.aggregate_bytes() == 0


def test_receive_rejects_truncated_frame() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    state, metadata, error = buf.receive("scope-1", "t-1", frame[:-1])
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.SIZE_EXCEEDED


def test_receive_rejects_malformed_image_bytes() -> None:
    buf = _buffer()
    # Declare PNG length matching a real PNG, but deliver garbage bytes of
    # the same length.
    frame = _png()
    garbage = b"\x00" * len(frame)
    buf.begin("scope-1", "t-1", _decl(frame))
    state, metadata, error = buf.receive("scope-1", "t-1", garbage)
    assert state is VisualTransferState.FAILED
    assert error in {
        VisualTransferErrorCode.MALFORMED_IMAGE,
        VisualTransferErrorCode.MIME_MISMATCH,
    }
    assert buf.active_count() == 0
    assert buf.aggregate_bytes() == 0


def test_receive_rejects_mime_mismatch() -> None:
    buf = _buffer()
    png_frame = _png()
    # Declare PNG but deliver JPEG-like bytes of the same length.
    jpeg_magic = b"\xff\xd8\xff\xe0" + b"\x00" * (len(png_frame) - 4)
    buf.begin("scope-1", "t-1", _decl(png_frame))
    state, metadata, error = buf.receive("scope-1", "t-1", jpeg_magic)
    assert state is VisualTransferState.FAILED
    assert error in {
        VisualTransferErrorCode.MIME_MISMATCH,
        VisualTransferErrorCode.MALFORMED_IMAGE,
    }


# --- Idempotent terminal returns --------------------------------------------


def test_receive_after_completion_returns_idempotent() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    first = buf.receive("scope-1", "t-1", frame)
    second = buf.receive("scope-1", "t-1", frame)
    assert first == second
    assert second[0] is VisualTransferState.COMPLETED
    assert second[2] is None


def test_receive_after_failure_returns_not_found() -> None:
    """After a failure the pending record is dropped; a second receive reports
    not-found rather than re-emitting the prior error code (no state retention
    beyond the terminal transition)."""
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    buf.receive("scope-1", "t-1", frame + b"extra")
    state, metadata, error = buf.receive("scope-1", "t-1", frame + b"extra")
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert metadata is None


def test_cancel_after_completion_is_idempotent_completed() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    buf.receive("scope-1", "t-1", frame)
    state, error = buf.cancel("scope-1", "t-1")
    assert state is VisualTransferState.COMPLETED
    assert error is None


def test_cancel_unknown_transfer_returns_not_found() -> None:
    buf = _buffer()
    state, error = buf.cancel("scope-1", "no-such")
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.TRANSFER_NOT_FOUND


def test_status_unknown_transfer_returns_not_found() -> None:
    buf = _buffer()
    state, metadata, error = buf.status("scope-1", "no-such")
    assert state is VisualTransferState.FAILED
    assert error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert metadata is None


# --- Cleanup: cancel / disconnect -------------------------------------------


def test_cancel_drops_pending_transfer_and_clears_buffer() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    state, error = buf.cancel("scope-1", "t-1")
    assert state is VisualTransferState.CANCELLED
    assert error is None
    assert buf.active_count() == 0
    assert buf.aggregate_bytes() == 0
    # Subsequent status reports not-found (cross-call non-disclosure).
    state, _, err = buf.status("scope-1", "t-1")
    assert state is VisualTransferState.FAILED
    assert err is VisualTransferErrorCode.TRANSFER_NOT_FOUND


def test_clear_session_drops_all_transfers_for_scope() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    buf.begin("scope-1", "t-2", _decl(frame, handoff_id="h-2"))
    buf.begin("scope-2", "t-3", _decl(frame, handoff_id="h-1"))
    buf.clear_session("scope-1")
    assert buf.active_count() == 1
    # The other scope's transfer is untouched.
    state, _, _ = buf.status("scope-2", "t-3")
    assert state is VisualTransferState.PENDING


def test_clear_session_no_op_for_scope_with_no_transfers() -> None:
    buf = _buffer()
    buf.clear_session("scope-empty")
    assert buf.active_count() == 0


def test_clear_session_frees_handoff_slots_for_reuse() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    buf.clear_session("scope-1")
    # After disconnect cleanup, the same scoped handoff can begin again.
    state, error = buf.begin("scope-1", "t-2", _decl(frame, handoff_id="h-1"))
    assert state is VisualTransferState.PENDING
    assert error is None


# --- Expiry / clock boundaries ----------------------------------------------


def test_receive_after_expiry_returns_expired() -> None:
    clock = _Clock()
    buf = _buffer(clock=clock)
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    clock.advance(TRANSFER_TTL_SECONDS)
    state, metadata, error = buf.receive("scope-1", "t-1", frame)
    assert state is VisualTransferState.EXPIRED
    assert error is VisualTransferErrorCode.TRANSFER_EXPIRED
    assert metadata is None
    assert buf.active_count() == 0


def test_receive_at_exact_expiry_boundary_returns_expired() -> None:
    clock = _Clock()
    buf = _buffer(clock=clock)
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    # now == expires_at is treated as expired (>= comparison).
    clock.advance(TRANSFER_TTL_SECONDS)
    state, _, error = buf.receive("scope-1", "t-1", frame)
    assert state is VisualTransferState.EXPIRED
    assert error is VisualTransferErrorCode.TRANSFER_EXPIRED


def test_receive_just_before_expiry_succeeds() -> None:
    clock = _Clock()
    buf = _buffer(clock=clock)
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    clock.advance(TRANSFER_TTL_SECONDS - 0.001)
    state, metadata, error = buf.receive("scope-1", "t-1", frame)
    assert state is VisualTransferState.COMPLETED
    assert error is None
    assert metadata is not None


def test_expire_due_sweeps_expired_transfers() -> None:
    clock = _Clock()
    buf = _buffer(clock=clock)
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    buf.begin("scope-1", "t-2", _decl(frame, handoff_id="h-2"))
    clock.advance(TRANSFER_TTL_SECONDS)
    expired = buf.expire_due()
    assert expired == 2
    assert buf.active_count() == 0
    assert buf.aggregate_bytes() == 0


def test_expire_due_skips_non_expired_transfers() -> None:
    clock = _Clock()
    buf = _buffer(clock=clock)
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    clock.advance(TRANSFER_TTL_SECONDS // 2)
    expired = buf.expire_due()
    assert expired == 0
    assert buf.active_count() == 1


def test_expire_due_skips_terminal_transfers() -> None:
    clock = _Clock()
    buf = _buffer(clock=clock)
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    buf.receive("scope-1", "t-1", frame)
    clock.advance(TRANSFER_TTL_SECONDS * 2)
    expired = buf.expire_due()
    assert expired == 0


def test_expire_due_no_op_on_empty_buffer() -> None:
    buf = _buffer()
    assert buf.expire_due() == 0


def test_expire_due_frees_handoff_slot_for_reuse() -> None:
    clock = _Clock()
    buf = _buffer(clock=clock)
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame, handoff_id="h-1"))
    clock.advance(TRANSFER_TTL_SECONDS)
    assert buf.expire_due() == 1
    state, error = buf.begin("scope-1", "t-2", _decl(frame, handoff_id="h-1"))
    assert state is VisualTransferState.PENDING
    assert error is None


# --- Clock failure modes ----------------------------------------------------


def test_clock_raising_fails_closed() -> None:
    def raising_clock() -> float:
        raise RuntimeError("clock unavailable")

    buf = VisualTransferBuffer(clock=raising_clock)
    frame = _png()
    with pytest.raises(ContractValidationError):
        buf.begin("scope-1", "t-1", _decl(frame))


def test_clock_returning_non_finite_fails_closed() -> None:
    import math

    buf = VisualTransferBuffer(clock=lambda: math.nan)
    frame = _png()
    with pytest.raises(ContractValidationError):
        buf.begin("scope-1", "t-1", _decl(frame))


def test_clock_returning_bool_fails_closed() -> None:
    buf = VisualTransferBuffer(clock=lambda: True)  # type: ignore[return-value]
    frame = _png()
    with pytest.raises(ContractValidationError):
        buf.begin("scope-1", "t-1", _decl(frame))


def test_clock_returning_string_fails_closed() -> None:
    buf = VisualTransferBuffer(clock=lambda: "1000.0")  # type: ignore[return-value]
    frame = _png()
    with pytest.raises(ContractValidationError):
        buf.begin("scope-1", "t-1", _decl(frame))


# --- Buffer clearing / zeroing ----------------------------------------------


def test_buffer_is_zeroed_before_drop_on_failure() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    # Capture the pending record reference before failure clears it.
    pending = buf._transfers[("scope-1", "t-1")]  # type: ignore[attr-defined]
    buf.receive("scope-1", "t-1", frame + b"extra")
    # After failure the buffer bytearray is cleared and reference dropped.
    assert pending.buffer is None
    assert pending.received_bytes == 0


def test_buffer_is_zeroed_before_drop_on_cancel() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    pending = buf._transfers[("scope-1", "t-1")]  # type: ignore[attr-defined]
    buf.cancel("scope-1", "t-1")
    assert pending.buffer is None


def test_buffer_is_cleared_on_success_metadata_retained() -> None:
    buf = _buffer()
    frame = _png()
    buf.begin("scope-1", "t-1", _decl(frame))
    pending = buf._transfers[("scope-1", "t-1")]  # type: ignore[attr-defined]
    buf.receive("scope-1", "t-1", frame)
    # Success clears the byte buffer but retains metadata for status queries.
    assert pending.buffer is None
    assert pending.received_bytes == 0
    assert pending.metadata is not None
    assert pending.state is VisualTransferState.COMPLETED


# --- Repr / content-free ----------------------------------------------------


def test_pending_transfer_repr_is_content_free() -> None:
    pending = _PendingTransfer(
        declaration=_decl(_png()),
        actor_scope="scope-secret",
        handoff_id="handoff-secret",
        created_at=12345.678,
        expires_at=12345.678 + TRANSFER_TTL_SECONDS,
    )
    text = repr(pending)
    assert "scope-secret" not in text
    assert "handoff-secret" not in text
    assert "12345" not in text
    assert "state=" in text


# --- Forbidden imports / no I/O ---------------------------------------------


def test_buffer_module_imports_no_io_or_third_party() -> None:
    import core.visual_transfer.buffer as mod
    import inspect

    source = inspect.getsource(mod)
    # No network, disk, subprocess, OCR, browser, camera, or third-party imports.
    for forbidden in (
        "import socket",
        "import urllib",
        "import requests",
        "import http",
        "import subprocess",
        "import os\n",
        "import pathlib",
        "import io",
        "import PIL",
        "import numpy",
        "import cv2",
        "import pytesseract",
        "import asyncio",
        "import threading",
        "import multiprocessing",
        "open(",
        "subprocess.run",
    ):
        assert forbidden not in source, f"forbidden token in buffer source: {forbidden!r}"


def test_buffer_module_has_no_disk_write_calls() -> None:
    import core.visual_transfer.buffer as mod
    import inspect

    source = inspect.getsource(mod)
    for forbidden in (
        ".write(",
        ".writelines(",
        "pathlib.Path(",
        "os.remove(",
        "os.unlink(",
        "shutil.",
        "tempfile.",
    ):
        assert forbidden not in source, f"forbidden disk token: {forbidden!r}"
