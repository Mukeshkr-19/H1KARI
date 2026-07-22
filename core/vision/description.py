"""Bounded local image-description adapter.

Provides a deterministic boundary around an injected local description runner.
No network, browser, camera, screenshot, provider, cloud, upload, or model
download is permitted. Construction and import perform no execution.

The optional production candidate is provisioned through a separately reviewed
and verified local model manifest and a disposable spawned worker. This adapter remains
usable with an explicitly injected callable or absolute executable; missing or
invalid provisioning makes the capability fail closed.
"""

from __future__ import annotations

import subprocess
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Optional, Protocol

from core.vision.contracts import (
    MAX_CONFIDENCE_MILLI,
    MAX_OBSERVATION_TEXT_LENGTH,
    MIN_CONFIDENCE_MILLI,
    MIN_OBSERVATION_TEXT_LENGTH,
    VisionObservation,
    VisionObservationKind,
)


class DescriptionStatus(StrEnum):
    """Bounded lifecycle status for a description request."""

    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class DescriptionResult:
    """Immutable description result.

    Description text and confidence are intentionally omitted from ``__repr__``
    and ``__str__`` to avoid leaking user content.
    """

    status: DescriptionStatus
    text: str = ""
    confidence_milli: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, DescriptionStatus):
            raise ValueError("status must be a DescriptionStatus")
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        length = len(self.text)
        if self.status is DescriptionStatus.SUCCESS:
            if length < MIN_OBSERVATION_TEXT_LENGTH:
                raise ValueError("description text is too short")
            if length > MAX_OBSERVATION_TEXT_LENGTH:
                raise ValueError("description text exceeds maximum length")
            if not self.text.strip():
                raise ValueError("description text must not be whitespace-only")
            for char in self.text:
                code = ord(char)
                if code < 32 or code == 127:
                    raise ValueError("description text contains control characters")
                if unicodedata.category(char) == "Cf":
                    raise ValueError(
                        "description text contains Unicode format characters"
                    )
        elif self.text:
            raise ValueError("non-success results must not carry description text")
        if self.confidence_milli is None:
            return
        if isinstance(self.confidence_milli, bool) or not isinstance(
            self.confidence_milli, int
        ):
            raise ValueError("confidence_milli must be an integer or None")
        if (
            self.confidence_milli < MIN_CONFIDENCE_MILLI
            or self.confidence_milli > MAX_CONFIDENCE_MILLI
        ):
            raise ValueError("confidence_milli is out of range")
        if self.status is not DescriptionStatus.SUCCESS:
            raise ValueError("non-success results must not carry confidence")

    def __repr__(self) -> str:
        return f"DescriptionResult(status={self.status.value!r})"

    def __str__(self) -> str:
        return self.__repr__()


class DescriptionAdapterError(Exception):
    """Fixed-message error raised for unrecoverable description adapter failures."""

    _MESSAGE = "description adapter error"

    def __init__(self) -> None:
        super().__init__(self._MESSAGE)

    def __repr__(self) -> str:
        return "DescriptionAdapterError()"

    def __str__(self) -> str:
        return self._MESSAGE


@dataclass(frozen=True)
class DescriptionAnalyzerResult:
    """Bounded outcome from an injected local description analyzer.

    Text and confidence are omitted from ``__repr__`` because they may contain
    or derive from user content.
    """

    status: DescriptionStatus
    text: str = ""
    confidence_milli: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, DescriptionStatus):
            raise ValueError("status must be a DescriptionStatus")
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if self.confidence_milli is None:
            return
        if isinstance(self.confidence_milli, bool) or not isinstance(
            self.confidence_milli, int
        ):
            raise ValueError("confidence_milli must be an integer or None")

    def __repr__(self) -> str:
        return f"DescriptionAnalyzerResult(status={self.status.value!r})"

    def __str__(self) -> str:
        return self.__repr__()


class DescriptionRunner(Protocol):
    """Injected local description runner.

    Production may use :class:`BoundedLocalDescriptionRunner`. Tests inject
    deterministic fakes. Runners never receive relative paths or PATH lookup.
    """

    def __call__(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        timeout: float,
    ) -> DescriptionAnalyzerResult: ...


_MAX_INPUT_BYTES = 1_048_576
_MAX_STDOUT_BYTES = MAX_OBSERVATION_TEXT_LENGTH * 4
_READ_CHUNK_SIZE = 8192
_TIMEOUT_SECONDS = 30.0
_ALLOWED_MIME_TYPES = frozenset({"image/png", "image/jpeg"})
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def _is_allowed_mime(mime_type: object) -> bool:
    return isinstance(mime_type, str) and mime_type in _ALLOWED_MIME_TYPES


def _mime_matches_magic(image_bytes: bytes, mime_type: str) -> bool:
    if mime_type == "image/png":
        return image_bytes.startswith(_PNG_MAGIC)
    if mime_type == "image/jpeg":
        return image_bytes.startswith(_JPEG_MAGIC)
    return False


