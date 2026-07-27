"""Adversarial framing and capture-adapter tests (synthetic only)."""

from __future__ import annotations

import struct
import time
import math
from pathlib import Path

import pytest

from core.voice_capture.contracts import CaptureMessageType
from core.voice_capture.config import VoiceCaptureConfig
from core.voice_capture.framing import HEADER_SIZE, MAX_PAYLOAD, decode_frame, encode_frame
from core.voice_capture.endpointing import EndpointEvent, UtteranceEndpointGate
from core.voice_capture.playback import PlaybackController
from core.voice_capture.transcript_session import BoundedTranscriptSession, TranscriptSessionReason
from core.voice_capture.vad_backend import (
    EnergyFallbackVadBackend,
    UnavailableVadBackend,
    VadBackendReason,
    VadProbabilityResult,
    create_vad_backend,
)
from core.voice_streaming.live_audio import AudioFrameSourceReason, try_create_production_frame_source
from core.voice_streaming.runtime import speech_interrupt_mode


def test_framing_round_trip():
    payload = b"ready-json"
    frame = encode_frame(CaptureMessageType.READY, sequence=3, monotonic_ns=99, payload=payload)
    decoded = decode_frame(frame)
    assert decoded is not None
    assert decoded.message_type == CaptureMessageType.READY
    assert decoded.sequence == 3
    assert decoded.payload == payload


def test_invalid_magic_rejected():
    frame = encode_frame(CaptureMessageType.PCM, sequence=1, monotonic_ns=1, payload=b"\x00\x01")
    bad = b"XXXX" + frame[4:]
    assert decode_frame(bad) is None


def test_reserved_header_fields_and_invalid_format_rejected():
    frame = bytearray(
        encode_frame(CaptureMessageType.PCM, sequence=1, monotonic_ns=1, payload=b"\x00\x01")
    )
    frame[36:40] = (1).to_bytes(4, "little")
    assert decode_frame(bytes(frame)) is None
    with pytest.raises(ValueError, match="invalid_audio_format"):
        encode_frame(
            CaptureMessageType.PCM,
            sequence=1,
            monotonic_ns=1,
            payload=b"\x00\x01",
            channels=2,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"handshake_timeout_s": float("nan")},
        {"shutdown_timeout_s": True},
        {"max_queue_depth": 65},
        {"sample_rate": 8_000},
        {"future_skew_ns": 31_000_000_000},
    ],
)
def test_capture_config_rejects_soft_or_unsafe_values(kwargs):
    with pytest.raises(ValueError):
        VoiceCaptureConfig(**kwargs)


def test_oversized_payload_rejected():
    with pytest.raises(ValueError):
        encode_frame(CaptureMessageType.PCM, sequence=1, monotonic_ns=1, payload=b"x" * (MAX_PAYLOAD + 1))


def test_truncated_frame_rejected():
    frame = encode_frame(CaptureMessageType.PCM, sequence=1, monotonic_ns=1, payload=b"\x00\x01")
    assert decode_frame(frame[:-1]) is None


def test_import_voice_capture_does_not_open_mic():
    import core.voice_capture as vc

    assert vc.CAPTURE_BACKEND_MACOS_COREAUDIO == "macos-coreaudio"


def test_production_source_default_unavailable():
    src = try_create_production_frame_source(stream_id="s1", capture_backend="utterance-only")
    assert src.capability.value == "unavailable"


def test_endpoint_gate_unavailable_backend_fails_closed():
    gate = UtteranceEndpointGate(stream_id="s1", backend=UnavailableVadBackend())
    tick = gate.process_frame(b"\x00\x00" * 160, monotonic_ns=1)
    assert tick.available is False
    assert tick.event == EndpointEvent.FAILED


def test_endpoint_gate_speech_start_and_finalize():
    backend = EnergyFallbackVadBackend(threshold=0.01)
    gate = UtteranceEndpointGate(stream_id="s1", backend=backend, hangover_ms=60.0)
    # Loud frames
    loud = (b"\x00\x40" * 320)  # noticeable amplitude
    silent = (b"\x00\x00" * 320)
    saw_start = False
    finalized = False
    now = 1_000_000_000
    bytes_supplied = 0
    for i in range(12):
        tick = gate.process_frame(loud, monotonic_ns=now + i * 20_000_000, frame_duration_ms=20.0)
        bytes_supplied += len(loud)
        if tick.event == EndpointEvent.SPEECH_START:
            saw_start = True
    for i in range(20):
        tick = gate.process_frame(silent, monotonic_ns=now + (20 + i) * 20_000_000, frame_duration_ms=20.0)
        bytes_supplied += len(silent)
        if tick.event == EndpointEvent.FINALIZED:
            finalized = True
            assert len(tick.utterance_pcm) > 0
            assert len(tick.utterance_pcm) <= bytes_supplied
            break
    assert saw_start
    assert finalized


