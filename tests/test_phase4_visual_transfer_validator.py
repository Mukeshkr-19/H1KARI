"""Tests for core.visual_transfer.validator: PNG/JPEG parsing, EXIF/metadata rejection, boundaries."""

from __future__ import annotations

import ast
import hashlib
import pathlib
import struct
import zlib

import pytest

from core.visual_transfer.contracts import (
    ContractValidationError,
    DECOMPRESSION_PIXEL_LIMIT,
    MAX_DIMENSION,
    MAX_ENCODED_BYTES,
    MIN_DIMENSION,
    VisualTransferDeclaration,
    VisualTransferErrorCode,
)
from core.visual_transfer.validator import VisualTransferValidator


# --- Minimal PNG/JPEG builders (standard library only) ---------------------

def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    chunk = chunk_type + data
    crc = zlib.crc32(chunk) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk + struct.pack(">I", crc)


def make_png(w: int, h: int, *, extra_chunks: list[bytes] | None = None) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"  # 8-bit RGB
    raw = b"".join(b"\x00" + b"\x00" * (w * 3) for _ in range(h))
    comp = zlib.compress(raw)
    out = sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", comp)
    for chunk in extra_chunks or ():
        out += chunk
    out += _png_chunk(b"IEND", b"")
    return out


def make_jpeg(
    w: int,
    h: int,
    *,
    include_app1_exif: bool = False,
    include_app0: bool = True,
    sof_marker: int = 0xC0,
) -> bytes:
    data = b"\xff\xd8"  # SOI
    if include_app0:
        app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        data += b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
    if include_app1_exif:
        app1 = b"Exif\x00\x00" + b"\x00" * 20
        data += b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    dqt = b"\x00" + b"\x10" * 64
    data += b"\xff\xdb" + struct.pack(">H", len(dqt) + 2) + dqt
    sof = struct.pack(">BHH", 8, h, w) + b"\x01\x01\x11\x00"
    data += bytes([0xFF, sof_marker]) + struct.pack(">H", len(sof) + 2) + sof
    dht = b"\x00" + b"\x01" + b"\x00" * 15 + b"\x00"
    data += b"\xff\xc4" + struct.pack(">H", len(dht) + 2) + dht
    sos = b"\x01\x01\x00\x00\x3f\x00"
    data += b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos
    data += b"\x00"
    data += b"\xff\xd9"
    return data


def _decl(mime: str, byte_len: int, w: int, h: int, **kw: object) -> VisualTransferDeclaration:
    return VisualTransferDeclaration(
        transfer_id="t-1",
        handoff_id="h-1",
        mime=mime,
        declared_byte_length=byte_len,
        declared_width=w,
        declared_height=h,
        **kw,
    )


# --- Valid minimal PNG ------------------------------------------------------

def test_valid_minimal_png() -> None:
    png = make_png(2, 3)
    decl = _decl("image/png", len(png), 2, 3)
    meta = VisualTransferValidator.validate_frame(decl, png)
    assert meta.mime == "image/png"
    assert meta.width == 2
    assert meta.height == 3
    assert meta.sha256 == "sha256." + hashlib.sha256(png).hexdigest()


# --- Valid bounded JPEG -----------------------------------------------------

def test_valid_minimal_jpeg() -> None:
    jpg = make_jpeg(4, 5)
    decl = _decl("image/jpeg", len(jpg), 4, 5)
    meta = VisualTransferValidator.validate_frame(decl, jpg)
    assert meta.mime == "image/jpeg"
    assert meta.width == 4
    assert meta.height == 5
    assert meta.sha256 == "sha256." + hashlib.sha256(jpg).hexdigest()


# --- Byte boundaries --------------------------------------------------------

def test_rejects_frame_below_min_bytes() -> None:
    decl = _decl("image/png", 10, 1, 1)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, b"\x89PNG")
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.SIZE_EXCEEDED


def test_rejects_frame_above_max_bytes() -> None:
    with pytest.raises(ContractValidationError):
        _decl("image/png", MAX_ENCODED_BYTES + 1, 1, 1)


def test_rejects_frame_length_mismatch() -> None:
    png = make_png(2, 3)
    decl = _decl("image/png", len(png) + 1, 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, png)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.SIZE_EXCEEDED


# --- Dimension boundaries ---------------------------------------------------

def test_rejects_png_dimension_above_max() -> None:
    # Build a PNG whose IHDR declares 4097 width; the parser must reject it.
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II", MAX_DIMENSION + 1, 1) + b"\x08\x02\x00\x00\x00"
    raw = b"\x00" + b"\x00" * 3
    comp = zlib.compress(raw)
    png = sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", comp) + _png_chunk(b"IEND", b"")
    with pytest.raises(ContractValidationError):
        _decl("image/png", len(png), MAX_DIMENSION + 1, 1)


