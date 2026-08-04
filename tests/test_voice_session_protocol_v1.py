import asyncio

import pytest

from core.voice_session.protocol_v1 import (
    VOICE_EVENT_SEQ_MAX,
    VoiceProtocolEmitter,
    build_voice_protocol_event,
)


def test_backend_envelope_matches_frontend_v1_snake_case_contract() -> None:
    envelope = build_voice_protocol_event(
        event_type="assistant_speaking",
        session_id="session_1",
        event_seq=4,
        response_id="response_1",
        playback_id="playback_1",
    )
    assert envelope.to_dict() == {
        "protocol_version": 1,
        "type": "assistant_speaking",
        "session_id": "session_1",
        "event_seq": 4,
        "response_id": "response_1",
        "playback_id": "playback_1",
    }


def test_backend_contract_rejects_content_and_unsafe_sequence_values() -> None:
    with pytest.raises(TypeError):
        build_voice_protocol_event(  # type: ignore[call-arg]
            event_type="partial_caption",
            session_id="session_1",
            event_seq=1,
            utterance_id="utterance_1",
            caption_len=3,
            transcript="secret",
        )
    with pytest.raises(ValueError, match="correlation"):
        build_voice_protocol_event(
            event_type="enter_idle",
            session_id="session_1",
            event_seq=VOICE_EVENT_SEQ_MAX + 1,
        )


class RecordingSink:
    def __init__(self, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.payloads: list[dict[str, object]] = []

    async def send(self, payload) -> None:
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("synthetic sink failure")
        self.payloads.append(dict(payload))


def test_protocol_emitter_serializes_delivery_and_latches_on_sink_failure() -> None:
    async def scenario() -> None:
        sink = RecordingSink(fail_first=True)
        emitter = VoiceProtocolEmitter(sink, session_id="session_1")
        first = await emitter.emit(
            lambda seq, sid: build_voice_protocol_event(
                event_type="enter_idle", session_id=sid, event_seq=seq
            )
        )
        second = await emitter.emit(
            lambda seq, sid: build_voice_protocol_event(
                event_type="wake_detected", session_id=sid, event_seq=seq
            )
        )
        assert first is None and second is None
        assert emitter.current_seq == 0
        assert sink.payloads == []

    asyncio.run(scenario())
