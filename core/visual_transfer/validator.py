"""Pure standard-library image validator for a single bounded frame.

Parses PNG IHDR and JPEG SOF dimensions directly from the byte stream without
decoding pixels. Rejects EXIF APP1, animated/multi-frame PNG indicators, and
suspicious unbounded ancillary metadata. Computes a SHA-256 receipt only after
all validation passes. Never returns image bytes.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Optional, Tuple

from core.visual_transfer.contracts import (
    ContractValidationError,
    DECOMPRESSION_PIXEL_LIMIT,
    MAX_DIMENSION,
    MAX_ENCODED_BYTES,
    MIN_DIMENSION,
    ValidatedImageMetadata,
    VisualTransferDeclaration,
    VisualTransferErrorCode,
    validate_mime,
)

# --- Magic bytes ------------------------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC_PREFIX = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"

# PNG chunk type allowlist for the bounded ancillary check. Anything outside
# this set that is not a critical chunk (IHDR/IDAT/IEND) is treated as
# suspicious metadata and rejected.
_PNG_CRITICAL_CHUNKS = frozenset({b"IHDR", b"IDAT", b"IEND"})
_PNG_ANCILLARY_ALLOWLIST = frozenset(
    {
        b"cHRM",  # chromaticity — fixed 32 bytes
        b"gAMA",  # gamma — fixed 4 bytes
        b"sRGB",  # rendering intent — fixed 1 byte
        b"sBIT",  # significant bits — bounded by colour type
        b"pHYs",  # physical pixel dimensions — fixed 9 bytes
        b"tRNS",  # transparency — bounded by colour type
        b"bKGD",  # background — bounded by colour type
    }
)

# PNG ancillary chunks that are explicitly rejected: they carry either
# unbounded metadata, location data, or arbitrary profiles.
_PNG_REJECTED_ANCILLARY = frozenset(
    {
        b"eXIf",  # embedded EXIF — location/profile risk
        b"tEXt",  # arbitrary text metadata — unbounded
        b"zTXt",  # compressed text metadata — unbounded
        b"iTXt",  # international text metadata — unbounded
        b"iCCP",  # ICC profile — arbitrary, unbounded
        b"sTER",  # stereo indicator — multi-frame-adjacent
        b"acTL",  # APNG animation control — multi-frame
        b"fcTL",  # APNG frame control — multi-frame
        b"fdAT",  # APNG frame data — multi-frame
    }
)

# JPEG marker names for diagnostics (kept internal; never surfaced in errors).
_JPEG_SOI = 0xD8
_JPEG_EOI_MARKER = 0xD9
_JPEG_APP1 = 0xE1
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
_JPEG_MAX_SEGMENT_PAYLOAD = 65_535  # max unsigned 16-bit length minus 2.
_JPEG_MAX_SEGMENTS = 256  # bounded cursor to reject cyclic/oversized structures.


class VisualTransferValidator:
    """Validate one declared frame against its actual bytes.

    The validator is stateless and never retains bytes after returning. It
    performs structural validation only; it does not decode pixels.
    """

    @staticmethod
    def validate_declaration(declaration: object) -> VisualTransferDeclaration:
        """Validate the declaration before any buffer is allocated.

        Accepts a ``VisualTransferDeclaration`` (already validated in
        ``__post_init__``) and re-checks type to avoid trusting annotations.
        """
        if not isinstance(declaration, VisualTransferDeclaration):
            raise ContractValidationError("invalid_request")
        return declaration

    @staticmethod
    def validate_frame(
        declaration: VisualTransferDeclaration, frame: object
    ) -> ValidatedImageMetadata:
        """Validate ``frame`` against ``declaration`` and return metadata.

        ``frame`` must be ``bytes`` (not ``bytearray`` or ``memoryview``) at
        this internal API boundary. Raises ``ContractValidationError`` with a
        stable ``VisualTransferErrorCode`` value as the message for any
        rejection. Never returns the image bytes.
        """
        if not isinstance(frame, bytes):
            raise ContractValidationError(VisualTransferErrorCode.INVALID_REQUEST.value)
        if len(frame) < MIN_DIMENSION or len(frame) > MAX_ENCODED_BYTES:
            raise ContractValidationError(VisualTransferErrorCode.SIZE_EXCEEDED.value)
        if len(frame) != declaration.declared_byte_length:
            raise ContractValidationError(VisualTransferErrorCode.SIZE_EXCEEDED.value)

        actual_mime = VisualTransferValidator._detect_mime(frame)
        if actual_mime is None:
            raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
        if actual_mime != declaration.mime:
            raise ContractValidationError(VisualTransferErrorCode.MIME_MISMATCH.value)

        if actual_mime == "image/png":
            width, height = VisualTransferValidator._parse_png_dimensions(frame)
        else:
            width, height = VisualTransferValidator._parse_jpeg_dimensions(frame)

        if width != declaration.declared_width or height != declaration.declared_height:
            raise ContractValidationError(VisualTransferErrorCode.DIMENSIONS_EXCEEDED.value)
        if width * height > DECOMPRESSION_PIXEL_LIMIT:
            raise ContractValidationError(VisualTransferErrorCode.DECOMPRESSION_LIMIT.value)

        digest = "sha256." + hashlib.sha256(frame).hexdigest()
        return ValidatedImageMetadata(
            mime=actual_mime, width=width, height=height, sha256=digest
        )

    # --- Error code mapping ------------------------------------------------

    @staticmethod
    def _error_code_from_message(message: str) -> VisualTransferErrorCode:
        """Map a ContractValidationError message to a stable error code.

        ContractValidationError messages are always one of the
        VisualTransferErrorCode values or a contract reason string. When the
        message is a known error code, return it directly; otherwise default
        to MALFORMED_IMAGE for parse failures and INVALID_REQUEST for
        declaration issues.
        """
        for code in VisualTransferErrorCode:
            if code.value == message:
                return code
        if message in {"declared_byte_length is invalid", "frame_count_invalid"}:
            return VisualTransferErrorCode.INVALID_REQUEST
        if message == "size_exceeded":
            return VisualTransferErrorCode.SIZE_EXCEEDED
        if message == "decompression_limit":
            return VisualTransferErrorCode.DECOMPRESSION_LIMIT
        if message == "mime_unsupported":
            return VisualTransferErrorCode.MIME_UNSUPPORTED
        return VisualTransferErrorCode.MALFORMED_IMAGE

    # --- MIME detection ----------------------------------------------------

    @staticmethod
    def _detect_mime(frame: bytes) -> Optional[str]:
        if frame.startswith(_PNG_MAGIC):
            return "image/png"
        if frame[:2] == _JPEG_MAGIC_PREFIX:
            return "image/jpeg"
        return None

    # --- PNG parsing -------------------------------------------------------

    @staticmethod
    def _parse_png_dimensions(frame: bytes) -> Tuple[int, int]:
        """Parse IHDR width/height and reject animation/metadata indicators."""
        if len(frame) < 24:  # 8 magic + 4 length + 4 type + 13 IHDR + 4 CRC minimum
            raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
        # IHDR is the first chunk; its length field must be exactly 13.
        ihdr_length = struct.unpack(">I", frame[8:12])[0]
        ihdr_type = frame[12:16]
        if ihdr_type != b"IHDR" or ihdr_length != 13:
            raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
        width, height = struct.unpack(">II", frame[16:24])
        if width < MIN_DIMENSION or width > MAX_DIMENSION:
            raise ContractValidationError(VisualTransferErrorCode.DIMENSIONS_EXCEEDED.value)
        if height < MIN_DIMENSION or height > MAX_DIMENSION:
            raise ContractValidationError(VisualTransferErrorCode.DIMENSIONS_EXCEEDED.value)

        # Walk remaining chunks with a bounded cursor.
        # IHDR chunk layout: [length:4][type:4][data:13][crc:4] starting at
        # offset 8 (after the 8-byte magic). Next chunk begins at 8 + 4 + 4 + 13 + 4 = 33.
        cursor = 8 + 4 + 4 + ihdr_length + 4
        seen_idat = False
        seen_iend = False
        while cursor + 8 <= len(frame):
            chunk_length = struct.unpack(">I", frame[cursor:cursor + 4])[0]
            chunk_type = frame[cursor + 4:cursor + 8]
            # Reject chunks whose declared length would overrun the buffer.
            if cursor + 8 + chunk_length + 4 > len(frame):
                raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
            # Reject oversized ancillary payloads even within a 1 MiB frame.
            if chunk_length > MAX_ENCODED_BYTES:
                raise ContractValidationError(VisualTransferErrorCode.METADATA_REJECTED.value)
            if chunk_type in _PNG_REJECTED_ANCILLARY:
                raise ContractValidationError(VisualTransferErrorCode.METADATA_REJECTED.value)
            if chunk_type == b"IDAT":
                seen_idat = True
            elif chunk_type == b"IEND":
                seen_iend = True
                # IEND must be the final chunk; trailing bytes are malformed.
                if cursor + 8 + chunk_length + 4 != len(frame):
                    raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
            elif chunk_type not in _PNG_CRITICAL_CHUNKS and chunk_type not in _PNG_ANCILLARY_ALLOWLIST:
                # Unknown ancillary chunk — reject as unbounded metadata.
                raise ContractValidationError(VisualTransferErrorCode.METADATA_REJECTED.value)
            cursor += 8 + chunk_length + 4
        if not seen_idat or not seen_iend:
            raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
        return width, height

    # --- JPEG parsing ------------------------------------------------------

    @staticmethod
    def _parse_jpeg_dimensions(frame: bytes) -> Tuple[int, int]:
        """Walk JPEG segments with a bounded cursor and return SOF dimensions."""
        if len(frame) < 4 or frame[:2] != _JPEG_MAGIC_PREFIX:
            raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
        if frame[-2:] != _JPEG_EOI:
            raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)

        cursor = 2
        segment_count = 0
        width = height = None
        while cursor + 4 <= len(frame):
            segment_count += 1
            if segment_count > _JPEG_MAX_SEGMENTS:
                raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
            if frame[cursor] != 0xFF:
                raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
            marker = frame[cursor + 1]
            # Standalone markers (no length payload): SOI, EOI, RSTn.
            if marker == _JPEG_EOI_MARKER:
                break
            if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                cursor += 2
                continue
            # All other markers carry a 2-byte length (including the length field).
            if cursor + 4 > len(frame):
                raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
            segment_length = struct.unpack(">H", frame[cursor + 2:cursor + 4])[0]
            if segment_length < 2:
                raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
            payload_end = cursor + 2 + segment_length
            if payload_end > len(frame):
                raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
            # Reject oversized segment payloads.
            if segment_length - 2 > _JPEG_MAX_SEGMENT_PAYLOAD:
                raise ContractValidationError(VisualTransferErrorCode.SIZE_EXCEEDED.value)
            # Reject EXIF APP1 explicitly.
            if marker == _JPEG_APP1:
                raise ContractValidationError(VisualTransferErrorCode.METADATA_REJECTED.value)
            # SOF markers carry dimensions: [precision(1), height(2), width(2), ...].
            if marker in _JPEG_SOF_MARKERS:
                if segment_length < 7:
                    raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
                sof_height, sof_width = struct.unpack(
                    ">HH", frame[cursor + 5:cursor + 9]
                )
                if sof_width < MIN_DIMENSION or sof_width > MAX_DIMENSION:
                    raise ContractValidationError(VisualTransferErrorCode.DIMENSIONS_EXCEEDED.value)
                if sof_height < MIN_DIMENSION or sof_height > MAX_DIMENSION:
                    raise ContractValidationError(VisualTransferErrorCode.DIMENSIONS_EXCEEDED.value)
                width, height = sof_width, sof_height
            cursor = payload_end
        if width is None or height is None:
            raise ContractValidationError(VisualTransferErrorCode.MALFORMED_IMAGE.value)
        return width, height


def validate_mime_public(value: object) -> str:
    """Public shim re-exported for tests; delegates to contracts.validate_mime."""
    return validate_mime(value)
