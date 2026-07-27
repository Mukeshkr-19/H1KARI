"""Adversarial tests for live audio frame source and runtime ingest."""

from __future__ import annotations

import math

import pytest

from core.voice_streaming.aec_evidence import AecEvidenceGate, PlatformAecEvidence
from core.voice_streaming.interruption_evidence import (
    InterruptionEvidence,
    InterruptionVerificationSource,
)
from core.voice_streaming.live_audio import (
    AudioFrameSourceReason,
    AudioInputCapability,
    CaptureSourceCategory,
    LiveAudioFrame,
    SyntheticAudioFrameSource,
    UnavailableAudioFrameSource,
    VoiceAudioLoop,
    VoiceAudioLoopConfig,
    try_create_pyaudio_source,
)
from core.voice_streaming.runtime import VoiceStreamingRuntime
from core.voice_streaming.contracts import VoiceStreamState


def _pcm(n: int = 640) -> bytes:
    return b"\xff" * n


def _frame(stream="s1", fid="f0", seq=0, ns=1000, pcm=None):
    return LiveAudioFrame(
        stream_id=stream,
        frame_id=fid,
        sequence=seq,
        monotonic_ns=ns,
        sample_rate=16000,
        channels=1,
        sample_width=2,
        pcm=pcm if pcm is not None else _pcm(),
        capture_source=CaptureSourceCategory.SYNTHETIC,
    )


def _evidence(runtime, *, interruption_id="r1", speech_ns=None, **kwargs):
    now = runtime.now_ns()
    speech = speech_ns if speech_ns is not None else now
    base = dict(
        stream_id=runtime.stream_id,
        interruption_id=interruption_id,
        target_assistant_utterance_id=runtime.interruption_target_id(),
        speaker_verified=True,
        verification_source=InterruptionVerificationSource.SPEAKER_AUTH,
        speech_observed_ns=speech,
        observed_at_ns=now,
        expires_at_ns=now + 2_000_000_000,
    )
    base.update(kwargs)
    return InterruptionEvidence(**base)


def test_default_source_unavailable_no_mic():
    src = UnavailableAudioFrameSource()
    assert src.capability == AudioInputCapability.UNAVAILABLE
    assert src.open().reason == AudioFrameSourceReason.UNAVAILABLE


