"""Bounded helper process lifecycle. Never launches caller-provided executables."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Sequence

from core.voice_capture.config import VoiceCaptureConfig


class HelperProcessError(RuntimeError):
    """Content-free helper failure."""


class HelperProcess:
    """Own a repository-resolved helper subprocess with bounded I/O."""

    def __init__(
        self,
        *,
        executable: Path,
        args: Sequence[str],
        config: Optional[VoiceCaptureConfig] = None,
        allowed_root: Optional[Path] = None,
    ) -> None:
        self._config = config or VoiceCaptureConfig()
        resolved = Path(executable).resolve()
        if not resolved.is_file():
            raise HelperProcessError("helper_missing")
        if not os.access(resolved, os.X_OK):
            raise HelperProcessError("helper_not_executable")
        root = (
            Path(__file__).resolve().parents[2] / "native" / "macos_audio_capture" / ".build"
            if allowed_root is None
            else Path(allowed_root).resolve()
        )
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise HelperProcessError("helper_path_rejected") from exc
        name = resolved.name
        if name != "hikari-macos-audio-capture":
            raise HelperProcessError("helper_name_rejected")
        normalized_args = tuple(args)
        if normalized_args not in {(), ("--probe",), ("--capture",)}:
            raise HelperProcessError("helper_args_rejected")
        fixed_args: List[str] = [str(resolved)]
        for arg in normalized_args:
            if not isinstance(arg, str) or not arg or len(arg) > 64:
                raise HelperProcessError("invalid_helper_arg")
            fixed_args.append(arg)
        if len(fixed_args) > 5:
            raise HelperProcessError("too_many_helper_args")
        self._argv = fixed_args
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._stderr_buf = bytearray()
        self._closed = False

    @property
    def pid(self) -> Optional[int]:
        return None if self._proc is None else self._proc.pid

    @property
    def returncode(self) -> Optional[int]:
        return None if self._proc is None else self._proc.poll()

    def start(self) -> None:
        if self._closed:
            raise HelperProcessError("closed")
        if self._proc is not None:
            raise HelperProcessError("already_started")
        self._proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )

    def write_stdin(self, data: bytes) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise HelperProcessError("not_started")
        self._proc.stdin.write(data)
        self._proc.stdin.flush()

    def read_exact(self, nbytes: int, *, timeout_s: float) -> bytes:
        if self._proc is None or self._proc.stdout is None:
            raise HelperProcessError("not_started")
        if nbytes < 1 or nbytes > self._config.max_frame_bytes + 48:
            raise HelperProcessError("invalid_read_size")
        deadline = time.monotonic() + timeout_s
        buf = bytearray()
        while len(buf) < nbytes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HelperProcessError("read_timeout")
            self._drain_stderr_nonblocking()
            if self._proc.poll() is not None and not select.select([self._proc.stdout], [], [], 0)[0]:
                raise HelperProcessError("helper_exited")
            ready, _, _ = select.select([self._proc.stdout], [], [], min(remaining, 0.25))
            if not ready:
                continue
            chunk = self._proc.stdout.read(nbytes - len(buf))
            if not chunk:
                raise HelperProcessError("helper_eof")
            buf.extend(chunk)
        return bytes(buf)

    def _drain_stderr_nonblocking(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        while True:
            ready, _, _ = select.select([self._proc.stderr], [], [], 0)
            if not ready:
                break
            chunk = self._proc.stderr.read(256)
            if not chunk:
                break
            room = self._config.max_stderr_bytes - len(self._stderr_buf)
            if room > 0:
                self._stderr_buf.extend(chunk[:room])

    def stderr_bytes(self) -> int:
        return len(self._stderr_buf)

    def request_cancel(self) -> None:
        try:
            self.write_stdin(b"c\n")
        except Exception:
            pass

    def stop(self, *, timeout_s: Optional[float] = None) -> None:
        timeout = self._config.shutdown_timeout_s if timeout_s is None else timeout_s
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 30:
            raise HelperProcessError("invalid_shutdown_timeout")
        if self._proc is None:
            self._closed = True
            return
        proc = self._proc
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.write(b"q\n")
                    proc.stdin.flush()
                    proc.stdin.close()
                except Exception:
                    pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception:
                    proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        proc.kill()
                    proc.wait(timeout=timeout)
        finally:
            for stream in (proc.stdout, proc.stderr, proc.stdin):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            self._proc = None
            self._closed = True

    def __repr__(self) -> str:
        return f"HelperProcess(pid={self.pid!r}, closed={self._closed})"
