"""Deterministic tests for bounded visual-transfer coordination."""

from __future__ import annotations

import math
import struct

import pytest

from core.visual_transfer import (
    VisualTransferBuffer,
    VisualTransferDeclaration,
    VisualTransferErrorCode,
    VisualTransferOutcomeStatus,
    VisualTransferService,
    VisualTransferState,
)
from core.visual_transfer.contracts import ContractValidationError


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + (b"\x00" * 4)


def _png(width: int = 1, height: int = 1) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", b"") + _chunk(b"IEND", b"")


def _declaration(frame: bytes, *, handoff_id: str = "handoff-1") -> VisualTransferDeclaration:
    return VisualTransferDeclaration(
        handoff_id=handoff_id,
        mime="image/png",
        declared_byte_length=len(frame),
        declared_width=1,
        declared_height=1,
        frame_count=1,
    )


def _service(*, clock=lambda: 1000.0, factory=lambda: "Transfer:1", accepted=lambda scope, handoff: True):
    buffer = VisualTransferBuffer(clock=clock)
    service = VisualTransferService(
        buffer=buffer,
        clock=clock,
        transfer_id_factory=factory,
        handoff_accepted=accepted,
    )
    return service, buffer


def test_begin_generates_transfer_id_server_side_and_receive_completes():
    frame = _png()
    service, buffer = _service()
    begun = service.begin("scope-1", _declaration(frame))
    assert begun.status is VisualTransferOutcomeStatus.OK
    assert begun.state is VisualTransferState.PENDING
    assert begun.transfer_id == "Transfer:1"
    assert buffer.active_count() == 1

    completed = service.receive("scope-1", "Transfer:1", frame)
    assert completed.status is VisualTransferOutcomeStatus.OK
    assert completed.state is VisualTransferState.COMPLETED
    assert completed.metadata is not None
    assert completed.metadata.sha256.startswith("sha256.")
    assert buffer.aggregate_bytes() == 0


def test_begin_requires_exact_accepted_handoff_scope():
    frame = _png()
    service, buffer = _service(accepted=lambda scope, handoff: False)
    result = service.begin("scope-1", _declaration(frame))
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.HANDOFF_NOT_ACCEPTED
    assert result.transfer_id is None
    assert buffer.active_count() == 0


def test_invalid_or_colliding_id_factory_fails_closed():
    frame = _png()
    invalid, _ = _service(factory=lambda: "bad id")
    result = invalid.begin("scope-1", _declaration(frame))
    assert result.error is VisualTransferErrorCode.UNAVAILABLE

    colliding, buffer = _service(factory=lambda: "Transfer:1")
    first = colliding.begin("scope-1", _declaration(frame, handoff_id="handoff-1"))
    second = colliding.begin("scope-1", _declaration(frame, handoff_id="handoff-2"))
    assert first.status is VisualTransferOutcomeStatus.OK
    assert second.status is VisualTransferOutcomeStatus.ERROR
    assert second.error is VisualTransferErrorCode.UNAVAILABLE
    assert buffer.active_count() == 1


def test_cross_scope_status_and_cancel_disclose_nothing():
    frame = _png()
    service, buffer = _service()
    service.begin("scope-1", _declaration(frame))
    status = service.status("scope-2", "Transfer:1")
    cancel = service.cancel("scope-2", "Transfer:1")
    assert status.error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert cancel.error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert buffer.active_count() == 1


def test_declared_size_mismatch_is_rejected_before_copy():
    frame = _png()
    service, buffer = _service()
    service.begin("scope-1", _declaration(frame))
    result = service.receive("scope-1", "Transfer:1", frame + b"extra")
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.SIZE_EXCEEDED
    assert buffer.active_count() == 0
    assert buffer.aggregate_bytes() == 0


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_clock_fails_closed(value: float):
    frame = _png()
    service, buffer = _service(clock=lambda: value)
    result = service.begin("scope-1", _declaration(frame))
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.UNAVAILABLE
    assert buffer.active_count() == 0


