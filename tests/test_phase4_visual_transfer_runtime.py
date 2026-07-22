"""Deterministic tests for the visual-transfer runtime adapter.

Covers: canonical begin/ready, all declaration bounds, invalid/future ID
factory behavior, exact bytes and size boundary, PNG/JPEG positive cases,
MIME mismatch, metadata/EXIF rejection, animated image rejection,
decompression/dimension limits, duplicate receive/cancel/status, expiry and
disconnect cleanup, aggregate buffer limit, cross-session non-disclosure,
content-free repr/errors, validate_server_message on every output, no
bytes/base64/data URL/path in JSON output, and source scan forbidding
network/filesystem/subprocess/capture/provider imports.
"""

from __future__ import annotations

import struct

import pytest

from core.protocol import validate_server_message
from core.visual_transfer import (
    VisualTransferBuffer,
    VisualTransferRuntime,
    VisualTransferService,
)
from core.visual_transfer.contracts import (
    MAX_ENCODED_BYTES,
    TRANSFER_TTL_SECONDS,
    VisualTransferErrorCode,
)


# --- Helpers -----------------------------------------------------------------


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + (b"\x00" * 4)


def _png(width: int = 1, height: int = 1) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", b"") + _chunk(b"IEND", b"")


def _jpeg(width: int = 1, height: int = 1) -> bytes:
    """Minimal JPEG: SOI + SOF0 + EOI."""
    sof_payload = b"\x08" + struct.pack(">HH", height, width) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    seg_len = 2 + len(sof_payload)
    segment = b"\xff\xc0" + struct.pack(">H", seg_len) + sof_payload
    return b"\xff\xd8" + segment + b"\xff\xd9"


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _runtime(
    *,
    clock=None,
    factory=None,
    accepted=None,
):
    clk = clock or _Clock()
    fac = factory or (lambda: "Transfer:1")
    acc = accepted or (lambda scope, handoff: True)
    buffer = VisualTransferBuffer(clock=clk)
    service = VisualTransferService(
        buffer=buffer,
        clock=clk,
        transfer_id_factory=fac,
        handoff_accepted=acc,
    )
    runtime = VisualTransferRuntime(service=service, clock=clk)
    return runtime, service, buffer


def _assert_valid(msg: dict) -> None:
    error = validate_server_message(msg)
    assert error is None, f"Invalid server message: {error}\n{msg}"


def _assert_all_valid(messages: list) -> None:
    assert len(messages) >= 1
    for msg in messages:
        _assert_valid(msg)


# --- Constructor / injection -------------------------------------------------


def test_runtime_rejects_non_service() -> None:
    with pytest.raises(TypeError):
        VisualTransferRuntime(service="not a service", clock=lambda: 1000.0)  # type: ignore[arg-type]


def test_runtime_rejects_non_callable_clock() -> None:
    buffer = VisualTransferBuffer(clock=lambda: 1000.0)
    service = VisualTransferService(
        buffer=buffer,
        clock=lambda: 1000.0,
        transfer_id_factory=lambda: "t-1",
        handoff_accepted=lambda s, h: True,
    )
    with pytest.raises(TypeError):
        VisualTransferRuntime(service=service, clock="not callable")  # type: ignore[arg-type]


# --- Canonical begin / ready ------------------------------------------------


def test_begin_returns_visual_transfer_ready() -> None:
    frame = _png()
    runtime, _, buffer = _runtime()
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert len(messages) == 1
    msg = messages[0]
    assert msg["type"] == "visual_transfer_ready"
    assert msg["request_id"] == "req-1"
    assert msg["transfer_id"] == "Transfer:1"
    assert isinstance(msg["expires_at"], float)
    assert msg["expires_at"] == 1000.0 + TRANSFER_TTL_SECONDS
    _assert_all_valid(messages)
    assert buffer.active_count() == 1