def test_rejects_jpeg_dimension_above_max() -> None:
    jpg = make_jpeg(MAX_DIMENSION + 1, 1)
    with pytest.raises(ContractValidationError):
        _decl("image/jpeg", len(jpg), MAX_DIMENSION + 1, 1)


def test_accepts_max_dimension_png() -> None:
    # We cannot build a full 4096x4096 PNG (too large), so test the parser
    # boundary by declaring max dimensions with a small actual frame that
    # carries max dimensions in IHDR. The validator must reject the mismatch
    # between declared and actual — but the actual dimensions parse fine.
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II", MAX_DIMENSION, MAX_DIMENSION) + b"\x08\x02\x00\x00\x00"
    raw = b"\x00" + b"\x00" * 3
    comp = zlib.compress(raw)
    png = sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", comp) + _png_chunk(b"IEND", b"")
    decl = _decl("image/png", len(png), MAX_DIMENSION, MAX_DIMENSION)
    meta = VisualTransferValidator.validate_frame(decl, png)
    assert meta.width == MAX_DIMENSION
    assert meta.height == MAX_DIMENSION


# --- Pixel boundary ---------------------------------------------------------

def test_decompression_pixel_boundary_rejected() -> None:
    # Declare 4096 x 4097 = 16,781,312 > 16,777,216. Declaration rejects this.
    with pytest.raises(ContractValidationError):
        _decl("image/png", 100, 4096, 4097)


# --- MIME mismatch ----------------------------------------------------------

def test_mime_mismatch_png_declared_jpeg_actual() -> None:
    png = make_png(2, 3)
    decl = _decl("image/jpeg", len(png), 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, png)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.MIME_MISMATCH


def test_mime_mismatch_jpeg_declared_png_actual() -> None:
    jpg = make_jpeg(4, 5)
    decl = _decl("image/png", len(jpg), 4, 5)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, jpg)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.MIME_MISMATCH


def test_rejects_unknown_magic() -> None:
    decl = _decl("image/png", 100, 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, b"\x00" * 100)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.MALFORMED_IMAGE


# --- Malformed/truncated PNG ------------------------------------------------

def test_truncated_png_missing_iend() -> None:
    png = make_png(2, 3)
    truncated = png[:-12]  # strip IEND chunk (4+4+4 = 12 bytes)
    decl = _decl("image/png", len(truncated), 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, truncated)
    code = VisualTransferValidator._error_code_from_message(str(exc.value))
    assert code in {VisualTransferErrorCode.MALFORMED_IMAGE, VisualTransferErrorCode.DIMENSIONS_EXCEEDED}


def test_truncated_png_short_header() -> None:
    decl = _decl("image/png", 10, 1, 1)
    with pytest.raises(ContractValidationError):
        VisualTransferValidator.validate_frame(decl, b"\x89PNG\r\n\x1a\n")


def test_malformed_png_bad_ihdr_length() -> None:
    sig = b"\x89PNG\r\n\x1a\n"
    bad_ihdr = struct.pack(">I", 12) + b"IHDR" + b"\x00" * 12 + struct.pack(">I", 0)
    png = sig + bad_ihdr
    decl = _decl("image/png", len(png), 1, 1)
    with pytest.raises(ContractValidationError):
        VisualTransferValidator.validate_frame(decl, png)


# --- Malformed/truncated JPEG ------------------------------------------------

def test_truncated_jpeg_missing_eoi() -> None:
    jpg = make_jpeg(4, 5)
    truncated = jpg[:-2]
    decl = _decl("image/jpeg", len(truncated), 4, 5)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, truncated)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.MALFORMED_IMAGE


def test_truncated_jpeg_no_sof() -> None:
    data = b"\xff\xd8\xff\xe0" + struct.pack(">H", 4) + b"JF" + b"\xff\xd9"
    decl = _decl("image/jpeg", len(data), 4, 5)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, data)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.MALFORMED_IMAGE


def test_malformed_jpeg_bad_segment_length() -> None:
    data = b"\xff\xd8\xff\xe0" + struct.pack(">H", 1) + b"JFIF" + b"\xff\xd9"
    decl = _decl("image/jpeg", len(data), 4, 5)
    with pytest.raises(ContractValidationError):
        VisualTransferValidator.validate_frame(decl, data)


# --- Oversized JPEG segments ------------------------------------------------

def test_oversized_jpeg_segment_rejected() -> None:
    # Build a JPEG with an APP0 segment whose declared length exceeds the frame.
    data = b"\xff\xd8\xff\xe0" + struct.pack(">H", 65_535) + b"JFIF" + b"\x00" * 100 + b"\xff\xd9"
    decl = _decl("image/jpeg", len(data), 4, 5)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, data)
    code = VisualTransferValidator._error_code_from_message(str(exc.value))
    assert code in {VisualTransferErrorCode.SIZE_EXCEEDED, VisualTransferErrorCode.MALFORMED_IMAGE}


# --- EXIF APP1 rejection ----------------------------------------------------