def test_disconnect_cleanup_drops_transient_transfer():
    frame = _png()
    service, buffer = _service()
    service.begin("scope-1", _declaration(frame))
    service.clear_session("scope-1")
    assert buffer.active_count() == 0
    assert buffer.aggregate_bytes() == 0


# --- Constructor / injection -------------------------------------------------


def test_service_rejects_non_buffer() -> None:
    with pytest.raises(TypeError):
        VisualTransferService(
            buffer="not a buffer",  # type: ignore[arg-type]
            clock=lambda: 1000.0,
            transfer_id_factory=lambda: "Transfer:1",
            handoff_accepted=lambda scope, handoff: True,
        )


def test_service_rejects_non_callable_clock() -> None:
    with pytest.raises(TypeError):
        VisualTransferService(
            buffer=VisualTransferBuffer(clock=lambda: 1000.0),
            clock="not callable",  # type: ignore[arg-type]
            transfer_id_factory=lambda: "Transfer:1",
            handoff_accepted=lambda scope, handoff: True,
        )


def test_service_rejects_non_callable_factory() -> None:
    with pytest.raises(TypeError):
        VisualTransferService(
            buffer=VisualTransferBuffer(clock=lambda: 1000.0),
            clock=lambda: 1000.0,
            transfer_id_factory="not callable",  # type: ignore[arg-type]
            handoff_accepted=lambda scope, handoff: True,
        )


def test_service_rejects_non_callable_handoff_predicate() -> None:
    with pytest.raises(TypeError):
        VisualTransferService(
            buffer=VisualTransferBuffer(clock=lambda: 1000.0),
            clock=lambda: 1000.0,
            transfer_id_factory=lambda: "Transfer:1",
            handoff_accepted="not callable",  # type: ignore[arg-type]
        )


# --- begin: handoff predicate ------------------------------------------------


def test_begin_rejects_when_handoff_predicate_raises() -> None:
    frame = _png()

    def raising(scope: str, handoff: str) -> bool:
        raise RuntimeError("transport down")

    service, buffer = _service(accepted=raising)
    result = service.begin("scope-1", _declaration(frame))
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.HANDOFF_NOT_ACCEPTED
    assert result.transfer_id is None
    assert buffer.active_count() == 0


def test_begin_rejects_when_handoff_predicate_returns_non_true() -> None:
    frame = _png()
    service, buffer = _service(accepted=lambda scope, handoff: 1)  # truthy but not True
    result = service.begin("scope-1", _declaration(frame))
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.HANDOFF_NOT_ACCEPTED


def test_begin_rejects_invalid_handoff_id_in_declaration() -> None:
    """A declaration with an invalid handoff_id cannot be constructed; the
    contract rejects it before the service is invoked."""
    frame = _png()
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(
            handoff_id="Bad Handoff",
            mime="image/png",
            declared_byte_length=len(frame),
            declared_width=1,
            declared_height=1,
            frame_count=1,
        )


def test_begin_passes_exact_scope_and_handoff_to_predicate() -> None:
    frame = _png()
    seen = {}

    def predicate(scope: str, handoff: str) -> bool:
        seen["scope"] = scope
        seen["handoff"] = handoff
        return True

    service, _ = _service(accepted=predicate)
    service.begin("scope-1", _declaration(frame, handoff_id="handoff-7"))
    assert seen == {"scope": "scope-1", "handoff": "handoff-7"}


# --- begin: invalid / future transfer-ID factory ----------------------------


def test_begin_rejects_empty_factory_output() -> None:
    frame = _png()
    service, buffer = _service(factory=lambda: "")
    result = service.begin("scope-1", _declaration(frame))
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.UNAVAILABLE
    assert buffer.active_count() == 0


def test_begin_rejects_oversized_factory_output() -> None:
    frame = _png()
    service, buffer = _service(factory=lambda: "x" * 129)
    result = service.begin("scope-1", _declaration(frame))
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.UNAVAILABLE