def test_begin_never_accepts_client_transfer_id() -> None:
    """The runtime's begin() signature has no transfer_id parameter; the
    transfer_id is always server-generated."""
    frame = _png()
    runtime, _, _ = _runtime()
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert messages[0]["type"] == "visual_transfer_ready"
    assert messages[0]["transfer_id"] == "Transfer:1"


# --- Declaration bounds through begin ---------------------------------------


def test_begin_rejects_unsupported_mime() -> None:
    runtime, _, buffer = _runtime()
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/gif",
        size_bytes=100,
        width=1,
        height=1,
        frame_count=1,
    )
    assert messages[0]["type"] == "visual_transfer_error"
    assert messages[0]["code"] == "mime_unsupported"
    _assert_all_valid(messages)
    assert buffer.active_count() == 0


def test_begin_rejects_zero_size() -> None:
    runtime, _, buffer = _runtime()
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=0,
        width=1,
        height=1,
        frame_count=1,
    )
    assert messages[0]["code"] == "size_exceeded"
    assert buffer.active_count() == 0


def test_begin_rejects_oversized_bytes() -> None:
    runtime, _, buffer = _runtime()
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=MAX_ENCODED_BYTES + 1,
        width=1,
        height=1,
        frame_count=1,
    )
    assert messages[0]["code"] == "size_exceeded"
    assert buffer.active_count() == 0


def test_begin_rejects_oversized_width() -> None:
    runtime, _, buffer = _runtime()
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=100,
        width=4097,
        height=1,
        frame_count=1,
    )
    assert messages[0]["code"] == "dimensions_exceeded"
    assert buffer.active_count() == 0


def test_begin_rejects_oversized_height() -> None:
    runtime, _, buffer = _runtime()
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=100,
        width=1,
        height=4097,
        frame_count=1,
    )
    assert messages[0]["code"] == "dimensions_exceeded"
    assert buffer.active_count() == 0


def test_begin_rejects_frame_count_not_one() -> None:
    runtime, _, buffer = _runtime()
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=100,
        width=1,
        height=1,
        frame_count=2,
    )
    assert messages[0]["code"] == "frame_count_invalid"
    assert buffer.active_count() == 0


def test_begin_rejects_handoff_not_accepted() -> None:
    frame = _png()
    runtime, _, buffer = _runtime(accepted=lambda s, h: False)
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert messages[0]["type"] == "visual_transfer_error"
    assert messages[0]["code"] == "handoff_not_accepted"
    assert "transfer_id" not in messages[0]
    _assert_all_valid(messages)
    assert buffer.active_count() == 0


# --- Invalid / future ID factory behavior -----------------------------------


def test_begin_rejects_empty_factory_output() -> None:
    frame = _png()
    runtime, _, buffer = _runtime(factory=lambda: "")
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert messages[0]["code"] == "unavailable"
    assert buffer.active_count() == 0


def test_begin_rejects_oversized_factory_output() -> None:
    frame = _png()
    runtime, _, buffer = _runtime(factory=lambda: "x" * 129)
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert messages[0]["code"] == "unavailable"


def test_begin_rejects_factory_with_invalid_chars() -> None:
    frame = _png()
    runtime, _, buffer = _runtime(factory=lambda: "bad id")
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert messages[0]["code"] == "unavailable"


def test_begin_rejects_when_factory_raises() -> None:
    frame = _png()

    def raising() -> str:
        raise RuntimeError("rng broken")

    runtime, _, buffer = _runtime(factory=raising)
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert messages[0]["code"] == "unavailable"
    assert buffer.active_count() == 0


# --- One active transfer per exact scope/handoff ----------------------------


def test_begin_rejects_second_active_for_same_scoped_handoff() -> None:
    frame = _png()
    counter = {"n": 0}

    def factory() -> str:
        counter["n"] += 1
        return f"Transfer:{counter['n']}"

    runtime, _, buffer = _runtime(factory=factory)
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-2",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert messages[0]["type"] == "visual_transfer_error"
    assert messages[0]["code"] == "rate_limited"
    _assert_all_valid(messages)
    assert buffer.active_count() == 1


