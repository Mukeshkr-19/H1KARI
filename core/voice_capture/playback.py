"""Playback controller for barge-in: pause/cancel with physical stop ack."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Callable, Optional, Protocol


class PlaybackState(StrEnum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class CancellablePlayback(Protocol):
    def pause(self) -> None: ...
    def cancel(self) -> None: ...
    def is_alive(self) -> bool: ...


@dataclass(frozen=True, repr=False)
class PlaybackHandle:
    playback_id: str
    response_id: str
    started_ns: int

    def __repr__(self) -> str:
        return "PlaybackHandle()"


class PlaybackController:
    """Tracks cancellable assistant playback; never fabricates stop evidence."""

    def __init__(self, *, now_ns: Optional[Callable[[], int]] = None) -> None:
        self._now_ns = now_ns or (lambda: time.monotonic_ns())
        self._state = PlaybackState.IDLE
        self._handle: Optional[PlaybackHandle] = None
        self._backend: Optional[CancellablePlayback] = None
        self._paused_for_barge = False
        self._stop_confirmed = False

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def handle(self) -> Optional[PlaybackHandle]:
        return self._handle

    @property
    def stop_confirmed(self) -> bool:
        return self._stop_confirmed

    def start(
        self,
        *,
        playback_id: str,
        response_id: str,
        backend: CancellablePlayback,
        started_ns: Optional[int] = None,
    ) -> PlaybackHandle:
        for name, value in (("playback_id", playback_id), ("response_id", response_id)):
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None
            ):
                raise ValueError(f"invalid_{name}")
        if not callable(getattr(backend, "pause", None)) or not callable(getattr(backend, "cancel", None)):
            raise ValueError("invalid_playback_backend")
        if not callable(getattr(backend, "is_alive", None)):
            raise ValueError("invalid_playback_backend")
        if self._state not in {PlaybackState.IDLE, PlaybackState.STOPPED}:
            raise RuntimeError("playback_already_active")
        ts = self._now_ns() if started_ns is None else started_ns
        now = self._now_ns()
        if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0 or ts > now + 1_000_000_000:
            raise ValueError("invalid_started_ns")
        self._handle = PlaybackHandle(playback_id, response_id, ts)
        self._backend = backend
        self._state = PlaybackState.PLAYING
        self._paused_for_barge = False
        self._stop_confirmed = False
        return self._handle

    def pause_for_barge(self) -> bool:
        if self._state != PlaybackState.PLAYING or self._backend is None:
            return False
        try:
            self._backend.pause()
        except Exception:
            return False
        self._state = PlaybackState.PAUSED
        self._paused_for_barge = True
        return True

    def cancel(self) -> bool:
        if self._backend is None:
            return self._state == PlaybackState.STOPPED and self._stop_confirmed
        self._state = PlaybackState.STOPPING
        try:
            self._backend.cancel()
        except Exception:
            self._state = PlaybackState.FAILED
            return False
        # Physical stop confirmation is separate (notify after process reaped).
        return True

    def notify_physically_stopped(self) -> bool:
        """Caller must invoke only after playback process has exited."""
        if self._state not in {PlaybackState.STOPPING, PlaybackState.PAUSED, PlaybackState.PLAYING}:
            if self._state == PlaybackState.STOPPED:
                return True
            return False
        if self._backend is None:
            return False
        try:
            if self._backend.is_alive():
                return False
        except Exception:
            self._state = PlaybackState.FAILED
            return False
        self._state = PlaybackState.STOPPED
        self._stop_confirmed = True
        self._backend = None
        return True

    def clear(self) -> None:
        self._state = PlaybackState.IDLE
        self._handle = None
        self._backend = None
        self._paused_for_barge = False
        self._stop_confirmed = False

    def __repr__(self) -> str:
        return f"PlaybackController(state={self._state.value!r}, stop_confirmed={self._stop_confirmed})"