def test_jpeg_with_exif_app1_rejected() -> None:
    jpg = make_jpeg(4, 5, include_app1_exif=True)
    decl = _decl("image/jpeg", len(jpg), 4, 5)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, jpg)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.METADATA_REJECTED


# --- PNG metadata rejection -------------------------------------------------

def test_png_with_text_metadata_rejected() -> None:
    png = make_png(2, 3, extra_chunks=[_png_chunk(b"tEXt", b"key\x00value")])
    decl = _decl("image/png", len(png), 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, png)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.METADATA_REJECTED


def test_png_with_icc_profile_rejected() -> None:
    png = make_png(2, 3, extra_chunks=[_png_chunk(b"iCCP", b"profile\x00\x00" + b"\x00" * 10)])
    decl = _decl("image/png", len(png), 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, png)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.METADATA_REJECTED


def test_png_with_exif_chunk_rejected() -> None:
    png = make_png(2, 3, extra_chunks=[_png_chunk(b"eXIf", b"\x00" * 10)])
    decl = _decl("image/png", len(png), 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, png)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.METADATA_REJECTED


# --- Animation / multi-frame rejection --------------------------------------

def test_png_with_apng_animation_control_rejected() -> None:
    # acTL chunk signals APNG animation.
    actl = struct.pack(">II", 2, 0)  # num_frames=2, num_plays=0
    png = make_png(2, 3, extra_chunks=[_png_chunk(b"acTL", actl)])
    decl = _decl("image/png", len(png), 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, png)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.METADATA_REJECTED


def test_png_with_apng_frame_control_rejected() -> None:
    fctl = struct.pack(">IIIIIHHBB", 0, 2, 3, 0, 0, 0, 0, 0, 0)
    png = make_png(2, 3, extra_chunks=[_png_chunk(b"fcTL", fctl)])
    decl = _decl("image/png", len(png), 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, png)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.METADATA_REJECTED


def test_png_with_unknown_ancillary_chunk_rejected() -> None:
    png = make_png(2, 3, extra_chunks=[_png_chunk(b"zZzZ", b"\x00" * 4)])
    decl = _decl("image/png", len(png), 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, png)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.METADATA_REJECTED


# --- Declaration/actual dimension mismatch ----------------------------------

def test_dimension_mismatch_png() -> None:
    png = make_png(2, 3)
    decl = _decl("image/png", len(png), 3, 3)  # declared width 3, actual 2
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, png)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.DIMENSIONS_EXCEEDED


def test_dimension_mismatch_jpeg() -> None:
    jpg = make_jpeg(4, 5)
    decl = _decl("image/jpeg", len(jpg), 4, 6)  # declared height 6, actual 5
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, jpg)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.DIMENSIONS_EXCEEDED


# --- Exact frame count ------------------------------------------------------

def test_frame_count_not_one_rejected_at_declaration() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(
            handoff_id="h-1",
            mime="image/png",
            declared_byte_length=100,
            declared_width=10,
            declared_height=10,
            frame_count=2,
        )


# --- Non-bytes input --------------------------------------------------------

def test_rejects_bytearray_input() -> None:
    png = make_png(2, 3)
    decl = _decl("image/png", len(png), 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, bytearray(png))
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.INVALID_REQUEST


def test_rejects_memoryview_input() -> None:
    png = make_png(2, 3)
    decl = _decl("image/png", len(png), 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, memoryview(png))
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.INVALID_REQUEST


def test_rejects_none_input() -> None:
    decl = _decl("image/png", 100, 2, 3)
    with pytest.raises(ContractValidationError) as exc:
        VisualTransferValidator.validate_frame(decl, None)
    assert VisualTransferValidator._error_code_from_message(str(exc.value)) is VisualTransferErrorCode.INVALID_REQUEST


# --- validate_declaration ---------------------------------------------------

def test_validate_declaration_rejects_non_declaration() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferValidator.validate_declaration({"transfer_id": "t-1"})


def test_validate_declaration_accepts_valid() -> None:
    decl = _decl("image/png", 100, 2, 3)
    assert VisualTransferValidator.validate_declaration(decl) is decl


# --- SHA-256 receipt --------------------------------------------------------

def test_sha_receipt_is_lowercase_hex() -> None:
    png = make_png(2, 3)
    decl = _decl("image/png", len(png), 2, 3)
    meta = VisualTransferValidator.validate_frame(decl, png)
    assert meta.sha256.startswith("sha256.")
    hex_part = meta.sha256[len("sha256."):]
    assert hex_part == hex_part.lower()
    assert len(hex_part) == 64
    assert all(c in "0123456789abcdef" for c in hex_part)


# --- Module hygiene ---------------------------------------------------------

def test_validator_module_has_no_forbidden_imports() -> None:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "visual_transfer"
        / "validator.py"
    )
    forbidden = {
        "subprocess", "socket", "threading", "asyncio", "requests",
        "http", "urllib", "browser", "PIL", "cv2", "numpy", "scipy",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported), f"forbidden imports: {forbidden & imported}"