# --- receive_binary: PNG positive case --------------------------------------


def test_receive_binary_png_completes() -> None:
    frame = _png()
    runtime, _, buffer = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    assert len(messages) == 2
    assert messages[0]["type"] == "visual_transfer_update"
    assert messages[0]["status"] == "completed"
    assert messages[0]["bytes_received"] == len(frame)
    assert messages[1]["type"] == "visual_transfer_complete"
    assert messages[1]["content_hash"].startswith("sha256.")
    assert len(messages[1]["content_hash"]) == 71
    _assert_all_valid(messages)
    assert buffer.aggregate_bytes() == 0


# --- receive_binary: JPEG positive case -------------------------------------


def test_receive_binary_jpeg_completes() -> None:
    frame = _jpeg()
    runtime, _, buffer = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/jpeg",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    assert messages[0]["status"] == "completed"
    assert messages[1]["type"] == "visual_transfer_complete"
    _assert_all_valid(messages)


# --- receive_binary: exact bytes and size boundary --------------------------


def test_receive_binary_rejects_str() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", "not bytes")
    assert messages[-1]["type"] == "visual_transfer_error"
    assert messages[-1]["code"] == "invalid_request"
    _assert_all_valid(messages)


def test_receive_binary_rejects_dict() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", {"data": frame})
    assert messages[-1]["code"] == "invalid_request"
    _assert_all_valid(messages)


def test_receive_binary_rejects_list() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", [frame])
    assert messages[-1]["code"] == "invalid_request"
    _assert_all_valid(messages)


def test_receive_binary_rejects_bytearray() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", bytearray(frame))
    assert messages[-1]["code"] == "invalid_request"
    _assert_all_valid(messages)


def test_receive_binary_rejects_memoryview() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", memoryview(frame))
    assert messages[-1]["code"] == "invalid_request"
    _assert_all_valid(messages)


def test_receive_binary_rejects_size_mismatch() -> None:
    frame = _png()
    runtime, _, buffer = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame + b"extra")
    assert messages[-1]["code"] == "size_exceeded"
    _assert_all_valid(messages)
    assert buffer.active_count() == 0


def test_receive_binary_rejects_truncated_frame() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame[:-1])
    assert messages[-1]["code"] == "size_exceeded"
    _assert_all_valid(messages)


# --- MIME mismatch ----------------------------------------------------------


def test_receive_binary_rejects_mime_mismatch() -> None:
    png_frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(png_frame),
        width=1,
        height=1,
        frame_count=1,
    )
    # Deliver JPEG-like bytes of the same length.
    jpeg_like = b"\xff\xd8\xff\xe0" + b"\x00" * (len(png_frame) - 4)
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", jpeg_like)
    assert messages[-1]["code"] in {"mime_mismatch", "malformed_image"}
    _assert_all_valid(messages)


# --- Metadata / EXIF rejection ----------------------------------------------


def test_receive_binary_rejects_png_with_text_metadata() -> None:
    """PNG with tEXt chunk must be rejected."""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    text_data = b"Comment\x00hello"
    frame = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"tEXt", text_data)
        + _chunk(b"IDAT", b"")
        + _chunk(b"IEND", b"")
    )
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    assert messages[-1]["code"] == "metadata_rejected"
    _assert_all_valid(messages)


def test_receive_binary_rejects_jpeg_with_exif() -> None:
    """JPEG with APP1 EXIF segment must be rejected."""
    sof = struct.pack(">HH", 1, 1)
    exif_data = b"Exif\x00\x00" + b"\x00" * 20
    frame = (
        b"\xff\xd8"
        + b"\xff\xe1" + struct.pack(">H", 2 + len(exif_data)) + exif_data
        + b"\xff\xc0" + struct.pack(">H", 2 + 1 + 4 + 1) + b"\x08" + sof + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
        + b"\xff\xd9"
    )
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/jpeg",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    assert messages[-1]["code"] == "metadata_rejected"
    _assert_all_valid(messages)


