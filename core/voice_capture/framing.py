"""HIKA binary framing codec (Python side of native helper protocol)."""

from __future__ import annotations

import struct
from typing import Optional, Tuple

from core.voice_capture.contracts import CaptureMessageType, DecodedCaptureFrame

MAGIC = b"HIKA"
VERSION = 1
HEADER_SIZE = 48
MAX_PAYLOAD = 65_536

_TYPE_MAP = {
    1: CaptureMessageType.READY,
    2: CaptureMessageType.PCM,
    3: CaptureMessageType.ERROR,
    4: CaptureMessageType.END,
    5: CaptureMessageType.CANCEL_ACK,
}
_TYPE_TO_INT = {v: k for k, v in _TYPE_MAP.items()}

# magic(4) ver(u16) type(u16) seq(u64) mono(u64) rate(u32) ch(u16) width(u16)
# plen(u32) reserved0(u32) reserved1(u32) reserved2(u32) => 48
_HEADER_STRUCT = struct.Struct("<4sHHQQIHHIIII")
assert _HEADER_STRUCT.size == HEADER_SIZE


def encode_frame(
    message_type: CaptureMessageType,
    *,
    sequence: int,
    monotonic_ns: int,
    payload: bytes = b"",
    sample_rate: int = 16_000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    if not isinstance(message_type, CaptureMessageType):
        raise ValueError("invalid_message_type")
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload_must_be_bytes")
    payload_b = bytes(payload)
    if len(payload_b) > MAX_PAYLOAD:
        raise ValueError("payload_too_large")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or sequence > (2**64 - 1)
        or isinstance(monotonic_ns, bool)
        or not isinstance(monotonic_ns, int)
        or monotonic_ns < 0
        or monotonic_ns > (2**64 - 1)
    ):
        raise ValueError("invalid_numeric")
    if sample_rate != 16_000 or channels != 1 or sample_width != 2:
        raise ValueError("invalid_audio_format")
    if message_type == CaptureMessageType.PCM and (
        not payload_b or len(payload_b) % (channels * sample_width) != 0
    ):
        raise ValueError("invalid_pcm_payload")
    if message_type in {CaptureMessageType.END, CaptureMessageType.CANCEL_ACK} and payload_b:
        raise ValueError("unexpected_payload")
    if message_type == CaptureMessageType.ERROR and len(payload_b) > 64:
        raise ValueError("error_payload_too_large")
    if message_type == CaptureMessageType.READY and len(payload_b) > 1024:
        raise ValueError("ready_payload_too_large")
    type_int = _TYPE_TO_INT[message_type]
    header = _HEADER_STRUCT.pack(
        MAGIC,
        VERSION,
        type_int,
        sequence,
        monotonic_ns,
        sample_rate,
        channels,
        sample_width,
        len(payload_b),
        0,
        0,
        0,
    )
    assert len(header) == HEADER_SIZE
    return header + payload_b


def decode_header(data: bytes) -> Optional[Tuple[CaptureMessageType, int, int, int, int, int, int]]:
    if not isinstance(data, bytes) or len(data) < HEADER_SIZE:
        return None
    (
        magic,
        version,
        type_int,
        sequence,
        monotonic_ns,
        sample_rate,
        channels,
        sample_width,
        plen,
        r0,
        r1,
        r2,
    ) = _HEADER_STRUCT.unpack_from(data, 0)
    if magic != MAGIC or version != VERSION or r0 != 0 or r1 != 0 or r2 != 0:
        return None
    message_type = _TYPE_MAP.get(type_int)
    if message_type is None:
        return None
    if plen > MAX_PAYLOAD:
        return None
    if sample_rate != 16_000 or channels != 1 or sample_width != 2:
        return None
    return message_type, sequence, monotonic_ns, sample_rate, channels, sample_width, plen


def decode_frame(data: bytes) -> Optional[DecodedCaptureFrame]:
    parsed = decode_header(data)
    if parsed is None:
        return None
    message_type, sequence, monotonic_ns, sample_rate, channels, sample_width, plen = parsed
    if len(data) != HEADER_SIZE + plen:
        return None
    payload = data[HEADER_SIZE : HEADER_SIZE + plen]
    if message_type == CaptureMessageType.PCM and (
        not payload or len(payload) % (channels * sample_width) != 0
    ):
        return None
    if message_type in {CaptureMessageType.END, CaptureMessageType.CANCEL_ACK} and payload:
        return None
    if message_type == CaptureMessageType.ERROR and len(payload) > 64:
        return None
    if message_type == CaptureMessageType.READY and len(payload) > 1024:
        return None
    return DecodedCaptureFrame(
        message_type=message_type,
        sequence=sequence,
        monotonic_ns=monotonic_ns,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
        payload=payload,
    )