def test_config_rejects_bool_zero_negative_huge_nan_inf_and_relations():
    with pytest.raises(ValueError):
        VoiceAudioLoopConfig(max_queue_depth=True)
    with pytest.raises(ValueError):
        VoiceAudioLoopConfig(max_queue_depth=0)
    with pytest.raises(ValueError):
        VoiceAudioLoopConfig(max_queue_depth=-1)
    with pytest.raises(ValueError):
        VoiceAudioLoopConfig(max_queue_depth=10_000)
    with pytest.raises(ValueError):
        VoiceAudioLoopConfig(future_skew_ns=float("nan"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        VoiceAudioLoopConfig(stale_skew_ns=float("inf"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        VoiceAudioLoopConfig(max_frame_bytes=10_000, max_buffered_bytes=100)
    with pytest.raises(ValueError):
        VoiceAudioLoopConfig(max_queue_depth=100, max_seen_frame_ids=10)
    with pytest.raises(ValueError):
        VoiceAudioLoopConfig(future_skew_ns=5_000, stale_skew_ns=1_000)


def test_rejects_bool_negative_future_stale_duplicate_cross_stream():
    with pytest.raises(ValueError):
        LiveAudioFrame("s", "f", True, 1, 16000, 1, 2, _pcm())
    with pytest.raises(ValueError):
        _frame(ns=-1)
    loop = VoiceAudioLoop("s1", clock=lambda: 10_000, config=VoiceAudioLoopConfig())
    loop._open = True
    assert loop.enqueue_injected(_frame(stream="other")).reason == AudioFrameSourceReason.CROSS_STREAM
    assert loop.enqueue_injected(_frame(fid="a", seq=0, ns=1000)).accepted
    assert loop.enqueue_injected(_frame(fid="a", seq=1, ns=2000)).reason == AudioFrameSourceReason.DUPLICATE_FRAME
    assert loop.enqueue_injected(_frame(fid="b", seq=0, ns=3000)).reason == AudioFrameSourceReason.OUT_OF_ORDER
    assert loop.enqueue_injected(_frame(fid="c", seq=2, ns=10**15)).reason == AudioFrameSourceReason.FUTURE_TIMESTAMP


def test_first_frame_stale_and_increasing_but_stale_rejected():
    # now=1e12; stale_skew default 30s; frame at ns=1 is far behind.
    loop = VoiceAudioLoop("s1", clock=lambda: 1_000_000_000_000, config=VoiceAudioLoopConfig())
    loop._open = True
    first = loop.enqueue_injected(_frame(fid="old", seq=0, ns=1))
    assert first.reason == AudioFrameSourceReason.STALE_TIMESTAMP
    assert loop.snapshot().frames_accepted == 0
    # Increasing but still stale relative to now.
    second = loop.enqueue_injected(_frame(fid="old2", seq=1, ns=100))
    assert second.reason == AudioFrameSourceReason.STALE_TIMESTAMP
    assert loop.snapshot().frames_accepted == 0


def test_rejection_does_not_advance_sequence_state():
    loop = VoiceAudioLoop("s1", clock=lambda: 50_000, config=VoiceAudioLoopConfig())
    loop._open = True
    assert loop.enqueue_injected(_frame(fid="ok", seq=0, ns=40_000)).accepted
    bad = loop.enqueue_injected(_frame(fid="dup", seq=0, ns=41_000))
    assert bad.reason == AudioFrameSourceReason.OUT_OF_ORDER
    # Next valid increasing seq still works; duplicate id of rejected never recorded.
    assert loop.enqueue_injected(_frame(fid="dup", seq=1, ns=42_000)).accepted


def test_replay_exhaustion_blocks_old_replay_without_clearing():
    cfg = VoiceAudioLoopConfig(max_seen_frame_ids=2, max_queue_depth=2)
    loop = VoiceAudioLoop("s1", clock=lambda: 50_000, config=cfg)
    loop._open = True
    assert loop.enqueue_injected(_frame(fid="a", seq=0, ns=10_000)).accepted
    assert loop.enqueue_injected(_frame(fid="b", seq=1, ns=11_000)).accepted
    # Capacity reached -> terminal exhaustion (no silent eviction).
    assert loop.enqueue_injected(_frame(fid="c", seq=2, ns=12_000)).reason == AudioFrameSourceReason.BOUND_EXCEEDED
    # Old id still blocked as duplicate semantics / exhaustion — never re-accepted.
    again = loop.enqueue_injected(_frame(fid="a", seq=3, ns=13_000))
    assert again.reason in {
        AudioFrameSourceReason.BOUND_EXCEEDED,
        AudioFrameSourceReason.DUPLICATE_FRAME,
    }
    assert loop.snapshot().replay_exhausted is True


def test_queue_exhaustion_deterministic_drop():
    loop = VoiceAudioLoop(
        "s1",
        clock=lambda: 50_000,
        config=VoiceAudioLoopConfig(max_queue_depth=2, max_buffered_bytes=10_000, max_frame_bytes=8_000),
    )
    loop._open = True
    assert loop.enqueue_injected(_frame(fid="1", seq=0, ns=1000)).accepted
    assert loop.enqueue_injected(_frame(fid="2", seq=1, ns=2000)).accepted
    assert loop.enqueue_injected(_frame(fid="3", seq=2, ns=3000)).accepted
    assert loop.snapshot().frames_dropped >= 1
    assert loop.snapshot().queued_frames == 2


def test_cancel_at_open_read_drain_boundaries():
    frames = [_frame(fid=f"f{i}", seq=i, ns=1000 + i) for i in range(3)]
    src = SyntheticAudioFrameSource(frames, stream_id="s1")
    loop = VoiceAudioLoop("s1", source=src, clock=lambda: 5000)
    assert loop.open().accepted
    assert loop.cancel().reason == AudioFrameSourceReason.CANCELLED
    assert loop.pull().reason == AudioFrameSourceReason.CANCELLED
    assert loop.close().reason == AudioFrameSourceReason.CLOSED


def test_runtime_ingest_never_orchestrates_while_sleeping():
    runtime = VoiceStreamingRuntime("s1", clock=lambda: 2000)
    runtime.start_wake_listening()
    result = runtime.ingest_live_frame(_frame(ns=1500))
    assert result["accepted"] is True
    assert result["can_orchestrate"] is False
    assert runtime.is_wake_listening


def test_assistant_playback_ignores_input_frames_for_orchestration():
    runtime = VoiceStreamingRuntime("s1", clock=lambda: 3000)
    runtime.start_active_listening()
    runtime.assistant_speaking_start()
    assert runtime.state == VoiceStreamState.ASSISTANT_SPEAKING
    result = runtime.ingest_live_frame(_frame(ns=2500))
    assert result["reason"] == "assistant_playback_active"
    assert result["can_orchestrate"] is False


def test_aec_gate_bounds_and_adversarial():
    with pytest.raises(ValueError):
        AecEvidenceGate(stream_id="s1", device_id="default", future_skew_ns=True)
    with pytest.raises(ValueError):
        AecEvidenceGate(stream_id="s1", device_id="default", stale_skew_ns=-1)
    with pytest.raises(ValueError):
        AecEvidenceGate(stream_id="s1", device_id="default", future_skew_ns=float("nan"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AecEvidenceGate(
            stream_id="s1",
            device_id="default",
            future_skew_ns=5_000,
            stale_skew_ns=1_000,
        )
    gate = AecEvidenceGate(stream_id="s1", device_id="default")
    now = 10_000_000_000
    assert gate.accept(
        PlatformAecEvidence("other", "default", True, True, True, now - 10), now_ns=now
    ).reason == "cross_stream"
    assert gate.accept(
        PlatformAecEvidence("s1", "other-dev", True, True, True, now - 10), now_ns=now
    ).reason == "cross_device"
    assert gate.accept(
        PlatformAecEvidence("s1", "default", True, True, True, now + 10**12), now_ns=now
    ).reason == "future_evidence"
    assert gate.accept(
        PlatformAecEvidence("s1", "default", True, True, True, 1), now_ns=now
    ).reason == "stale_evidence"
    ok = gate.accept(
        PlatformAecEvidence("s1", "default", True, True, True, now - 10), now_ns=now
    )
    assert ok.accepted and ok.full_duplex
    gate.mark_lost()
    assert gate.current is None


def test_aec_unavailable_half_duplex_forged_and_loss():
    runtime = VoiceStreamingRuntime("s1", clock=lambda: 10_000_000_000)
    assert runtime.duplex_mode() == "half_duplex"
    bad = PlatformAecEvidence("other", "default", True, True, True, 9_000_000_000)
    assert runtime.submit_platform_aec_evidence(bad)["reason"] == "cross_stream"
    stale = PlatformAecEvidence("s1", "default", True, True, True, 1)
    assert runtime.submit_platform_aec_evidence(stale)["reason"] == "stale_evidence"
    ok = PlatformAecEvidence("s1", "default", True, True, True, 9_500_000_000)
    assert runtime.submit_platform_aec_evidence(ok)["accepted"]
    assert runtime.duplex_mode() == "full_duplex"
    runtime.mark_aec_lost()
    assert runtime.duplex_mode() == "half_duplex"
    assert runtime.echo_cancellation_active() is False


def test_barge_in_adversarial_matrix():
    runtime = VoiceStreamingRuntime("s1", clock=lambda: 8_000)
    runtime.start_active_listening()
    # Old command speech must not authorize barge-in during later playback.
    runtime.process_utterance("turn lights on", is_verified_speaker=True)
    runtime.assistant_speaking_start()
    assert runtime.request_interruption("r0") is False  # missing evidence
    assert runtime.request_interruption("r0", is_authenticated=True) is False

    # speech before playback
    pre = runtime._playback_started_ns
    bad_speech = _evidence(runtime, interruption_id="old", speech_ns=max(1, pre - 100))
    assert runtime.request_interruption(evidence=bad_speech) is False

    # wrong speaker
    wrong_spk = _evidence(runtime, interruption_id="ws", speaker_verified=False)
    assert runtime.request_interruption(evidence=wrong_spk) is False

    # wrong stream
    cross = InterruptionEvidence(
        stream_id="other",
        interruption_id="xs",
        target_assistant_utterance_id="assistant_playback",
        speaker_verified=True,
        verification_source=InterruptionVerificationSource.SPEAKER_AUTH,
        speech_observed_ns=runtime.now_ns(),
        observed_at_ns=runtime.now_ns(),
        expires_at_ns=runtime.now_ns() + 2_000_000_000,
    )
    assert runtime.request_interruption(evidence=cross) is False

    wrong_utt = _evidence(runtime, interruption_id="wu", target_assistant_utterance_id="nope")
    assert runtime.request_interruption(evidence=wrong_utt) is False

    # stale / future
    now = runtime.now_ns()
    stale = _evidence(
        runtime,
        interruption_id="stale",
        expires_at_ns=now - 1,
        observed_at_ns=now - 10,
        speech_observed_ns=now - 10,
    )
    assert runtime.request_interruption(evidence=stale) is False

    # sleeping
    sleep_rt = VoiceStreamingRuntime("sleep", clock=lambda: 9_000)
    sleep_rt.start_wake_listening()
    sleep_ev = InterruptionEvidence(
        stream_id="sleep",
        interruption_id="sl",
        target_assistant_utterance_id="assistant_playback",
        speaker_verified=True,
        verification_source=InterruptionVerificationSource.SPEAKER_AUTH,
        speech_observed_ns=sleep_rt.now_ns(),
        observed_at_ns=sleep_rt.now_ns(),
        expires_at_ns=sleep_rt.now_ns() + 2_000_000_000,
    )
    assert sleep_rt.request_interruption(evidence=sleep_ev) is False

    # playback not active
    idle = VoiceStreamingRuntime("idle", clock=lambda: 9_000)
    idle.start_active_listening()
    idle_ev = InterruptionEvidence(
        stream_id="idle",
        interruption_id="id",
        target_assistant_utterance_id="assistant_playback",
        speaker_verified=True,
        verification_source=InterruptionVerificationSource.SPEAKER_AUTH,
        speech_observed_ns=idle.now_ns(),
        observed_at_ns=idle.now_ns(),
        expires_at_ns=idle.now_ns() + 2_000_000_000,
    )
    assert idle.request_interruption(evidence=idle_ev) is False

    # valid fresh interruption (utterance_only)
    ok = _evidence(runtime, interruption_id="ok1")
    assert runtime.request_interruption(evidence=ok) is True
    # duplicate / replay
    assert runtime.request_interruption(evidence=_evidence(runtime, interruption_id="ok1")) is False


def test_frame_stream_barge_requires_post_playback_vad():
    runtime = VoiceStreamingRuntime("s1", clock=lambda: 20_000)
    runtime.start_active_listening()
    runtime.assistant_speaking_start()
    src = SyntheticAudioFrameSource([], stream_id="s1")
    loop = VoiceAudioLoop("s1", source=src, clock=lambda: 20_000)
    assert loop.open().accepted
    runtime.attach_audio_loop(loop)
    assert runtime.input_capability == AudioInputCapability.FRAME_STREAM
    # Without barge VAD after playback, deny even with evidence.
    assert runtime.request_interruption(evidence=_evidence(runtime, interruption_id="f1")) is False
    # Observe loud frame during playback.
    runtime.ingest_live_frame(_frame(fid="b1", seq=0, ns=runtime.now_ns()))
    assert runtime.request_interruption(evidence=_evidence(runtime, interruption_id="f2")) is True


def test_set_input_capability_public_and_downgrade():
    runtime = VoiceStreamingRuntime("s1")
    denied = runtime.set_input_capability(AudioInputCapability.FRAME_STREAM, frame_loop_open=False)
    assert denied["capability"] == AudioInputCapability.UTTERANCE_ONLY.value
    assert denied["accepted"] is False
    ok = runtime.set_input_capability(AudioInputCapability.FRAME_STREAM, frame_loop_open=True)
    assert ok["capability"] == AudioInputCapability.FRAME_STREAM.value
    runtime.set_input_capability(AudioInputCapability.UTTERANCE_ONLY, frame_loop_open=False)
    assert runtime.input_capability == AudioInputCapability.UTTERANCE_ONLY


def test_pyaudio_package_presence_does_not_imply_frame_stream():
    src = try_create_pyaudio_source(stream_id="s1")
    assert src.capability == AudioInputCapability.UNAVAILABLE


def test_no_raw_audio_or_sensitive_ids_in_repr_or_events():
    frame = _frame()
    assert "frame_id" not in repr(frame)
    assert frame.pcm not in repr(frame).encode("utf-8", errors="ignore")
    assert "speaker" not in repr(
        PlatformAecEvidence("s1", "device-secret", True, True, True, 100)
    )
    runtime = VoiceStreamingRuntime("s1")
    runtime.start_active_listening()
    runtime.assistant_speaking_start()
    runtime.request_interruption(evidence=_evidence(runtime, interruption_id="secret_req"))
    blob = repr(runtime.get_history()) + repr(runtime.content_free_summary())
    assert "secret_req" not in blob
    assert "speaker_id" not in blob
    assert "request_id" not in blob
