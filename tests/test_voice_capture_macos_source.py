"""Helper process framing source tests using a fake helper executable."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from core.voice_capture.config import VoiceCaptureConfig
from core.voice_capture.contracts import CaptureMessageType
from core.voice_capture.framing import encode_frame
from core.voice_capture.macos_coreaudio import MacOSCoreAudioFrameSource
from core.voice_capture.process import HelperProcess, HelperProcessError
from core.voice_streaming.live_audio import AudioFrameSourceReason


def _write_fake_helper(path: Path, script: str) -> None:
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_source_rejects_wrong_helper_name(tmp_path: Path):
    helper = tmp_path / "not-hikari"
    _write_fake_helper(helper, "#!/bin/sh\nexit 0\n")
    src = MacOSCoreAudioFrameSource(
        stream_id="s1", helper_path=helper, helper_root=tmp_path,
        now_ns=lambda: 1_000_000_000_000,
    )
    result = src.open()
    assert result.accepted is False


def test_helper_rejects_correctly_named_binary_outside_allowed_root(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    helper = outside / "hikari-macos-audio-capture"
    _write_fake_helper(helper, "#!/bin/sh\nexit 0\n")
    with pytest.raises(HelperProcessError, match="helper_path_rejected"):
        HelperProcess(executable=helper, args=(), allowed_root=allowed)


def test_helper_rejects_arbitrary_arguments(tmp_path: Path):
    helper = tmp_path / "hikari-macos-audio-capture"
    _write_fake_helper(helper, "#!/bin/sh\nexit 0\n")

    with pytest.raises(HelperProcessError, match="helper_args_rejected"):
        HelperProcess(
            executable=helper,
            args=("unreviewed-mode",),
            allowed_root=tmp_path,
        )


def test_handshake_and_pcm_frames(tmp_path: Path):
    helper = tmp_path / "hikari-macos-audio-capture"
    # Fake helper: emit ready then one pcm frame then end on stdout.
    ready = encode_frame(CaptureMessageType.READY, sequence=0, monotonic_ns=1_000_000_000_000, payload=b'{"capability":"frame_stream"}')
    pcm = encode_frame(
        CaptureMessageType.PCM,
        sequence=1,
        monotonic_ns=1_000_000_000_010,
        payload=b"\x01\x00" * 160,
    )
    end = encode_frame(CaptureMessageType.END, sequence=2, monotonic_ns=1_000_000_000_020, payload=b"")
    blob = ready + pcm + end
    script = f"""#!/usr/bin/env python3
import sys
sys.stdout.buffer.write({blob!r})
sys.stdout.buffer.flush()
sys.stdin.read(1)
"""
    _write_fake_helper(helper, script)
    clock = {"t": 1_000_000_000_000}

    def now():
        return clock["t"]

    src = MacOSCoreAudioFrameSource(
        stream_id="stream-a",
        helper_path=helper,
        helper_root=tmp_path,
        now_ns=now,
        config=VoiceCaptureConfig(handshake_timeout_s=2.0, frame_read_timeout_s=2.0),
    )
    assert src.open().accepted is True
    frame = src.read_frame()
    assert frame.accepted is True
    assert frame.frame is not None
    assert frame.frame.sequence == 1
    # duplicate rejection via second identical sequence would need replay; instead close
    closed = src.close()
    assert closed.reason == AudioFrameSourceReason.CLOSED


def test_duplicate_and_out_of_order(tmp_path: Path):
    helper = tmp_path / "hikari-macos-audio-capture"
    ready = encode_frame(CaptureMessageType.READY, sequence=0, monotonic_ns=1_000_000_000_000, payload=b"{}")
    f1 = encode_frame(CaptureMessageType.PCM, sequence=1, monotonic_ns=1_000_000_000_100, payload=b"\x00\x01" * 80)
    f1_dup = encode_frame(CaptureMessageType.PCM, sequence=1, monotonic_ns=1_000_000_000_110, payload=b"\x00\x02" * 80)
    f0 = encode_frame(CaptureMessageType.PCM, sequence=0, monotonic_ns=1_000_000_000_120, payload=b"\x00\x03" * 80)
    blob = ready + f1 + f1_dup + f0
    script = f"""#!/usr/bin/env python3
import sys
sys.stdout.buffer.write({blob!r})
sys.stdout.buffer.flush()
import time; time.sleep(0.2)
"""
    _write_fake_helper(helper, script)
    src = MacOSCoreAudioFrameSource(
        stream_id="s",
        helper_path=helper,
        helper_root=tmp_path,
        now_ns=lambda: 1_000_000_000_000,
    )
    assert src.open().accepted
    assert src.read_frame().accepted
    dup = src.read_frame()
    assert dup.accepted is False
    assert dup.reason == AudioFrameSourceReason.DUPLICATE_FRAME
    ooo = src.read_frame()
    assert ooo.reason == AudioFrameSourceReason.OUT_OF_ORDER
    src.close()


def test_monotonic_timestamp_regression_rejected(tmp_path: Path):
    helper = tmp_path / "hikari-macos-audio-capture"
    ready = encode_frame(CaptureMessageType.READY, sequence=0, monotonic_ns=1_000, payload=b"{}")
    f1 = encode_frame(CaptureMessageType.PCM, sequence=1, monotonic_ns=1_100, payload=b"\x00\x01" * 80)
    f2 = encode_frame(CaptureMessageType.PCM, sequence=2, monotonic_ns=1_050, payload=b"\x00\x02" * 80)
    blob = ready + f1 + f2
    script = f"""#!/usr/bin/env python3
import sys, time
sys.stdout.buffer.write({blob!r})
sys.stdout.buffer.flush()
time.sleep(0.2)
"""
    _write_fake_helper(helper, script)
    src = MacOSCoreAudioFrameSource(
        stream_id="s",
        helper_path=helper,
        helper_root=tmp_path,
        now_ns=lambda: 1_200,
    )
    assert src.open().accepted
    assert src.read_frame().accepted
    assert src.read_frame().reason == AudioFrameSourceReason.OUT_OF_ORDER
    src.close()
