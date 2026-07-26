"""Adversarial tests for Time Sense runtime bridge."""

from __future__ import annotations

import ast
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.time_sense.runtime_bridge import (
    RuntimeBridgeConfig,
    TimeSenseRuntimeBridge,
)
from core.time_sense.session_policy import (
    ConversationTimingObservation,
    TimingAction,
    TimingReason,
)


NOW = datetime(2026, 7, 26, 16, 0, tzinfo=timezone.utc)


def obs(**kwargs):
    base = dict(
        session_id="sess-1",
        observed_at=NOW,
        pause_age_seconds=2.0,
        last_user_speech_age_seconds=2.0,
        last_assistant_response_age_seconds=5.0,
        conversation_active=True,
        sleeping=False,
        quiet_hours=False,
        recent_dismissal=False,
        child_mode=False,
        privacy_suppression=False,
        user_speaking=False,
        assistant_speaking=False,
    )
    base.update(kwargs)
    return ConversationTimingObservation(**base)


def test_suppress_during_sleep_quiet_child_privacy_speech_dismissal():
    bridge = TimeSenseRuntimeBridge(lambda: NOW)
    assert bridge.ingest(obs(sleeping=True)).action == TimingAction.SUPPRESS
    assert bridge.ingest(obs(quiet_hours=True)).reason == TimingReason.QUIET_HOURS
    assert bridge.ingest(obs(child_mode=True)).reason == TimingReason.CHILD_MODE
    assert bridge.ingest(obs(privacy_suppression=True)).reason == TimingReason.PRIVACY_SUPPRESSION
    assert bridge.ingest(obs(user_speaking=True)).reason == TimingReason.ACTIVE_SPEECH
    assert bridge.ingest(obs(assistant_speaking=True)).reason == TimingReason.ACTIVE_SPEECH
    assert bridge.ingest(obs(recent_dismissal=True)).reason == TimingReason.RECENT_DISMISSAL


def test_respond_when_evidence_allows():
    bridge = TimeSenseRuntimeBridge(lambda: NOW)
    advisory = bridge.ingest(obs(pause_age_seconds=2.0))
    assert advisory.action == TimingAction.RESPOND
    assert bridge.latest("sess-1") == advisory


def test_stale_observation_suppressed():
    bridge = TimeSenseRuntimeBridge(
        lambda: NOW,
        config=RuntimeBridgeConfig(max_observation_age_seconds=60),
    )
    old = obs(observed_at=NOW - timedelta(hours=2))
    assert bridge.ingest(old).reason == TimingReason.INVALID_EVIDENCE


def test_session_bound_eviction():
    bridge = TimeSenseRuntimeBridge(
        lambda: NOW,
        config=RuntimeBridgeConfig(max_tracked_sessions=2),
    )
    bridge.ingest(obs(session_id="a"))
    bridge.ingest(obs(session_id="b"))
    bridge.ingest(obs(session_id="c"))
    ids = {item.session_id for item in bridge.snapshot()}
    assert len(ids) == 2
    assert "c" in ids


def test_no_transcript_storage_and_content_free_repr():
    bridge = TimeSenseRuntimeBridge(lambda: NOW)
    advisory = bridge.ingest(obs())
    assert "hello" not in repr(advisory)
    assert not hasattr(advisory, "transcript")
    assert "sess-1" not in repr(advisory)


def test_config_rejects_non_finite_or_excessive_observation_age():
    for value in (math.nan, math.inf, -math.inf, 2_592_001.0):
        with pytest.raises(ValueError, match="invalid_max_observation_age_seconds"):
            RuntimeBridgeConfig(max_observation_age_seconds=value)


def test_bridge_module_has_no_io_imports():
    path = Path("core/time_sense/runtime_bridge.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    for forbidden in ("socket", "subprocess", "sqlite3", "httpx", "requests", "urllib"):
        assert forbidden not in names