class BoundedLocalDescriptionRunner:
    """Subprocess boundary for an explicit absolute local executable.

    This runner does not discover models, search PATH, expand home directories,
    or download assets. It remains available as an injected compatibility
    boundary; the optional production candidate uses the spawned MLX runner.
    """

    def __init__(self, executable_path: Path | str) -> None:
        if not isinstance(executable_path, (Path, str)):
            raise DescriptionAdapterError()
        path = Path(executable_path)
        if not path.parts or not str(path) or not path.is_absolute():
            raise DescriptionAdapterError()
        self._executable_path = path

    def __call__(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        timeout: float,
    ) -> DescriptionAnalyzerResult:
        if not isinstance(image_bytes, bytes):
            return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)
        if not _is_allowed_mime(mime_type):
            return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
            or timeout > _TIMEOUT_SECONDS
        ):
            return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)

        argv = [str(self._executable_path)]
        try:
            if Path(argv[0]).resolve() != self._executable_path.resolve():
                return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)
        except (OSError, ValueError):
            return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)

        try:
            proc = subprocess.Popen(
                list(argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                env={},
            )
        except (OSError, subprocess.SubprocessError):
            return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)

        killed = threading.Event()

        def _kill() -> None:
            killed.set()
            try:
                proc.kill()
            except Exception:
                pass

        def _write_stdin() -> None:
            try:
                if proc.stdin is not None:
                    proc.stdin.write(image_bytes)
                    proc.stdin.close()
            except (OSError, BrokenPipeError):
                pass

        timer = threading.Timer(timeout, _kill)
        timer.start()
        writer = threading.Thread(target=_write_stdin, daemon=True)
        writer.start()
        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                try:
                    chunk = proc.stdout.read(_READ_CHUNK_SIZE) if proc.stdout else b""
                except (OSError, ValueError):
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_STDOUT_BYTES:
                    _kill()
                    return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)

            writer.join(timeout=1.0)
            if killed.is_set():
                return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)

            try:
                returncode = proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                _kill()
                return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)

            if returncode != 0:
                return DescriptionAnalyzerResult(status=DescriptionStatus.FAILED)

            try:
                text = b"".join(chunks).decode("utf-8")
            except UnicodeDecodeError:
                return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)

            # Subprocess stdout carries text only; no measured confidence is available.
            return DescriptionAnalyzerResult(
                status=DescriptionStatus.SUCCESS,
                text=text,
                confidence_milli=None,
            )
        except Exception:
            _kill()
            return DescriptionAnalyzerResult(status=DescriptionStatus.UNAVAILABLE)
        finally:
            timer.cancel()
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass
            writer.join(timeout=1.0)

    def __repr__(self) -> str:
        return "BoundedLocalDescriptionRunner()"


class LocalDescriptionAdapter:
    """Bounded local description adapter matching DescriptionAnalyzer.

    Validates image bytes and MIME magic, invokes an injected local runner or
    callable, and returns content-safe results. Never captures images, searches
    PATH, downloads models, or persists content.
    """

    def __init__(
        self,
        *,
        executable_path: Path | str | None = None,
        runner: DescriptionRunner | None = None,
        analyzer: Callable[..., DescriptionAnalyzerResult] | None = None,
    ) -> None:
        if analyzer is not None:
            if executable_path is not None or runner is not None:
                raise DescriptionAdapterError()
            if not callable(analyzer):
                raise DescriptionAdapterError()
            self._runner: DescriptionRunner | Callable[..., DescriptionAnalyzerResult] = (
                analyzer
            )
            self._executable_path: Path | None = None
            return

        if executable_path is None:
            raise DescriptionAdapterError()
        path = Path(executable_path)
        if not path.parts or not str(path) or not path.is_absolute():
            raise DescriptionAdapterError()
        self._executable_path = path
        if runner is None:
            self._runner = BoundedLocalDescriptionRunner(path)
        else:
            if not callable(runner):
                raise DescriptionAdapterError()
            self._runner = runner

    def analyze(self, image_bytes: bytes, *, mime_type: str) -> DescriptionResult:
        """Describe bounded image bytes and return a deterministic result."""
        if not isinstance(image_bytes, bytes):
            return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)
        if len(image_bytes) == 0:
            return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)
        if len(image_bytes) > _MAX_INPUT_BYTES:
            return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)
        if not _is_allowed_mime(mime_type):
            return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)
        if not _mime_matches_magic(image_bytes, mime_type):
            return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)

        try:
            raw = self._runner(
                image_bytes,
                mime_type=mime_type,
                timeout=_TIMEOUT_SECONDS,
            )
        except Exception:
            return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)

        if not isinstance(raw, DescriptionAnalyzerResult):
            return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)

        if raw.status is DescriptionStatus.FAILED:
            return DescriptionResult(status=DescriptionStatus.FAILED)
        if raw.status is not DescriptionStatus.SUCCESS:
            return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)

        confidence = raw.confidence_milli
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, int):
                return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)
            if confidence < MIN_CONFIDENCE_MILLI or confidence > MAX_CONFIDENCE_MILLI:
                return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)

        try:
            return DescriptionResult(
                status=DescriptionStatus.SUCCESS,
                text=raw.text,
                confidence_milli=confidence,
            )
        except ValueError:
            return DescriptionResult(status=DescriptionStatus.UNAVAILABLE)

    def __call__(
        self, image_bytes: bytes, *, mime_type: str
    ) -> tuple[VisionObservation, ...]:
        """DescriptionAnalyzer contract used by VisionRuntime."""
        result = self.analyze(image_bytes, mime_type=mime_type)
        if result.status is not DescriptionStatus.SUCCESS:
            return ()
        try:
            observation = VisionObservation(
                kind=VisionObservationKind.DESCRIPTION,
                text=result.text,
                confidence_milli=result.confidence_milli,
            )
        except Exception:
            return ()
        return (observation,)

    def cancel(self) -> None:
        """Request cancellation when the injected runner supports it."""
        cancel = getattr(self._runner, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass

    def __repr__(self) -> str:
        return "LocalDescriptionAdapter()"

    def __str__(self) -> str:
        return self.__repr__()
