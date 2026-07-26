"""Synthetic remote-worker contract tests. No networking."""

from __future__ import annotations

import pytest

from core.phase6_agent.contracts import (
    FailureCode,
    RemoteWorkerAuthorityEnvelope,
    RemoteWorkerResult,
)
from core.phase6_agent.remote_worker import (
    RemoteValidationOutcome,
    accept_remote_result,
    consume_nonce,
    validate_remote_envelope,
)


def _envelope(**kwargs) -> RemoteWorkerAuthorityEnvelope:
    base = dict(
        envelope_id="env-1",
        worker_id="worker-1",
        task_id="task-1",
        capability="analyze",
        targets=("repo.main",),
        expires_at_mono=100.0,
        nonce="aabbccddeeff0011",
        max_responses=2,
    )
    base.update(kwargs)
    return RemoteWorkerAuthorityEnvelope(**base)


def _verify_ok(envelope, signature: bytes) -> bool:
    return signature == b"sig-ok"


def test_valid_envelope():
    env = _envelope()
    result = validate_remote_envelope(
        env,
        now_mono=50.0,
        expected_worker_id="worker-1",
        expected_task_id="task-1",
        expected_capability="analyze",
        expected_targets=("repo.main",),
        consumed_nonces=set(),
        signature=b"sig-ok",
        verify_signature=_verify_ok,
    )
    assert result.outcome is RemoteValidationOutcome.VALID


def test_expired_revoked_replayed_mismatched():
    env = _envelope(expires_at_mono=10.0)
    assert (
        validate_remote_envelope(
            env,
            now_mono=50.0,
            expected_worker_id="worker-1",
            expected_task_id="task-1",
            expected_capability="analyze",
            expected_targets=("repo.main",),
            consumed_nonces=set(),
            signature=b"sig-ok",
            verify_signature=_verify_ok,
        ).outcome
        is RemoteValidationOutcome.EXPIRED
    )
    env2 = _envelope(revoked=True)
    assert (
        validate_remote_envelope(
            env2,
            now_mono=50.0,
            expected_worker_id="worker-1",
            expected_task_id="task-1",
            expected_capability="analyze",
            expected_targets=("repo.main",),
            consumed_nonces=set(),
            signature=b"sig-ok",
            verify_signature=_verify_ok,
        ).outcome
        is RemoteValidationOutcome.REVOKED
    )
    consumed = {"aabbccddeeff0011"}
    assert (
        validate_remote_envelope(
            _envelope(),
            now_mono=50.0,
            expected_worker_id="worker-1",
            expected_task_id="task-1",
            expected_capability="analyze",
            expected_targets=("repo.main",),
            consumed_nonces=consumed,
            signature=b"sig-ok",
            verify_signature=_verify_ok,
        ).outcome
        is RemoteValidationOutcome.REPLAYED
    )
    assert (
        validate_remote_envelope(
            _envelope(),
            now_mono=50.0,
            expected_worker_id="worker-2",
            expected_task_id="task-1",
            expected_capability="analyze",
            expected_targets=("repo.main",),
            consumed_nonces=set(),
            signature=b"sig-ok",
            verify_signature=_verify_ok,
        ).outcome
        is RemoteValidationOutcome.MISMATCH
    )


def test_unsigned_and_bad_signature():
    env = _envelope()
    assert (
        validate_remote_envelope(
            env,
            now_mono=50.0,
            expected_worker_id="worker-1",
            expected_task_id="task-1",
            expected_capability="analyze",
            expected_targets=("repo.main",),
            consumed_nonces=set(),
            signature=None,
            verify_signature=_verify_ok,
        ).outcome
        is RemoteValidationOutcome.UNSIGNED
    )
    assert (
        validate_remote_envelope(
            env,
            now_mono=50.0,
            expected_worker_id="worker-1",
            expected_task_id="task-1",
            expected_capability="analyze",
            expected_targets=("repo.main",),
            consumed_nonces=set(),
            signature=b"bad",
            verify_signature=_verify_ok,
        ).outcome
        is RemoteValidationOutcome.UNSIGNED
    )


def test_remote_cannot_elevate_and_result_untrusted_until_validated():
    env = _envelope()
    validation = validate_remote_envelope(
        env,
        now_mono=50.0,
        expected_worker_id="worker-1",
        expected_task_id="task-1",
        expected_capability="analyze",
        expected_targets=("repo.main",),
        consumed_nonces=set(),
        signature=b"sig-ok",
        verify_signature=_verify_ok,
    )
    remote = RemoteWorkerResult(
        result_id="res-1",
        envelope_id="env-1",
        worker_id="worker-1",
        task_id="task-1",
        summary="looks done",
        observed_at_mono=60.0,
    )
    accepted = accept_remote_result(
        remote,
        envelope=env,
        envelope_validation=validation,
        response_count=0,
    )
    assert accepted.accepted_as_evidence is True
    assert accepted.can_mark_task_success is False
    assert accepted.can_execute_local_action is False
    with pytest.raises(ValueError):
        type(accepted)(
            accepted_as_evidence=True,
            failure_code=None,
            can_mark_task_success=True,
            can_execute_local_action=False,
        )


def test_consume_nonce_replay_protection():
    env = _envelope()
    consumed: set[str] = set()
    assert consume_nonce(consumed, env) is True
    assert consume_nonce(consumed, env) is False


def test_mismatched_remote_result_rejected():
    env = _envelope()
    validation = validate_remote_envelope(
        env,
        now_mono=50.0,
        expected_worker_id="worker-1",
        expected_task_id="task-1",
        expected_capability="analyze",
        expected_targets=("repo.main",),
        consumed_nonces=set(),
        signature=b"sig-ok",
        verify_signature=_verify_ok,
    )
    bad = RemoteWorkerResult(
        result_id="res-1",
        envelope_id="env-1",
        worker_id="worker-other",
        task_id="task-1",
        summary="nope",
        observed_at_mono=60.0,
    )
    accepted = accept_remote_result(
        bad,
        envelope=env,
        envelope_validation=validation,
        response_count=0,
    )
    assert accepted.accepted_as_evidence is False
    assert accepted.failure_code is FailureCode.REMOTE_MISMATCH
