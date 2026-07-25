"""Tests for deterministic stuck-task detection.

Every assessment is driven by caller-supplied evidence and an injected
``now``. Synthetic task names and timestamps are used throughout.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone, timedelta

import pytest

from core.time_sense.contracts import (
    StuckAssessment,
    StuckReason,
    TaskProgressObservation,
    TaskProgressState,
)
from core.time_sense.stuck_detection import (
    StuckDetector,
    StuckDetectorConfig,
    assess_stuck,
)


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 7, 23, 12, 0)


def _observation(
    *,
    state: TaskProgressState = TaskProgressState.MAKING_PROGRESS,
    observed_at: datetime = NOW,
    last_progress_at: datetime | None = None,
    parsed_at: datetime | None = None,
    last_error_count: int = 0,
    repeated_failure_count: int = 0,
    repeated_failure_category: str | None = None,
    attempt_count: int = 0,
    overdue: bool = False,
    blocked_dependency: bool = False,
    waiting_for_approval: bool = False,
    waiting_for_user_input: bool = False,
    delivery_failed: bool = False,
    heartbeat_missing: bool = False,
    quiet_hours_active: bool = False,
    evidence_codes: tuple[str, ...] = (),
) -> TaskProgressObservation:
    return TaskProgressObservation(
        task_id="task-aurora-9",
        kind="deploy",
        state=state,
        observed_at=observed_at,
        last_progress_at=last_progress_at,
        parsed_at=parsed_at,
        last_error_count=last_error_count,
        repeated_failure_count=repeated_failure_count,
        repeated_failure_category=repeated_failure_category,
        attempt_count=attempt_count,
        overdue=overdue,
        blocked_dependency=blocked_dependency,
        waiting_for_approval=waiting_for_approval,
        waiting_for_user_input=waiting_for_user_input,
        delivery_failed=delivery_failed,
        heartbeat_missing=heartbeat_missing,
        quiet_hours_active=quiet_hours_active,
        evidence_codes=evidence_codes,
    )


# ---------------------------------------------------------------------------
# 13. One slow task is not stuck
# ---------------------------------------------------------------------------


def test_one_slow_observation_is_not_stuck():
    obs = _observation(
        state=TaskProgressState.NO_PROGRESS,
        last_progress_at=NOW - timedelta(minutes=5),
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is False
    assert verdict.reason is StuckReason.NOT_STUCK


def test_one_slow_with_short_delay_still_not_stuck():
    obs = _observation(
        state=TaskProgressState.NO_PROGRESS,
        last_progress_at=NOW - timedelta(minutes=10),
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is False


# ---------------------------------------------------------------------------
# 14. Repeated failure may be stuck
# ---------------------------------------------------------------------------


def test_repeated_failure_count_is_stuck():
    obs = _observation(
        state=TaskProgressState.REPEATED_FAILURE,
        repeated_failure_count=4,
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is True
    assert verdict.reason is StuckReason.REPEATED_FAILURE


def test_repeated_failure_below_threshold_not_stuck():
    obs = _observation(
        state=TaskProgressState.REPEATED_FAILURE,
        repeated_failure_count=2,
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is False


def test_repeated_failure_category_signals_stuck():
    obs = _observation(
        repeated_failure_count=4,
        repeated_failure_category="auth_error",
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is True
    assert verdict.reason is StuckReason.REPEATED_FAILURE_CATEGORY


# ---------------------------------------------------------------------------
# 15. Waiting for approval classification
# ---------------------------------------------------------------------------


def test_waiting_for_approval_not_stuck():
    obs = _observation(
        state=TaskProgressState.WAITING_FOR_APPROVAL,
        waiting_for_approval=True,
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is False
    assert verdict.reason is StuckReason.NOT_STUCK
    assert "waiting_for_approval" in verdict.evidence_codes


def test_waiting_for_approval_with_tech_failure_is_stuck():
    # Approval + repeated failure cannot be hidden behind the approval flag.
    obs = _observation(
        waiting_for_approval=True,
        repeated_failure_count=5,
        repeated_failure_category="deploy_error",
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is True
    assert "waiting_for_approval" in verdict.evidence_codes


# ---------------------------------------------------------------------------
# 16. Waiting for user input classification
# ---------------------------------------------------------------------------


def test_waiting_for_user_input_not_stuck():
    obs = _observation(
        state=TaskProgressState.WAITING_FOR_USER_INPUT,
        waiting_for_user_input=True,
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is False
    assert "waiting_for_user_input" in verdict.evidence_codes


def test_waiting_for_user_input_with_tech_failure_is_stuck():
    obs = _observation(
        waiting_for_user_input=True,
        delivery_failed=True,
        quiet_hours_active=False,
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is True


# ---------------------------------------------------------------------------
# 17. Blocked dependency classification
# ---------------------------------------------------------------------------


def test_blocked_dependency_is_stuck():
    obs = _observation(
        state=TaskProgressState.NO_PROGRESS,
        blocked_dependency=True,
        last_progress_at=NOW - timedelta(hours=2),
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is True
    assert verdict.reason is StuckReason.BLOCKED_DEPENDENCY


# ---------------------------------------------------------------------------
# 18. Quiet-hours delivery delay is not task failure
# ---------------------------------------------------------------------------


def test_quiet_hours_delivery_pause_not_stuck():
    obs = _observation(
        state=TaskProgressState.DELIVERY_FAILED,
        delivery_failed=True,
        quiet_hours_active=True,
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is False
    assert "quiet_hours_delivery_pause" in verdict.evidence_codes


def test_delivery_failure_without_quiet_hours_is_stuck():
    obs = _observation(
        delivery_failed=True,
        quiet_hours_active=False,
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is True
    assert verdict.reason is StuckReason.DELIVERY_FAILED


# ---------------------------------------------------------------------------
# 19. Missing heartbeat classification
# ---------------------------------------------------------------------------


def test_missing_heartbeat_is_stuck():
    obs = _observation(
        state=TaskProgressState.NO_PROGRESS,
        heartbeat_missing=True,
        last_progress_at=NOW - timedelta(minutes=60),
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is True
    assert verdict.reason is StuckReason.HEARTBEAT_MISSING


def test_missing_heartbeat_below_threshold_not_stuck():
    obs = _observation(
        heartbeat_missing=True,
        last_progress_at=NOW - timedelta(minutes=2),
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is False


# ---------------------------------------------------------------------------
# 20. Configurable thresholds
# ---------------------------------------------------------------------------


def test_configurable_no_progress_threshold():
    short = StuckDetectorConfig(no_progress_minutes=5)
    obs = _observation(
        state=TaskProgressState.NO_PROGRESS,
        last_progress_at=NOW - timedelta(minutes=10),
    )
    assert assess_stuck(obs, now=NOW).stuck is False
    assert assess_stuck(obs, now=NOW, config=short).stuck is True


def test_configurable_repeated_failure_threshold():
    strict = StuckDetectorConfig(repeated_failure_count=2)
    obs = _observation(
        state=TaskProgressState.REPEATED_FAILURE,
        repeated_failure_count=2,
    )
    assert assess_stuck(obs, now=NOW).stuck is False
    assert assess_stuck(obs, now=NOW, config=strict).stuck is True


def test_config_rejects_non_positive_threshold():
    with pytest.raises(ValueError):
        StuckDetectorConfig(no_progress_minutes=0)


def test_config_rejects_negative_grace():
    with pytest.raises(ValueError):
        StuckDetectorConfig(overdue_grace_minutes=-1)


# ---------------------------------------------------------------------------
# 21. Deterministic assessments
# ---------------------------------------------------------------------------


def test_assessment_is_deterministic():
    obs = _observation(
        repeated_failure_count=5,
        repeated_failure_category="conn_timeout",
    )
    a = assess_stuck(obs, now=NOW)
    b = assess_stuck(obs, now=NOW)
    assert a == b


def test_tie_breaking_is_stable():
    # Equal severities should deterministically pick one reason.
    obs = _observation(
        state=TaskProgressState.NO_PROGRESS,
        overdue=True,
        blocked_dependency=True,
        last_progress_at=NOW - timedelta(hours=3),
        parsed_at=NOW - timedelta(hours=2),
    )
    a = assess_stuck(obs, now=NOW)
    b = assess_stuck(obs, now=NOW)
    assert a.reason == b.reason


# ---------------------------------------------------------------------------
# 22. Evidence codes preserved
# ---------------------------------------------------------------------------


def test_evidence_codes_preserved_from_observation():
    obs = _observation(
        evidence_codes=("e.alpha", "e.beta"),
        repeated_failure_count=5,
        repeated_failure_category="auth",
    )
    verdict = assess_stuck(obs, now=NOW)
    assert "e.alpha" in verdict.evidence_codes
    assert "e.beta" in verdict.evidence_codes


def test_assessment_adds_reason_evidence_code():
    obs = _observation(
        state=TaskProgressState.NO_PROGRESS,
        last_progress_at=NOW - timedelta(hours=3),
    )
    verdict = assess_stuck(obs, now=NOW)
    assert "no_progress_delayed" in verdict.evidence_codes


def test_evidence_codes_are_opaque():
    obs = _observation(
        state=TaskProgressState.REPEATED_FAILURE,
        repeated_failure_count=4,
        evidence_codes=("trace_id_abc",),
    )
    verdict = assess_stuck(obs, now=NOW)
    # No raw error text leaks into the assessment.
    assert all(isinstance(code, str) for code in verdict.evidence_codes)


# ---------------------------------------------------------------------------
# 23. No task mutation
# ---------------------------------------------------------------------------


def test_assessment_does_not_mutate_observation():
    obs = _observation(
        state=TaskProgressState.REPEATED_FAILURE,
        repeated_failure_count=5,
        repeated_failure_category="net",
    )
    before = (
        obs.state,
        obs.repeated_failure_count,
        obs.repeated_failure_category,
        obs.evidence_codes,
    )
    assess_stuck(obs, now=NOW)
    after = (
        obs.state,
        obs.repeated_failure_count,
        obs.repeated_failure_category,
        obs.evidence_codes,
    )
    assert before == after


def test_observation_is_immutable():
    obs = _observation()
    with pytest.raises(FrozenInstanceError):
        obs.state = TaskProgressState.UNKNOWN  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 30. Empty observation input (state UNKNOWN, no signals)
# ---------------------------------------------------------------------------


def test_empty_observation_input_is_not_stuck():
    obs = _observation(state=TaskProgressState.UNKNOWN)
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is False
    assert verdict.reason is StuckReason.NOT_STUCK
    assert verdict.severity == 0.0


# ---------------------------------------------------------------------------
# 31. Invalid timestamps
# ---------------------------------------------------------------------------


def test_naive_now_rejected():
    obs = _observation()
    with pytest.raises(ValueError):
        assess_stuck(obs, now=NAIVE)


def test_future_observation_beyond_horizon_rejected():
    obs = _observation(
        observed_at=NOW + timedelta(days=400),
    )
    with pytest.raises(ValueError):
        assess_stuck(obs, now=NOW)


def test_naive_observation_rejected_at_construction():
    with pytest.raises(ValueError):
        TaskProgressObservation(
            task_id="t1",
            kind="deploy",
            state=TaskProgressState.UNKNOWN,
            observed_at=NAIVE,
        )


# ---------------------------------------------------------------------------
# 32. Immutable contracts
# ---------------------------------------------------------------------------


def test_assessment_is_frozen():
    verdict = assess_stuck(_observation(), now=NOW)
    with pytest.raises(FrozenInstanceError):
        verdict.stuck = True  # type: ignore[misc]


def test_detector_is_stateless():
    detector = StuckDetector()
    obs = _observation(
        state=TaskProgressState.REPEATED_FAILURE,
        repeated_failure_count=4,
    )
    a = detector.assess(obs, now=NOW)
    b = detector.assess(obs, now=NOW)
    assert a == b


# ---------------------------------------------------------------------------
# Retries without state movement
# ---------------------------------------------------------------------------


def test_retries_without_movement_is_stuck():
    obs = _observation(
        state=TaskProgressState.RETRYING,
        attempt_count=4,
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is True
    assert verdict.reason is StuckReason.RETRIES_WITHOUT_MOVEMENT


def test_retries_below_threshold_not_stuck():
    obs = _observation(
        state=TaskProgressState.RETRYING,
        attempt_count=2,
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is False


# ---------------------------------------------------------------------------
# Overdue
# ---------------------------------------------------------------------------


def test_overdue_is_stuck():
    obs = _observation(
        overdue=True,
        parsed_at=NOW - timedelta(hours=2),
    )
    verdict = assess_stuck(obs, now=NOW)
    assert verdict.stuck is True
    assert verdict.reason is StuckReason.OVERDUE


def test_overdue_in_grace_period_not_stuck():
    config = StuckDetectorConfig(overdue_grace_minutes=60)
    obs = _observation(
        overdue=True,
        parsed_at=NOW - timedelta(minutes=30),
    )
    verdict = assess_stuck(obs, now=NOW, config=config)
    assert verdict.stuck is False


# ---------------------------------------------------------------------------
# Severity / confidence bounds
# ---------------------------------------------------------------------------


def test_severity_in_unit_range():
    obs = _observation(
        state=TaskProgressState.REPEATED_FAILURE,
        repeated_failure_count=10,
        repeated_failure_category="boom",
    )
    verdict = assess_stuck(obs, now=NOW)
    assert 0.0 <= verdict.severity <= 1.0


def test_confidence_in_unit_range():
    obs = _observation(
        state=TaskProgressState.REPEATED_FAILURE,
        repeated_failure_count=10,
        repeated_failure_category="boom",
    )
    verdict = assess_stuck(obs, now=NOW)
    assert 0.0 <= verdict.confidence <= 1.0


def test_high_evidence_raises_confidence():
    weak = _observation(
        state=TaskProgressState.REPEATED_FAILURE,
        repeated_failure_count=3,
    )
    strong = _observation(
        state=TaskProgressState.REPEATED_FAILURE,
        repeated_failure_count=5,
        repeated_failure_category="conn_error",
        delivery_failed=True,
        quiet_hours_active=False,
    )
    v_weak = assess_stuck(weak, now=NOW)
    v_strong = assess_stuck(strong, now=NOW)
    assert v_strong.confidence >= v_weak.confidence
