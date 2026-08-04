"""Frozen content-free backend contract matching the frontend voice reducer v1.

The protocol deliberately excludes transcript and assistant-response text.  It
has an independent serialized sequence allocator because internal coordinator
events are not one-to-one with browser events.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Protocol

VOICE_PROTOCOL_VERSION = 1
VOICE_EVENT_SEQ_MAX = (2**53) - 2
VOICE_CAPTION_MAX = 240
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_CODE_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")

_NO_ID_EVENTS = frozenset(
    {
        "session_start",
        "session_reset",
        "wake_detected",
        "await_command",
        "enter_idle",
        "enter_asleep",
        "degraded_half_duplex",
        "clear_caption",
    }
)
_UTTERANCE_EVENTS = frozenset(
    {"listening_started", "user_speech_activity", "final_transcript_ready", "submit_turn"}
)
_RESPONSE_PLAYBACK_EVENTS = frozenset(
    {"assistant_speaking", "barge_in", "cancel_assistant"}
)
_RESPONSE_EVENTS = frozenset({"assistant_cancelled", "interrupted_ack"})
_ERROR_EVENTS = frozenset({"microphone_error", "aec_error"})
_VOICE_STATES = frozenset(
    {
        "asleep",
        "wake_detected",
        "awaiting_command",
        "listening",
        "user_speaking",
        "processing_final",
        "assistant_generating",
        "assistant_speaking",
        "barge_in_pending",
        "cancelling_assistant",
        "interrupted",
        "cancelled",
        "degraded_half_duplex",
        "microphone_error",
        "aec_error",
        "idle",
    }
)


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


def _valid_seq(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= VOICE_EVENT_SEQ_MAX
    )


@dataclass(frozen=True, repr=False)
class VoiceProtocolEnvelope:
    payload: Mapping[str, object]

    def __repr__(self) -> str:
        return (
            "VoiceProtocolEnvelope("
            f"type={self.payload.get('type')!r}, event_seq={self.payload.get('event_seq')!r})"
        )

    def to_dict(self) -> dict[str, object]:
        return dict(self.payload)


def build_voice_protocol_event(
    *,
    event_type: str,
    session_id: str,
    event_seq: int,
    utterance_id: str = "",
    response_id: str = "",
    playback_id: str = "",
    caption_len: Optional[int] = None,
    code: str = "",
    snapshot: Optional[Mapping[str, object]] = None,
) -> VoiceProtocolEnvelope:
    if not _valid_id(session_id) or not _valid_seq(event_seq):
        raise ValueError("invalid_voice_protocol_correlation")
    payload: dict[str, object] = {
        "protocol_version": VOICE_PROTOCOL_VERSION,
        "type": event_type,
        "session_id": session_id,
        "event_seq": event_seq,
    }
    if event_type in _NO_ID_EVENTS:
        if any((utterance_id, response_id, playback_id, code)) or caption_len is not None or snapshot:
            raise ValueError("unexpected_voice_protocol_fields")
    elif event_type in _UTTERANCE_EVENTS:
        if not _valid_id(utterance_id) or any((response_id, playback_id, code)) or caption_len is not None or snapshot:
            raise ValueError("invalid_voice_protocol_fields")
        payload["utterance_id"] = utterance_id
    elif event_type == "partial_caption":
        if not _valid_id(utterance_id):
            raise ValueError("invalid_utterance_id")
        if (
            isinstance(caption_len, bool)
            or not isinstance(caption_len, int)
            or not 0 <= caption_len <= VOICE_CAPTION_MAX
        ):
            raise ValueError("invalid_caption_len")
        if any((response_id, playback_id, code)) or snapshot:
            raise ValueError("unexpected_voice_protocol_fields")
        payload.update({"utterance_id": utterance_id, "caption_len": caption_len})
    elif event_type == "assistant_generating":
        if not _valid_id(utterance_id) or not _valid_id(response_id):
            raise ValueError("invalid_voice_protocol_fields")
        if playback_id or code or caption_len is not None or snapshot:
            raise ValueError("unexpected_voice_protocol_fields")
        payload.update({"utterance_id": utterance_id, "response_id": response_id})
    elif event_type in _RESPONSE_PLAYBACK_EVENTS:
        if not _valid_id(response_id) or not _valid_id(playback_id):
            raise ValueError("invalid_voice_protocol_fields")
        if utterance_id or code or caption_len is not None or snapshot:
            raise ValueError("unexpected_voice_protocol_fields")
        payload.update({"response_id": response_id, "playback_id": playback_id})
    elif event_type in _RESPONSE_EVENTS:
        if not _valid_id(response_id):
            raise ValueError("invalid_response_id")
        if utterance_id or playback_id or code or caption_len is not None or snapshot:
            raise ValueError("unexpected_voice_protocol_fields")
        payload["response_id"] = response_id
    elif event_type in _ERROR_EVENTS:
        if code and _CODE_RE.fullmatch(code) is None:
            raise ValueError("invalid_error_code")
        if utterance_id or response_id or playback_id or caption_len is not None or snapshot:
            raise ValueError("unexpected_voice_protocol_fields")
        if code:
            payload["code"] = code
    elif event_type == "session_snapshot":
        if snapshot is None:
            raise ValueError("missing_session_snapshot")
        expected = {
            "name",
            "utterance_id",
            "response_id",
            "playback_id",
            "degraded_half_duplex",
            "push_to_talk_fallback",
            "last_error_code",
        }
        if set(snapshot) != expected:
            raise ValueError("invalid_session_snapshot_fields")
        if snapshot["name"] not in _VOICE_STATES:
            raise ValueError("invalid_session_snapshot_state")
        for field in ("utterance_id", "response_id", "playback_id"):
            value = snapshot[field]
            if value != "" and not _valid_id(value):
                raise ValueError("invalid_session_snapshot_correlation")
        for field in ("degraded_half_duplex", "push_to_talk_fallback"):
            if not isinstance(snapshot[field], bool):
                raise ValueError("invalid_session_snapshot_flag")
        error_code = snapshot["last_error_code"]
        if error_code != "" and (
            not isinstance(error_code, str) or _CODE_RE.fullmatch(error_code) is None
        ):
            raise ValueError("invalid_session_snapshot_error")
        if any((utterance_id, response_id, playback_id, code)) or caption_len is not None:
            raise ValueError("unexpected_voice_protocol_fields")
        payload["snapshot"] = dict(snapshot)
    else:
        raise ValueError("unknown_voice_protocol_type")
    return VoiceProtocolEnvelope(payload)


class VoiceProtocolSink(Protocol):
    async def send(self, payload: Mapping[str, object]) -> None: ...


class VoiceProtocolEmitter:
    """Serialize browser event allocation and delivery without sequence gaps."""

    def __init__(self, sink: VoiceProtocolSink, *, session_id: str) -> None:
        if not _valid_id(session_id):
            raise ValueError("invalid_session_id")
        self._sink = sink
        self._session_id = session_id
        self._seq = 0
        self._lock = asyncio.Lock()
        self._failed = False

    @property
    def current_seq(self) -> int:
        return self._seq

    async def emit(
        self, factory: Callable[[int, str], VoiceProtocolEnvelope]
    ) -> Optional[VoiceProtocolEnvelope]:
        async with self._lock:
            if self._failed or self._seq >= VOICE_EVENT_SEQ_MAX:
                return None
            candidate = self._seq + 1
            envelope = factory(candidate, self._session_id)
            try:
                await self._sink.send(envelope.payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._failed = True
                return None
            self._seq = candidate
            return envelope


__all__ = [
    "VOICE_CAPTION_MAX",
    "VOICE_EVENT_SEQ_MAX",
    "VOICE_PROTOCOL_VERSION",
    "VoiceProtocolEmitter",
    "VoiceProtocolEnvelope",
    "VoiceProtocolSink",
    "build_voice_protocol_event",
]