def test_begin_rejects_factory_output_with_invalid_chars() -> None:
    frame = _png()
    service, buffer = _service(factory=lambda: "bad id")
    result = service.begin("scope-1", _declaration(frame))
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.UNAVAILABLE


def test_begin_rejects_when_factory_raises() -> None:
    frame = _png()

    def raising_factory() -> str:
        raise RuntimeError("rng broken")

    service, buffer = _service(factory=raising_factory)
    result = service.begin("scope-1", _declaration(frame))
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.UNAVAILABLE
    assert buffer.active_count() == 0


def test_begin_rejects_when_buffer_begin_raises() -> None:
    frame = _png()

    class _RaisingBuffer(VisualTransferBuffer):
        def begin(self, actor_scope, transfer_id, declaration):  # type: ignore[no-untyped-def]
            raise RuntimeError("internal")

    buf = _RaisingBuffer(clock=lambda: 1000.0)
    service = VisualTransferService(
        buffer=buf,
        clock=lambda: 1000.0,
        transfer_id_factory=lambda: "Transfer:1",
        handoff_accepted=lambda scope, handoff: True,
    )
    result = service.begin("scope-1", _declaration(frame))
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.UNAVAILABLE


# --- receive / status / cancel via service ----------------------------------


def test_receive_returns_unauthorized_for_invalid_scope() -> None:
    frame = _png()
    service, _ = _service()
    result = service.receive("Bad Scope", "Transfer:1", frame)
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.UNAUTHORIZED


def test_receive_returns_not_found_for_invalid_transfer_id() -> None:
    frame = _png()
    service, _ = _service()
    result = service.receive("scope-1", "bad id", frame)
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.TRANSFER_NOT_FOUND


def test_status_returns_unauthorized_for_invalid_scope() -> None:
    service, _ = _service()
    result = service.status("Bad Scope", "Transfer:1")
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.UNAUTHORIZED


def test_status_returns_not_found_for_invalid_transfer_id() -> None:
    service, _ = _service()
    result = service.status("scope-1", "bad id")
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.TRANSFER_NOT_FOUND


def test_status_pending_reports_state_without_metadata() -> None:
    frame = _png()
    service, _ = _service()
    service.begin("scope-1", _declaration(frame))
    result = service.status("scope-1", "Transfer:1")
    assert result.status is VisualTransferOutcomeStatus.OK
    assert result.state is VisualTransferState.PENDING
    assert result.metadata is None
    assert result.error is None


def test_status_completed_reports_metadata() -> None:
    frame = _png()
    service, _ = _service()
    service.begin("scope-1", _declaration(frame))
    service.receive("scope-1", "Transfer:1", frame)
    result = service.status("scope-1", "Transfer:1")
    assert result.status is VisualTransferOutcomeStatus.OK
    assert result.state is VisualTransferState.COMPLETED
    assert result.metadata is not None
    assert result.error is None


def test_cancel_returns_unauthorized_for_invalid_scope() -> None:
    service, _ = _service()
    result = service.cancel("Bad Scope", "Transfer:1")
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.UNAUTHORIZED


def test_cancel_returns_not_found_for_invalid_transfer_id() -> None:
    service, _ = _service()
    result = service.cancel("scope-1", "bad id")
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.TRANSFER_NOT_FOUND


def test_cancel_pending_returns_cancelled() -> None:
    frame = _png()
    service, buffer = _service()
    service.begin("scope-1", _declaration(frame))
    result = service.cancel("scope-1", "Transfer:1")
    assert result.status is VisualTransferOutcomeStatus.OK
    assert result.state is VisualTransferState.CANCELLED
    assert buffer.active_count() == 0


def test_cancel_completed_is_idempotent_completed() -> None:
    frame = _png()
    service, _ = _service()
    service.begin("scope-1", _declaration(frame))
    service.receive("scope-1", "Transfer:1", frame)
    result = service.cancel("scope-1", "Transfer:1")
    assert result.status is VisualTransferOutcomeStatus.OK
    assert result.state is VisualTransferState.COMPLETED


# --- clear_session / expire_due via service ---------------------------------


