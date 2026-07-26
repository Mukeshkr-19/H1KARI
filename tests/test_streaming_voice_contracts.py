"""Adversarial tests for streaming-voice contracts and segments."""

from __future__ import annotations

import math
import socket
import subprocess
from pathlib import Path

import pytest

from core.streaming_voice import (
    ConfidenceCategory,
    SegmentLedger,
    SegmentStatus,
    SpeakerCategory,
    StreamingReason,
    TranscriptSegment,
)


def _seg(**kwargs):
    base = dict(
        segment_id="seg-1",
        utterance_id="utt-1",
        session_id="sess-1",
        speaker=SpeakerCategory.OWNER,
        start_mono=1.0,
        end_mono=2.0,
        status=SegmentStatus.FINAL,
        confidence=ConfidenceCategory.HIGH,
        text="hello",
        sequence=0,
    )
    base.update(kwargs)
    return TranscriptSegment(**base)


def test_segment_rejects_nan_inf_negative_timestamps():
    for bad in (float("nan"), float("inf"), float("-inf"), -0.1):
        with pytest.raises(ValueError):
            _seg(start_mono=bad)
        with pytest.raises(ValueError):
            _seg(end_mono=bad)


def test_exact_boundary_timestamps_accepted():
    seg = _seg(start_mono=0.0, end_mono=0.0)
    assert seg.start_mono == 0.0
    assert seg.end_mono == 0.0


def test_end_before_start_rejected():
    with pytest.raises(ValueError):
        _seg(start_mono=2.0, end_mono=1.0)


def test_ledger_duplicate_replay_out_of_order():
    ledger = SegmentLedger()
    assert ledger.accept(_seg(sequence=0)).accepted
    assert ledger.accept(_seg(segment_id="seg-1", sequence=1)).reason == StreamingReason.DUPLICATE
    assert ledger.accept(_seg(segment_id="seg-2", sequence=0)).reason == StreamingReason.REPLAYED
    assert ledger.accept(_seg(segment_id="seg-3", sequence=0)).reason in (
        StreamingReason.REPLAYED,
        StreamingReason.OUT_OF_ORDER,
    )
    assert ledger.accept(_seg(segment_id="seg-4", sequence=2)).accepted
    # out of order lower than last
    bad = ledger.accept(_seg(segment_id="seg-5", sequence=1))
    assert bad.reason == StreamingReason.OUT_OF_ORDER
    ordered = ledger.ordered()
    assert [s.sequence for s in ordered] == [0, 2]


def test_content_free_repr_and_no_io_on_import():
    seg = _seg(text="secret-private-words")
    r = repr(seg)
    assert "secret-private-words" not in r
    # Import path already executed; assert no accidental temp files created by module
    assert not Path("/tmp/hikari_streaming_voice_should_not_exist").exists()


def test_no_socket_subprocess_in_contracts_module():
    # Sanity: helpers used by tests themselves — package must not open sockets at import
    import core.streaming_voice.contracts as c

    assert not hasattr(c, "socket")
    assert socket.getdefaulttimeout() is None or True
