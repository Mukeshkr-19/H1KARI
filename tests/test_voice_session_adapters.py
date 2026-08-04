from __future__ import annotations

import asyncio

import pytest

from core.voice_capture.endpointing import UtteranceEndpointGate
from core.voice_capture.vad_backend import EnergyFallbackVadBackend
from core.voice_session.adapters import (
    CancellablePlaybackAdapter,
    EndpointVadObservationAdapter,
    InjectedOwnerVerifierAdapter,
    LocalBytesRendererAdapter,
    VoiceAudioLoopFrameAdapter,
    WholeResponseGenerationAdapter,
    WholeResponseHalfDuplexFallback,
)
from core.voice_session.contracts import AudioFrame, OwnerVerificationResult
from core.voice_streaming.live_audio import (
    AudioFrameSourceReason,
    AudioFrameSourceResult,
    CaptureSourceCategory,
    LiveAudioFrame,
    VoiceAudioLoop,
)


class StubLoop(VoiceAudioLoop):
    def __init__(self, result: AudioFrameSourceResult) -> None:
        self.result = result

    def pull(self) -> AudioFrameSourceResult:
        return self.result


def test_capture_adapter_translates_existing_live_frame_without_copying_identity() -> None:
    async def scenario() -> None:
        live = LiveAudioFrame(
        stream_id="stream_1",
        frame_id="frame_1",
        sequence=1,
        monotonic_ns=100,
        sample_rate=16_000,
        channels=1,
        sample_width=2,
        pcm=b"\x00\x00" * 160,
        capture_source=CaptureSourceCategory.SYNTHETIC,
    )
        adapter = VoiceAudioLoopFrameAdapter(
            StubLoop(AudioFrameSourceResult(True, AudioFrameSourceReason.OK, live)),
            run_sync=lambda call: _immediate(call()),
        )
        frame = await adapter.get_frame()
        assert frame == AudioFrame(live.pcm, 16_000, 1, 100)

    asyncio.run(scenario())


async def _immediate(value: object) -> object:
    return value


def test_endpoint_vad_is_bounded_fanout_not_a_competing_capture_reader() -> None:
    async def scenario() -> None:
        gate = UtteranceEndpointGate(stream_id="stream_1", backend=EnergyFallbackVadBackend())
        adapter = EndpointVadObservationAdapter(gate, max_pending_frames=1)
        frame = AudioFrame(b"\xff\x7f" * 512, 16_000, 1, 10)
        assert adapter.submit(frame) is True
        assert adapter.submit(frame) is False
        observation = await adapter.observe()
        assert observation is not None
        assert observation[2] == 10
        assert await adapter.observe() is None

    asyncio.run(scenario())


def test_owner_adapter_fails_closed_on_malformed_local_result() -> None:
    adapter = InjectedOwnerVerifierAdapter(lambda _frames: object())  # type: ignore[arg-type]
    result = adapter.verify_owner([])
    assert result == OwnerVerificationResult(False, 0.0, reason="invalid_result")


def test_whole_response_generation_preserves_existing_nonstreaming_fallback() -> None:
    async def scenario() -> None:
        adapter = WholeResponseGenerationAdapter(
            lambda prompt: f"reply:{prompt}", run_sync=lambda call: _immediate(call())
        )
        assert [part async for part in adapter.generate("hello")] == ["reply:hello"]

    asyncio.run(scenario())


def test_renderer_requires_actual_bounded_audio_bytes() -> None:
    async def scenario() -> None:
        good = LocalBytesRendererAdapter(
            lambda _text: b"RIFF-audio", run_sync=lambda call: _immediate(call())
        )
        assert await good.render("hello") == b"RIFF-audio"
        bad = LocalBytesRendererAdapter(
            lambda text: text, run_sync=lambda call: _immediate(call())  # type: ignore[arg-type]
        )
        with pytest.raises(TypeError, match="audio_bytes"):
            await bad.render("hello")

    asyncio.run(scenario())


class FakePlaybackHandle:
    def __init__(self) -> None:
        self.paused = False
        self.resumed = False
        self.terminate_requested = False
        self.exited = asyncio.Event()

    async def wait(self) -> int:
        await self.exited.wait()
        return 0

    async def pause(self) -> bool:
        self.paused = True
        return True

    async def resume(self) -> bool:
        self.resumed = True
        return True

    async def terminate(self) -> bool:
        self.terminate_requested = True
        return True


def test_cancellable_playback_separates_requested_from_confirmed_stop() -> None:
    async def scenario() -> None:
        handle = FakePlaybackHandle()

        async def launch(_audio: bytes, _playback_id: str) -> FakePlaybackHandle:
            return handle

        adapter = CancellablePlaybackAdapter(launch)
        play_task = asyncio.create_task(adapter.play(b"audio", "pb_1"))
        await asyncio.sleep(0)
        stop_task = asyncio.create_task(adapter.stop())
        await asyncio.sleep(0)
        assert adapter.stop_report.requested is True
        assert adapter.stop_report.confirmed is False
        assert handle.terminate_requested is True
        handle.exited.set()
        await stop_task
        assert adapter.stop_report.confirmed is True
        assert await play_task is True

    asyncio.run(scenario())


def test_cancelled_play_waiter_does_not_orphan_subprocess_before_stop() -> None:
    async def scenario() -> None:
        handle = FakePlaybackHandle()

        async def launch(_audio: bytes, _playback_id: str) -> FakePlaybackHandle:
            return handle

        adapter = CancellablePlaybackAdapter(launch)
        play_task = asyncio.create_task(adapter.play(b"audio", "pb_2"))
        await asyncio.sleep(0)
        play_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await play_task
        assert handle.terminate_requested is False
        stop_task = asyncio.create_task(adapter.stop())
        await asyncio.sleep(0)
        assert handle.terminate_requested is True
        assert adapter.stop_report.confirmed is False
        handle.exited.set()
        await stop_task
        assert adapter.stop_report.confirmed is True

    asyncio.run(scenario())


def test_whole_response_fallback_is_explicitly_serial_and_nonstreaming() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        adapter = WholeResponseHalfDuplexFallback(
            lambda text: calls.append(text), run_sync=lambda call: _immediate(call())
        )
        assert await adapter.speak("whole response") is True
        assert calls == ["whole response"]

    asyncio.run(scenario())