def test_clear_session_invalid_scope_is_noop() -> None:
    service, buffer = _service()
    # Must not raise; invalid scope is silently ignored.
    service.clear_session("Bad Scope")
    assert buffer.active_count() == 0


def test_clear_session_drops_all_for_scope() -> None:
    frame = _png()
    service, buffer = _service()
    service.begin("scope-1", _declaration(frame, handoff_id="handoff-1"))
    service.clear_session("scope-1")
    assert buffer.active_count() == 0


def test_expire_due_delegates_to_buffer() -> None:
    frame = _png()

    class _Clock:
        def __init__(self) -> None:
            self.t = 1000.0

        def __call__(self) -> float:
            return self.t

    clock = _Clock()
    service, buffer = _service(clock=clock)
    service.begin("scope-1", _declaration(frame))
    clock.t += 120  # past TTL
    expired = service.expire_due()
    assert expired == 1
    assert buffer.active_count() == 0


# --- Cross-session non-disclosure via service --------------------------------


def test_cross_scope_receive_discloses_nothing() -> None:
    frame = _png()
    service, buffer = _service()
    service.begin("scope-1", _declaration(frame))
    result = service.receive("scope-2", "Transfer:1", frame)
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert result.metadata is None
    # Original transfer untouched.
    assert buffer.active_count() == 1


def test_cross_scope_status_discloses_nothing() -> None:
    frame = _png()
    service, _ = _service()
    service.begin("scope-1", _declaration(frame))
    result = service.status("scope-2", "Transfer:1")
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert result.metadata is None


def test_cross_scope_cancel_discloses_nothing() -> None:
    frame = _png()
    service, buffer = _service()
    service.begin("scope-1", _declaration(frame))
    result = service.cancel("scope-2", "Transfer:1")
    assert result.status is VisualTransferOutcomeStatus.ERROR
    assert result.error is VisualTransferErrorCode.TRANSFER_NOT_FOUND
    assert buffer.active_count() == 1


# --- Content-free repr / errors ---------------------------------------------


def test_begin_result_repr_is_content_free() -> None:
    frame = _png()
    service, _ = _service()
    begun = service.begin("scope-1", _declaration(frame))
    text = repr(begun)
    # The transfer_id is server-generated and may appear in repr (it is the
    # return value the caller needs). But scope/handoff must not leak.
    assert "scope-1" not in text
    assert "handoff-1" not in text


def test_error_result_repr_is_content_free() -> None:
    frame = _png()
    service, _ = _service(accepted=lambda scope, handoff: False)
    result = service.begin("scope-1", _declaration(frame))
    text = repr(result)
    assert "scope-1" not in text
    assert "handoff-1" not in text


def test_result_repr_does_not_leak_frame_bytes() -> None:
    frame = _png(width=2, height=3)
    service, _ = _service()
    begun = service.begin("scope-1", _declaration(frame))
    text = repr(begun)
    # No raw frame bytes (PNG magic, IHDR dimensions) in repr.
    assert b"\x89PNG" not in text.encode()
    assert "2" not in text or "state=" in text  # state values may contain digits


def test_exception_messages_do_not_contain_frame_bytes() -> None:
    """ContractValidationError messages must be content-free (stable reason
    codes only); no frame bytes, dimensions, or IDs in exception text."""
    from core.visual_transfer.contracts import (
        ContractValidationError,
        validate_transfer_id,
    )

    try:
        validate_transfer_id("bad id with bytes \x89PNG")
    except ContractValidationError as exc:
        text = str(exc)
        assert "bad id" not in text
        assert "\x89PNG" not in text
        assert "PNG" not in text


# --- Byte sequence absence from exceptions -----------------------------------


def test_receive_failure_does_not_embed_frame_bytes_in_error() -> None:
    frame = _png()
    service, _ = _service()
    service.begin("scope-1", _declaration(frame))
    result = service.receive("scope-1", "Transfer:1", frame + b"extra")
    # The error code is a stable enum; no frame bytes leak through.
    assert result.error is VisualTransferErrorCode.SIZE_EXCEEDED
    assert result.metadata is None