# --- Animated image rejection -----------------------------------------------


def test_receive_binary_rejects_apng_animation() -> None:
    """PNG with acTL (animation control) chunk must be rejected."""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    actl_data = struct.pack(">II", 1, 0)  # num_frames=1, num_plays=0
    frame = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"acTL", actl_data)
        + _chunk(b"IDAT", b"")
        + _chunk(b"IEND", b"")
    )
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    assert messages[-1]["code"] == "metadata_rejected"
    _assert_all_valid(messages)


# --- Dimension mismatch ------------------------------------------------------


def test_receive_binary_rejects_dimension_mismatch() -> None:
    """Declared 1x1 but actual PNG is 2x2."""
    frame = _png(width=2, height=2)
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    assert messages[-1]["code"] == "dimensions_exceeded"
    _assert_all_valid(messages)


# --- Duplicate receive / cancel / status ------------------------------------


def test_duplicate_receive_cannot_complete_twice() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    first = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    assert first[0]["status"] == "completed"
    second = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    # Second receive finds the transfer already completed; the buffer returns
    # the completed state idempotently but does not re-validate.
    _assert_all_valid(second)
    # The second receive must not produce a second complete message.
    complete_count = sum(1 for m in second if m["type"] == "visual_transfer_complete")
    assert complete_count == 0


def test_duplicate_cancel_is_safe() -> None:
    frame = _png()
    runtime, _, buffer = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    first = runtime.cancel("scope-1", "req-1", "Transfer:1")
    assert first[0]["status"] == "cancelled"
    _assert_all_valid(first)
    second = runtime.cancel("scope-1", "req-1", "Transfer:1")
    # After cancel, the transfer is dropped; second cancel returns not_found.
    assert second[-1]["code"] == "transfer_not_found"
    _assert_all_valid(second)
    assert buffer.active_count() == 0


def test_duplicate_status_is_read_only() -> None:
    frame = _png()
    runtime, _, buffer = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    first = runtime.status("scope-1", "req-1", "Transfer:1")
    second = runtime.status("scope-1", "req-1", "Transfer:1")
    assert first == second
    assert first[0]["status"] == "pending"
    _assert_all_valid(first)
    _assert_all_valid(second)
    assert buffer.active_count() == 1


def test_completed_transfer_cannot_be_reopened() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    # Cancel after completion is idempotent completed.
    cancel = runtime.cancel("scope-1", "req-1", "Transfer:1")
    assert cancel[0]["status"] == "completed"
    _assert_all_valid(cancel)


# --- Expiry and disconnect cleanup ------------------------------------------


def test_receive_after_expiry_returns_expired() -> None:
    frame = _png()
    clock = _Clock()
    runtime, _, buffer = _runtime(clock=clock)
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    clock.advance(TRANSFER_TTL_SECONDS)
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    assert messages[-1]["code"] == "transfer_expired"
    _assert_all_valid(messages)
    assert buffer.active_count() == 0


def test_expire_due_sweeps_expired() -> None:
    frame = _png()
    clock = _Clock()
    runtime, _, buffer = _runtime(clock=clock)
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    clock.advance(TRANSFER_TTL_SECONDS)
    expired = runtime.expire_due()
    assert expired == 1
    assert buffer.active_count() == 0


def test_disconnect_cleanup_drops_transient_transfer() -> None:
    frame = _png()
    runtime, _, buffer = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    runtime.clear_session("scope-1")
    assert buffer.active_count() == 0
    assert buffer.aggregate_bytes() == 0
    # Status after cleanup returns not_found.
    messages = runtime.status("scope-1", "req-1", "Transfer:1")
    assert messages[-1]["code"] == "transfer_not_found"
    _assert_all_valid(messages)