def test_endpoint_energy_silence_can_release_stuck_vad():
    class StuckVad:
        available = True

        def reset(self):
            return None

        def process_pcm16_mono(self, pcm, *, sample_rate=16_000):
            return VadProbabilityResult(True, VadBackendReason.OK, 0.9)

    gate = UtteranceEndpointGate(
        stream_id="s1",
        backend=StuckVad(),
        hangover_ms=64.0,
    )
    loud = b"\xff\x3f" * 512
    silent = b"\x00\x00" * 512
    now = 1_000_000_000
    assert gate.process_frame(loud, monotonic_ns=now).event == EndpointEvent.NONE
    assert gate.process_frame(loud, monotonic_ns=now + 32_000_000).event == EndpointEvent.SPEECH_START
    assert gate.process_frame(silent, monotonic_ns=now + 64_000_000).event != EndpointEvent.FINALIZED
    assert gate.process_frame(silent, monotonic_ns=now + 96_000_000).event == EndpointEvent.FINALIZED


def test_transcript_partial_never_final_authority():
    class Fake:
        def transcribe_pcm16(self, pcm, *, sample_rate=16000, short_utterance=False):
            return "hello world", 0.9

    session = BoundedTranscriptSession(
        stream_id="s",
        utterance_id="u1",
        transcriber=Fake(),
        partials_supported=True,
    )
    partial = session.on_partial("hel")
    assert partial is not None and partial.is_final is False
    final = session.finalize_pcm(b"\x00\x00" * 800)
    assert final.is_final and final.reason == TranscriptSessionReason.OK
    dup = session.finalize_pcm(b"\x00\x00" * 800)
    assert dup.reason == TranscriptSessionReason.DUPLICATE_FINAL


def test_transcript_filler_and_low_confidence():
    class Fake:
        def __init__(self, text, conf):
            self.text, self.conf = text, conf

        def transcribe_pcm16(self, pcm, *, sample_rate=16000, short_utterance=False):
            return self.text, self.conf

    s1 = BoundedTranscriptSession(stream_id="s", utterance_id="u", transcriber=Fake("um", 0.9))
    assert s1.finalize_pcm(b"\x00\x00" * 800).reason == TranscriptSessionReason.FILLER
    s2 = BoundedTranscriptSession(stream_id="s", utterance_id="u", transcriber=Fake("maybe time", 0.1))
    ev = s2.finalize_pcm(b"\x00\x00" * 800)
    assert ev.ask_clarification is True
    assert ev.reason == TranscriptSessionReason.LOW_CONFIDENCE


def test_transcript_rejects_nan_confidence_and_is_terminal():
    class Fake:
        def transcribe_pcm16(self, pcm, *, sample_rate=16000, short_utterance=False):
            return "unsafe", math.nan

    session = BoundedTranscriptSession(stream_id="s", utterance_id="u", transcriber=Fake())
    first = session.finalize_pcm(b"\x00\x00" * 800)
    assert first.reason == TranscriptSessionReason.UNAVAILABLE
    assert session.finalize_pcm(b"\x00\x00" * 800).reason == TranscriptSessionReason.DUPLICATE_FINAL


def test_playback_controller_requires_physical_stop():
    class Backend:
        def __init__(self):
            self.alive = True

        def pause(self):
            pass

        def cancel(self):
            self.alive = False

        def is_alive(self):
            return self.alive

    ctl = PlaybackController(now_ns=lambda: 100)
    ctl.start(playback_id="p1", response_id="r1", backend=Backend())
    assert ctl.pause_for_barge() is True
    assert ctl.stop_confirmed is False
    assert ctl.cancel() is True
    assert ctl.stop_confirmed is False
    assert ctl.notify_physically_stopped() is True
    assert ctl.stop_confirmed is True


def test_playback_controller_refuses_false_physical_stop():
    class Backend:
        def pause(self):
            return None

        def cancel(self):
            return None

        def is_alive(self):
            return True

    ctl = PlaybackController(now_ns=lambda: 100)
    ctl.start(playback_id="p1", response_id="r1", backend=Backend())
    assert ctl.notify_physically_stopped() is False
    assert ctl.stop_confirmed is False


def test_speech_interrupt_modes():
    assert speech_interrupt_mode("stop") == "stop"
    assert speech_interrupt_mode("be quiet") == "stop"
    assert speech_interrupt_mode("cancel") == "cancel"
    assert speech_interrupt_mode("goodbye") == "goodbye"
    assert speech_interrupt_mode("do not stop the timer") is None


def test_vad_create_unavailable_without_model():
    backend = create_vad_backend(model_path="/nonexistent/silero.onnx", allow_energy_fallback=False)
    assert backend.available is False


def test_probe_does_not_claim_microphone_open():
    from core.voice_capture.capability import probe_macos_coreaudio_capability

    cap = probe_macos_coreaudio_capability()
    assert cap.opens_microphone is False
