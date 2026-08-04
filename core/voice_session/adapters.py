"""Explicit async adapter seams for the prospective voice session authority.

Nothing in this module opens a microphone, starts a subprocess, loads a model,
or accesses persistent state on import.  Production capabilities are injected;
the adapters only translate their bounded results into coordinator contracts.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import tempfile
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional, Protocol

from core.voice_capture.endpointing import EndpointEvent, UtteranceEndpointGate
from core.voice_session.contracts import (
    AudioFrame,
    OwnerVerificationResult,
    validate_playback_id,
)
from core.voice_streaming.live_audio import AudioFrameSourceReason, VoiceAudioLoop


async def _run_sync(call: Callable[[], object]) -> object:
    return await asyncio.to_thread(call)


class VoiceAudioLoopFrameAdapter:
    """Non-blocking coordinator frame source backed by an injected VoiceAudioLoop."""

    def __init__(
        self,
        loop: VoiceAudioLoop,
        *,
        run_sync: Callable[[Callable[[], object]], Awaitable[object]] = _run_sync,
    ) -> None:
        if not isinstance(loop, VoiceAudioLoop):
            raise TypeError("loop must be a VoiceAudioLoop")
        self._loop = loop
        self._run_sync = run_sync

    async def get_frame(self) -> Optional[AudioFrame]:
        result = await self._run_sync(self._loop.pull)
        if result.accepted and result.frame is not None:
            frame = result.frame
            return AudioFrame(
                data=frame.pcm,
                sample_rate=frame.sample_rate,
                channels=frame.channels,
                monotonic_ns=frame.monotonic_ns,
            )
        if result.reason in {
            AudioFrameSourceReason.TIMEOUT,
            AudioFrameSourceReason.QUEUE_EXHAUSTED,
        }:
            return None
        # Closed/unavailable/cancelled sources fail closed without inventing audio.
        return None


class EndpointVadObservationAdapter:
    """Bounded fan-out VAD adapter; capture remains the sole frame consumer.

    A caller submits each captured frame once.  ``observe`` processes the next
    bounded queued frame, avoiding a second competing read from the microphone.
    """

    def __init__(self, gate: UtteranceEndpointGate, *, max_pending_frames: int = 32) -> None:
        if not isinstance(gate, UtteranceEndpointGate):
            raise TypeError("gate must be an UtteranceEndpointGate")
        if isinstance(max_pending_frames, bool) or not isinstance(max_pending_frames, int):
            raise TypeError("max_pending_frames must be an integer")
        if max_pending_frames < 1 or max_pending_frames > 256:
            raise ValueError("max_pending_frames out of range")
        self._gate = gate
        self._queue: asyncio.Queue[AudioFrame] = asyncio.Queue(maxsize=max_pending_frames)

    def submit(self, frame: AudioFrame) -> bool:
        if not isinstance(frame, AudioFrame):
            return False
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            return False
        return True

    async def observe(self) -> Optional[tuple[bool, float, int]]:
        try:
            frame = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        result = self._gate.process_frame(
            frame.data,
            monotonic_ns=frame.monotonic_ns,
            sample_rate=frame.sample_rate,
        )
        if not result.available or result.event in {EndpointEvent.FAILED, EndpointEvent.CANCELLED}:
            return None
        return (result.speech_probability > 0.0, result.speech_probability, frame.monotonic_ns)


class InjectedOwnerVerifierAdapter:
    """Content-safe owner verification boundary over an injected local verifier."""

    def __init__(self, verify: Callable[[list[AudioFrame]], OwnerVerificationResult]) -> None:
        if not callable(verify):
            raise TypeError("verify must be callable")
        self._verify = verify

    def verify_owner(self, frames: list[AudioFrame]) -> OwnerVerificationResult:
        result = self._verify(frames)
        if not isinstance(result, OwnerVerificationResult):
            return OwnerVerificationResult(False, 0.0, reason="invalid_result")
        return result


class WholeResponseGenerationAdapter:
    """Expose the existing synchronous orchestrator as one async response chunk."""

    def __init__(
        self,
        process_input: Callable[[str], str],
        *,
        run_sync: Callable[[Callable[[], object]], Awaitable[object]] = _run_sync,
    ) -> None:
        if not callable(process_input):
            raise TypeError("process_input must be callable")
        self._process_input = process_input
        self._run_sync = run_sync

    async def generate(self, prompt_text: str) -> AsyncIterator[str]:
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            return
        result = await self._run_sync(lambda: self._process_input(prompt_text))
        if isinstance(result, str) and result:
            yield result


class LocalBytesRendererAdapter:
    """Async wrapper for a local renderer that returns actual encoded audio bytes."""

    def __init__(
        self,
        render_bytes: Callable[[str], bytes],
        *,
        run_sync: Callable[[Callable[[], object]], Awaitable[object]] = _run_sync,
        max_audio_bytes: int = 10_000_000,
    ) -> None:
        if not callable(render_bytes):
            raise TypeError("render_bytes must be callable")
        if isinstance(max_audio_bytes, bool) or not isinstance(max_audio_bytes, int):
            raise TypeError("max_audio_bytes must be an integer")
        if max_audio_bytes < 1 or max_audio_bytes > 10_000_000:
            raise ValueError("max_audio_bytes out of range")
        self._render_bytes = render_bytes
        self._run_sync = run_sync
        self._max_audio_bytes = max_audio_bytes

    async def render(self, text: str) -> bytes:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("invalid_render_text")
        result = await self._run_sync(lambda: self._render_bytes(text))
        if not isinstance(result, (bytes, bytearray)):
            raise TypeError("renderer_did_not_return_audio_bytes")
        audio = bytes(result)
        if not audio or len(audio) > self._max_audio_bytes:
            raise ValueError("invalid_rendered_audio_size")
        return audio


class AsyncPlaybackHandle(Protocol):
    async def wait(self) -> int: ...
    async def pause(self) -> bool: ...
    async def resume(self) -> bool: ...
    async def terminate(self) -> bool: ...


class AsyncioSubprocessPlaybackHandle:
    """Cancellable local subprocess handle; process exit is stop confirmation."""

    def __init__(self, process: asyncio.subprocess.Process, audio_path: Path) -> None:
        self._process = process
        self._audio_path = audio_path

    async def wait(self) -> int:
        try:
            return await self._process.wait()
        finally:
            try:
                self._audio_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def pause(self) -> bool:
        if self._process.returncode is not None:
            return False
        try:
            self._process.send_signal(signal.SIGSTOP)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    async def resume(self) -> bool:
        if self._process.returncode is not None:
            return False
        try:
            self._process.send_signal(signal.SIGCONT)
        except (ProcessLookupError, PermissionError):
            return False
        return True

    async def terminate(self) -> bool:
        if self._process.returncode is not None:
            return True
        try:
            self._process.terminate()
        except ProcessLookupError:
            return True
        return True


class LocalAudioSubprocessLauncher:
    """Materialize encoded audio bytes and launch an explicitly configured player.

    The executable is never discovered or invoked until the returned callable
    is awaited.  No shell is used and transcript/response text is never placed
    in argv, filenames, or diagnostics.
    """

    def __init__(self, *, executable: str, temp_dir: Optional[str] = None) -> None:
        path = Path(executable)
        if not path.is_absolute():
            raise ValueError("playback executable must be absolute")
        self._executable = str(path)
        self._temp_dir = temp_dir

    async def __call__(self, audio_data: bytes, playback_id: str) -> AsyncPlaybackHandle:
        validate_playback_id(playback_id)
        if not isinstance(audio_data, bytes) or not audio_data or len(audio_data) > 10_000_000:
            raise ValueError("invalid_audio_data")
        fd, raw_path = tempfile.mkstemp(prefix="hikari-voice-", suffix=".wav", dir=self._temp_dir)
        audio_path = Path(raw_path)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(audio_data)
                stream.flush()
            process = await asyncio.create_subprocess_exec(
                self._executable,
                str(audio_path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            audio_path.unlink(missing_ok=True)
            raise
        return AsyncioSubprocessPlaybackHandle(process, audio_path)


@dataclass(frozen=True, repr=False)
class PlaybackStopReport:
    playback_id: Optional[str]
    requested: bool
    confirmed: bool

    def __repr__(self) -> str:
        return (
            "PlaybackStopReport("
            f"has_playback_id={self.playback_id is not None}, requested={self.requested}, "
            f"confirmed={self.confirmed})"
        )


class CancellablePlaybackAdapter:
    """Coordinator playback port with correlated, physically confirmed stop."""

    def __init__(
        self,
        launch: Callable[[bytes, str], Awaitable[AsyncPlaybackHandle]],
    ) -> None:
        if not callable(launch):
            raise TypeError("launch must be callable")
        self._launch = launch
        self._handle: Optional[AsyncPlaybackHandle] = None
        self._wait_task: Optional[asyncio.Task[int]] = None
        self._playback_id: Optional[str] = None
        self._stop_report = PlaybackStopReport(None, False, False)
        self._lock = asyncio.Lock()

    @property
    def stop_report(self) -> PlaybackStopReport:
        return self._stop_report

    async def play(self, audio_data: bytes, playback_id: str) -> bool:
        playback_id = validate_playback_id(playback_id)
        if not isinstance(audio_data, bytes) or not audio_data:
            return False
        async with self._lock:
            if self._handle is not None:
                return False
            handle = await self._launch(audio_data, playback_id)
            if handle is None or not callable(getattr(handle, "wait", None)):
                return False
            self._handle = handle
            self._playback_id = playback_id
            self._stop_report = PlaybackStopReport(playback_id, False, False)
            self._wait_task = asyncio.create_task(handle.wait())
            wait_task = self._wait_task
        try:
            # Cancelling the coordinator consumer must not cancel/reap tracking
            # before stop() can terminate and confirm the physical subprocess.
            return (await asyncio.shield(wait_task)) == 0
        finally:
            async with self._lock:
                if self._wait_task is wait_task and wait_task.done():
                    self._handle = None
                    self._wait_task = None
                    self._playback_id = None

    async def pause(self) -> None:
        async with self._lock:
            handle = self._handle
        if handle is not None:
            await handle.pause()

    async def resume(self) -> None:
        async with self._lock:
            handle = self._handle
        if handle is not None:
            await handle.resume()

    async def stop(self) -> None:
        async with self._lock:
            handle = self._handle
            wait_task = self._wait_task
            playback_id = self._playback_id
            if handle is None or wait_task is None:
                return
            self._stop_report = PlaybackStopReport(playback_id, True, False)
        requested = await handle.terminate()
        if not requested:
            return
        try:
            await asyncio.shield(wait_task)
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        async with self._lock:
            self._stop_report = PlaybackStopReport(playback_id, True, wait_task.done())
            if self._wait_task is wait_task and wait_task.done():
                self._handle = None
                self._wait_task = None
                self._playback_id = None


class WholeResponseHalfDuplexFallback:
    """Explicit adapter for the current non-streaming local TTS path.

    This fallback never claims cancellation or full duplex.  It serializes one
    whole response and reports completion only after the injected synchronous
    speaker returns.
    """

    def __init__(
        self,
        speak_whole_response: Callable[[str], object],
        *,
        run_sync: Callable[[Callable[[], object]], Awaitable[object]] = _run_sync,
    ) -> None:
        if not callable(speak_whole_response):
            raise TypeError("speak_whole_response must be callable")
        self._speak = speak_whole_response
        self._run_sync = run_sync
        self._lock = asyncio.Lock()

    async def speak(self, text: str) -> bool:
        if not isinstance(text, str) or not text.strip():
            return False
        async with self._lock:
            result = await self._run_sync(lambda: self._speak(text))
        return result is not False


__all__ = [
    "AsyncPlaybackHandle",
    "AsyncioSubprocessPlaybackHandle",
    "CancellablePlaybackAdapter",
    "EndpointVadObservationAdapter",
    "InjectedOwnerVerifierAdapter",
    "LocalBytesRendererAdapter",
    "LocalAudioSubprocessLauncher",
    "PlaybackStopReport",
    "VoiceAudioLoopFrameAdapter",
    "WholeResponseGenerationAdapter",
    "WholeResponseHalfDuplexFallback",
]