# --- Aggregate buffer limit -------------------------------------------------


def test_aggregate_buffer_limit_rejects_excess() -> None:
    frame = _png()
    cap = len(frame) * 2 - 1
    clock = _Clock()
    buffer = VisualTransferBuffer(clock=clock, aggregate_cap_bytes=cap)
    service = VisualTransferService(
        buffer=buffer,
        clock=clock,
        transfer_id_factory=lambda: "Transfer:1",
        handoff_accepted=lambda s, h: True,
    )
    runtime = VisualTransferRuntime(service=service, clock=clock)
    first = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert first[0]["type"] == "visual_transfer_ready"
    counter = {"n": 1}

    def factory() -> str:
        counter["n"] += 1
        return f"Transfer:{counter['n']}"

    service._transfer_id_factory = factory  # type: ignore[attr-defined]
    second = runtime.begin(
        actor_scope="scope-1",
        request_id="req-2",
        handoff_id="h-2",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert second[0]["type"] == "visual_transfer_error"
    assert second[0]["code"] == "rate_limited"
    _assert_all_valid(second)
    assert buffer.active_count() == 1


# --- Cross-session non-disclosure -------------------------------------------


def test_cross_scope_receive_discloses_nothing() -> None:
    frame = _png()
    runtime, _, buffer = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-2", "req-1", "Transfer:1", frame)
    assert messages[-1]["code"] == "transfer_not_found"
    _assert_all_valid(messages)
    assert buffer.active_count() == 1


def test_cross_scope_status_discloses_nothing() -> None:
    frame = _png()
    runtime, _, buffer = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.status("scope-2", "req-1", "Transfer:1")
    assert messages[-1]["code"] == "transfer_not_found"
    _assert_all_valid(messages)
    assert buffer.active_count() == 1


def test_cross_scope_cancel_discloses_nothing() -> None:
    frame = _png()
    runtime, _, buffer = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.cancel("scope-2", "req-1", "Transfer:1")
    assert messages[-1]["code"] == "transfer_not_found"
    _assert_all_valid(messages)
    assert buffer.active_count() == 1


# --- Content-free repr / errors ---------------------------------------------


def test_error_messages_do_not_leak_scope_or_handoff() -> None:
    frame = _png()
    runtime, _, _ = _runtime(accepted=lambda s, h: False)
    messages = runtime.begin(
        actor_scope="scope-secret",
        request_id="req-1",
        handoff_id="handoff-secret",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    import json

    text = json.dumps(messages)
    assert "scope-secret" not in text
    assert "handoff-secret" not in text


def test_no_bytes_in_json_output() -> None:
    """No outbound JSON message may contain raw frame bytes, base64, data
    URLs, or filesystem paths."""
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    import json

    for msg in messages:
        text = json.dumps(msg)
        assert b"\x89PNG" not in text.encode("latin-1")
        assert "data:" not in text
        assert "base64" not in text
        assert "/tmp/" not in text
        assert "/var/" not in text
        assert ".png" not in text
        assert ".jpg" not in text


# --- validate_server_message on every output ---------------------------------


def test_all_begin_outputs_pass_validate_server_message() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    _assert_all_valid(messages)


def test_all_error_outputs_pass_validate_server_message() -> None:
    frame = _png()
    runtime, _, _ = _runtime(accepted=lambda s, h: False)
    messages = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    _assert_all_valid(messages)


def test_all_receive_outputs_pass_validate_server_message() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    _assert_all_valid(messages)


def test_all_status_outputs_pass_validate_server_message() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.status("scope-1", "req-1", "Transfer:1")
    _assert_all_valid(messages)


def test_all_cancel_outputs_pass_validate_server_message() -> None:
    frame = _png()
    runtime, _, _ = _runtime()
    runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    messages = runtime.cancel("scope-1", "req-1", "Transfer:1")
    _assert_all_valid(messages)


# --- Full lifecycle ----------------------------------------------------------


def test_full_lifecycle_begin_receive_status_cancel() -> None:
    frame = _png()
    runtime, _, buffer = _runtime()
    begun = runtime.begin(
        actor_scope="scope-1",
        request_id="req-1",
        handoff_id="h-1",
        mime_type="image/png",
        size_bytes=len(frame),
        width=1,
        height=1,
        frame_count=1,
    )
    assert begun[0]["type"] == "visual_transfer_ready"
    assert begun[0]["transfer_id"] == "Transfer:1"

    received = runtime.receive_binary("scope-1", "req-1", "Transfer:1", frame)
    assert received[0]["status"] == "completed"
    assert received[1]["type"] == "visual_transfer_complete"

    status = runtime.status("scope-1", "req-1", "Transfer:1")
    assert status[0]["status"] == "completed"
    assert status[1]["type"] == "visual_transfer_complete"

    cancel = runtime.cancel("scope-1", "req-1", "Transfer:1")
    assert cancel[0]["status"] == "completed"
    _assert_all_valid(begun + received + status + cancel)
    assert buffer.aggregate_bytes() == 0


# --- Source scan: no forbidden imports / no I/O ------------------------------


def _strip_docstring(source: str) -> str:
    import ast

    tree = ast.parse(source)
    if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(
        tree.body[0].value, ast.Constant
    ) and isinstance(tree.body[0].value.value, str):
        tree.body = tree.body[1:]
    return ast.unparse(tree)


def test_runtime_module_imports_no_io_or_third_party() -> None:
    import inspect

    import core.visual_transfer.runtime as mod

    code_only = _strip_docstring(inspect.getsource(mod))
    for forbidden in (
        "import socket",
        "import urllib",
        "import requests",
        "import http",
        "import subprocess",
        "import os\n",
        "import pathlib",
        "import io\n",
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
        assert forbidden not in code_only, f"forbidden token in runtime source: {forbidden!r}"


def test_runtime_module_has_no_disk_write_calls() -> None:
    import inspect

    import core.visual_transfer.runtime as mod

    code_only = _strip_docstring(inspect.getsource(mod))
    for forbidden in (
        ".write(",
        ".writelines(",
        "pathlib.Path(",
        "os.remove(",
        "os.unlink(",
        "shutil.",
        "tempfile.",
    ):
        assert forbidden not in code_only, f"forbidden disk token: {forbidden!r}"


def test_runtime_module_has_no_network_calls() -> None:
    import inspect

    import core.visual_transfer.runtime as mod

    code_only = _strip_docstring(inspect.getsource(mod))
    for forbidden in (
        "socket.socket(",
        "urllib.request",
        "requests.",
        "http.client",
        "urlopen(",
        ".connect(",
    ):
        assert forbidden not in code_only, f"forbidden network token: {forbidden!r}"


def test_runtime_module_has_no_camera_screenshot_ocr() -> None:
    import inspect

    import core.visual_transfer.runtime as mod

    code_only = _strip_docstring(inspect.getsource(mod))
    for forbidden in (
        "cv2.VideoCapture",
        "pytesseract.image_to_string",
        "ImageGrab",
        "screencapture",
        "osascript",
        "subprocess.check_output",
    ):
        assert forbidden not in code_only, f"forbidden capture/OCR token: {forbidden!r}"


def test_runtime_module_has_no_provider_selection() -> None:
    import inspect

    import core.visual_transfer.runtime as mod

    code_only = _strip_docstring(inspect.getsource(mod))
    for forbidden in (
        "select_provider",
        "choose_provider",
        "provider_factory",
        "get_provider",
        "llm.complete",
        "chat.completion",
    ):
        assert forbidden not in code_only, f"forbidden provider token: {forbidden!r}"