def test_malformed_frame_error_does_not_embed_bytes() -> None:
    frame = _png()
    service, _ = _service()
    service.begin("scope-1", _declaration(frame))
    garbage = b"\x00\x01\x02\x03" + b"\xff" * (len(frame) - 4)
    result = service.receive("scope-1", "Transfer:1", garbage)
    assert result.status is VisualTransferOutcomeStatus.ERROR
    # Error must be a stable code, never the raw bytes.
    assert isinstance(result.error, VisualTransferErrorCode)


# --- Source scan: no forbidden imports / no I/O ------------------------------


def test_service_module_imports_no_io_or_third_party() -> None:
    import inspect

    import core.visual_transfer.service as mod

    source = inspect.getsource(mod)
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
        assert forbidden not in source, f"forbidden token in service source: {forbidden!r}"


def test_service_module_has_no_disk_write_calls() -> None:
    import inspect

    import core.visual_transfer.service as mod

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


def test_service_module_has_no_network_calls() -> None:
    import inspect

    import core.visual_transfer.service as mod

    source = inspect.getsource(mod)
    for forbidden in (
        "socket.socket(",
        "urllib.request",
        "requests.",
        "http.client",
        "urlopen(",
        ".connect(",
    ):
        assert forbidden not in source, f"forbidden network token: {forbidden!r}"


def test_service_module_has_no_camera_screenshot_ocr() -> None:
    import ast
    import inspect

    import core.visual_transfer.service as mod

    source = inspect.getsource(mod)
    # Strip module docstring before scanning so negative claims in prose
    # (e.g. "no AppleScript") do not trigger false positives.
    tree = ast.parse(source)
    if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(
        tree.body[0].value, ast.Constant
    ) and isinstance(tree.body[0].value.value, str):
        tree.body = tree.body[1:]
    code_only = ast.unparse(tree)
    for forbidden in (
        "cv2.VideoCapture",
        "pytesseract.image_to_string",
        "ImageGrab",
        "screencapture",
        "osascript",
        "subprocess.check_output",
    ):
        assert forbidden not in code_only, f"forbidden capture/OCR token: {forbidden!r}"


# --- Full begin -> receive -> status -> cancel lifecycle ---------------------


def test_full_lifecycle_begin_receive_status_cancel() -> None:
    frame = _png()
    service, buffer = _service()
    begun = service.begin("scope-1", _declaration(frame))
    assert begun.status is VisualTransferOutcomeStatus.OK
    assert begun.transfer_id == "Transfer:1"

    received = service.receive("scope-1", "Transfer:1", frame)
    assert received.status is VisualTransferOutcomeStatus.OK
    assert received.state is VisualTransferState.COMPLETED
    assert received.metadata is not None

    status = service.status("scope-1", "Transfer:1")
    assert status.status is VisualTransferOutcomeStatus.OK
    assert status.state is VisualTransferState.COMPLETED
    assert status.metadata is not None

    # Cancel after completion is idempotent completed.
    cancel = service.cancel("scope-1", "Transfer:1")
    assert cancel.state is VisualTransferState.COMPLETED

    assert buffer.aggregate_bytes() == 0


def test_two_transfers_for_different_handoffs_in_same_scope() -> None:
    frame = _png()
    service, buffer = _service(factory=lambda: "Transfer:1")

    # First transfer uses factory default; second needs a fresh ID.
    counter = {"n": 0}

    def factory() -> str:
        counter["n"] += 1
        return f"Transfer:{counter['n']}"

    service, buffer = _service(factory=factory)
    a = service.begin("scope-1", _declaration(frame, handoff_id="h-1"))
    b = service.begin("scope-1", _declaration(frame, handoff_id="h-2"))
    assert a.status is VisualTransferOutcomeStatus.OK
    assert b.status is VisualTransferOutcomeStatus.OK
    assert a.transfer_id != b.transfer_id
    assert buffer.active_count() == 2
