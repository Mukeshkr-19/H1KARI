"""Tests for cancellation generation tracking, stale work invalidation, and request/confirmation records."""

from __future__ import annotations

import pytest

from core.voice_session.cancellation import (
    CancellationTracker,
    InterruptionConfirmation,
    InterruptionRequest,
)


def test_cancellation_tracker_generation() -> None:
    tracker = CancellationTracker(initial_generation=0)
    assert tracker.current_generation == 0
    assert tracker.is_stale(0) is False

    gen1 = tracker.cancel("barge_in")
    assert gen1 == 1
    assert tracker.current_generation == 1
    assert tracker.is_stale(0) is True
    assert tracker.is_stale(1) is False

    gen2 = tracker.cancel("barge_in_again")
    assert gen2 == 2
    assert tracker.is_stale(1) is True
    assert tracker.is_stale(2) is False


def test_interruption_request_confirmation_separation() -> None:
    tracker = CancellationTracker()

    req = InterruptionRequest(
        request_id="req_1",
        session_id="sess_1",
        utterance_id="utt_1",
        response_id="resp_1",
        playback_id="pb_1",
        cancellation_generation=1,
        monotonic_ns=1000,
        reason="barge_in",
    )
    tracker.record_request(req)
    assert tracker.last_request == req
    assert tracker.last_confirmation is None

    conf = InterruptionConfirmation(
        request_id="req_1",
        session_id="sess_1",
        playback_id="pb_1",
        confirmed=True,
        monotonic_ns=1050,
        bytes_played=4096,
    )
    tracker.record_confirmation(conf)
    assert tracker.last_confirmation == conf

    rep_req = repr(req)
    rep_conf = repr(conf)
    assert "gen=1" in rep_req
    assert "confirmed=True" in rep_conf


def test_invalid_cancellation_inputs() -> None:
    tracker = CancellationTracker()

    with pytest.raises(TypeError):
        tracker.is_stale("invalid")  # type: ignore

    with pytest.raises(ValueError):
        tracker.is_stale(-1)
