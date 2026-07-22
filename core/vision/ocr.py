"""Bounded local OCR adapter.

Provides a deterministic, side-effect-free boundary around a local OCR runner.
No network, browser, camera, screenshot, provider, or cloud use is permitted.
"""

from __future__ import annotations

import subprocess
import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class OcrStatus(StrEnum):
    """Bounded lifecycle status for an OCR request."""

    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class OcrResult:
    """Immutable OCR result.

    The recognized text is intentionally omitted from ``__repr__`` to avoid
    leaking user content into logs, exceptions, or audit metadata.
    """

    status: OcrStatus
    text: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, OcrStatus):
            raise ValueError("status must be an OcrStatus")
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if len(self.text) > _MAX_OUTPUT_CODEPOINTS:
            raise ValueError("OCR text exceeds maximum length")
        for char in self.text:
            code = ord(char)
            if char not in _ALLOWED_WHITESPACE and (code < 32 or code == 127):
                raise ValueError("OCR text contains control characters")
            if unicodedata.category(char) == "Cf":
                raise ValueError("OCR text contains Unicode format characters")

    def __repr__(self) -> str:
        return f"OcrResult(status={self.status.value!r})"


class OcrAdapterError(Exception):
    """Fixed-message error raised for unrecoverable OCR adapter failures."""

    _MESSAGE = "OCR adapter error"

    def __init__(self) -> None:
        super().__init__(self._MESSAGE)

    def __repr__(self) -> str:
        return "OcrAdapterError()"


@dataclass(frozen=True)
class CommandResult:
    """Bounded command outcome.

    stdout is intentionally excluded from ``__repr__`` because it may contain
    user content.
    """

    returncode: int
    stdout: bytes = b""

    def __post_init__(self) -> None:
        if isinstance(self.returncode, bool) or not isinstance(self.returncode, int):
            raise ValueError("returncode must be an integer")
        if not isinstance(self.stdout, bytes):
            raise ValueError("stdout must be bytes")

    def __repr__(self) -> str:
        return f"CommandResult(returncode={self.returncode})"


class CommandRunner(Protocol):
    """Injected runner for argv arrays with stdin bytes.

    Production uses :class:`BoundedTesseractRunner`; tests use deterministic
    fakes.
    """

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        stdin: bytes,
    ) -> CommandResult: ...


_MAX_INPUT_BYTES = 1_048_576
_MAX_OUTPUT_CODEPOINTS = 20_000
_MAX_STDOUT_BYTES = _MAX_OUTPUT_CODEPOINTS * 4
_READ_CHUNK_SIZE = 8192
_TIMEOUT_SECONDS = 15.0
_ALLOWED_MIME_TYPES = frozenset({"image/png", "image/jpeg"})
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_ALLOWED_WHITESPACE = frozenset({"\n", "\t"})


def _is_allowed_mime(mime_type: object) -> bool:
    return isinstance(mime_type, str) and mime_type in _ALLOWED_MIME_TYPES


class BoundedTesseractRunner:
    """Production runner that invokes Tesseract once with bounded stdout."""

    def __init__(self, executable_path: Path | str) -> None:
        if not isinstance(executable_path, (Path, str)):
            raise OcrAdapterError()
        path = Path(executable_path)
        if not path.parts or not str(path) or not path.is_absolute():
            raise OcrAdapterError()
        self._executable_path = path

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        stdin: bytes,
    ) -> CommandResult:
        if not isinstance(argv, (list, tuple)) or not argv:
            return CommandResult(returncode=1, stdout=b"")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            return CommandResult(returncode=1, stdout=b"")
        if not isinstance(stdin, bytes):
            return CommandResult(returncode=1, stdout=b"")

        try:
            if Path(argv[0]).resolve() != self._executable_path.resolve():
                return CommandResult(returncode=1, stdout=b"")
        except (OSError, ValueError):
            return CommandResult(returncode=1, stdout=b"")

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
            return CommandResult(returncode=1, stdout=b"")

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
                    proc.stdin.write(stdin)
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
                    chunk = proc.stdout.read(_READ_CHUNK_SIZE)
                except (OSError, ValueError):
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_STDOUT_BYTES:
                    _kill()
                    return CommandResult(returncode=1, stdout=b"")

            writer.join(timeout=1.0)
            if killed.is_set():
                return CommandResult(returncode=1, stdout=b"")

            try:
                returncode = proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                _kill()
                return CommandResult(returncode=1, stdout=b"")

            return CommandResult(returncode=returncode, stdout=b"".join(chunks))
        except Exception:
            _kill()
            return CommandResult(returncode=1, stdout=b"")
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


class LocalOcrAdapter:
    """Bounded local OCR adapter.

    Validates input, runs an injected ``CommandRunner``, and returns a safe,
    deterministic ``OcrResult``. No content is ever logged or surfaced in
    exceptions.
    """

    def __init__(
        self,
        *,
        executable_path: Path | str,
        runner: CommandRunner | None = None,
    ) -> None:
        self._executable_path = Path(executable_path)
        if not self._executable_path.is_absolute():
            raise OcrAdapterError()
        if runner is None:
            self._runner: CommandRunner = BoundedTesseractRunner(self._executable_path)
        else:
            self._runner = runner

    def analyze(self, image_bytes: bytes, *, mime_type: str) -> OcrResult:
        """Analyze bounded image bytes and return a deterministic OCR result."""
        if not isinstance(image_bytes, bytes):
            return OcrResult(status=OcrStatus.UNAVAILABLE)
        if len(image_bytes) == 0:
            return OcrResult(status=OcrStatus.UNAVAILABLE)
        if len(image_bytes) > _MAX_INPUT_BYTES:
            return OcrResult(status=OcrStatus.UNAVAILABLE)
        if not _is_allowed_mime(mime_type):
            return OcrResult(status=OcrStatus.UNAVAILABLE)

        expected_magic = {_PNG_MAGIC: "image/png", _JPEG_MAGIC: "image/jpeg"}
        matched = False
        for magic, mt in expected_magic.items():
            if mime_type == mt and image_bytes.startswith(magic):
                matched = True
                break
        if not matched:
            return OcrResult(status=OcrStatus.UNAVAILABLE)

        argv = [str(self._executable_path), "stdin", "stdout"]
        try:
            result = self._runner(argv, timeout=_TIMEOUT_SECONDS, stdin=image_bytes)
        except Exception:
            return OcrResult(status=OcrStatus.UNAVAILABLE)

        if not isinstance(result, CommandResult):
            return OcrResult(status=OcrStatus.UNAVAILABLE)

        if result.returncode != 0:
            return OcrResult(status=OcrStatus.FAILED)

        stdout = result.stdout
        if not isinstance(stdout, bytes):
            return OcrResult(status=OcrStatus.UNAVAILABLE)

        try:
            text = stdout.decode("utf-8")
        except UnicodeDecodeError:
            return OcrResult(status=OcrStatus.UNAVAILABLE)

        try:
            return OcrResult(status=OcrStatus.SUCCESS, text=text)
        except ValueError:
            return OcrResult(status=OcrStatus.UNAVAILABLE)

    def __repr__(self) -> str:
        return "LocalOcrAdapter()"
