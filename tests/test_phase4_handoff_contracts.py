"""Phase 4 handoff contract validation and privacy tests."""

from __future__ import annotations

import re

import pytest

from core.action_policy import Actor, ActorContext
from core.handoff.contracts import (
    FrozenHandoffPreview,
    HandoffErrorCode,
    HandoffRecord,
    HandoffResult,
    HandoffState,
    make_offer_record,
)


@pytest.fixture
def owner_actor() -> ActorContext:
    return ActorContext(
        actor_id="local-owner",
        actor=Actor.OWNER,
        session_id="session-1",
        source="local",
    )


def test_frozen_preview_valid():
    preview = FrozenHandoffPreview(task_id="task-123", summary="Review quarterly report")
    assert preview.task_id == "task-123"
    assert preview.summary == "Review quarterly report"
    assert re.fullmatch(r"^[0-9a-f]{64}$", preview.snapshot_digest)


def test_frozen_preview_preserves_valid_whitespace():
    preview = FrozenHandoffPreview(task_id="task-123", summary="  valid summary  ")
    assert preview.summary == "  valid summary  "


def test_frozen_preview_rejects_whitespace_only_summary():
    with pytest.raises(ValueError, match="whitespace-only"):
        FrozenHandoffPreview(task_id="task-123", summary="   ")


def test_frozen_preview_rejects_unicode_whitespace_only():
    with pytest.raises(ValueError, match="whitespace-only"):
        FrozenHandoffPreview(task_id="task-123", summary="\u2003\u3000")


def test_frozen_preview_rejects_control_chars():
    with pytest.raises(ValueError, match="control characters"):
        FrozenHandoffPreview(task_id="task-123", summary="hello\x00world")
    with pytest.raises(ValueError, match="control characters"):
        FrozenHandoffPreview(task_id="task-123", summary="hello\x7fworld")


def test_frozen_preview_rejects_unicode_cf():
    with pytest.raises(ValueError, match="Unicode format"):
        FrozenHandoffPreview(task_id="task-123", summary="hello\u200bworld")


def test_frozen_preview_rejects_empty_summary():
    with pytest.raises(ValueError, match="summary length"):
        FrozenHandoffPreview(task_id="task-123", summary="")


def test_frozen_preview_rejects_overlong_summary():
    with pytest.raises(ValueError, match="summary length"):
        FrozenHandoffPreview(task_id="task-123", summary="x" * 201)


def test_frozen_preview_rejects_invalid_task_id():
    with pytest.raises(ValueError, match="invalid task_id"):
        FrozenHandoffPreview(task_id="task with spaces", summary="summary")
    with pytest.raises(ValueError, match="invalid task_id"):
        FrozenHandoffPreview(task_id="", summary="summary")


def test_frozen_preview_repr_is_content_free():
    preview = FrozenHandoffPreview(task_id="task-123", summary="secret summary")
    rep = repr(preview)
    assert "task-123" not in rep
    assert "secret" not in rep
    assert "snapshot_digest" not in rep


def test_handoff_record_valid(owner_actor: ActorContext):
    preview = FrozenHandoffPreview(task_id="task-123", summary="Review report")
    record = make_offer_record(
        handoff_id="h-1",
        actor=owner_actor,
        preview=preview,
        request_id="req-1",
        created_at=1000.0,
    )
    assert record.handoff_id == "h-1"
    assert record.actor_id == "local-owner"
    assert record.session_id == "session-1"
    assert record.task_id == "task-123"
    assert record.summary == "Review report"
    assert record.snapshot_digest == preview.snapshot_digest
    assert record.state is HandoffState.OFFERED
    assert record.expires_at == 1000.0 + 15 * 60
    assert record.request_id == "req-1"
    assert record.revision == 1


def test_handoff_record_repr_is_content_free(owner_actor: ActorContext):
    preview = FrozenHandoffPreview(task_id="task-123", summary="secret summary")
    record = make_offer_record(
        handoff_id="h-1",
        actor=owner_actor,
        preview=preview,
        request_id="req-1",
        created_at=1000.0,
    )
    rep = repr(record)
    for forbidden in [
        "task-123",
        "secret",
        "local-owner",
        "session-1",
        "h-1",
        "req-1",
        "1000",
        "1900",
    ]:
        assert forbidden not in rep
    assert "state=" in rep


def test_handoff_record_rejects_non_offered_state(owner_actor: ActorContext):
    preview = FrozenHandoffPreview(task_id="task-123", summary="Review report")
    with pytest.raises(ValueError, match="expires_at"):
        HandoffRecord(
            handoff_id="h-1",
            actor_id="local-owner",
            session_id="session-1",
            task_id=preview.task_id,
            summary=preview.summary,
            snapshot_digest=preview.snapshot_digest,
            state=HandoffState.ACCEPTED,
            created_at=1000.0,
            expires_at=2000.0,
            request_id="req-1",
        )


def test_handoff_record_rejects_invalid_handoff_id(owner_actor: ActorContext):
    preview = FrozenHandoffPreview(task_id="task-123", summary="Review report")
    with pytest.raises(ValueError, match="invalid handoff_id"):
        make_offer_record(
            handoff_id="BAD-ID",
            actor=owner_actor,
            preview=preview,
            request_id="req-1",
            created_at=1000.0,
        )


def test_handoff_record_rejects_invalid_revision(owner_actor: ActorContext):
    preview = FrozenHandoffPreview(task_id="task-123", summary="Review report")
    with pytest.raises(ValueError, match="revision"):
        HandoffRecord(
            handoff_id="h-1",
            actor_id="local-owner",
            session_id="session-1",
            task_id=preview.task_id,
            summary=preview.summary,
            snapshot_digest=preview.snapshot_digest,
            state=HandoffState.OFFERED,
            created_at=1000.0,
            expires_at=1000.0 + 15 * 60,
            request_id="req-1",
            revision=0,
        )


def test_handoff_result_success_requires_no_error_code():
    with pytest.raises(ValueError, match="error_code"):
        HandoffResult(success=True, error_code=HandoffErrorCode.UNAVAILABLE)


def test_handoff_result_failure_requires_error_code():
    with pytest.raises(ValueError, match="error_code"):
        HandoffResult(success=False)


def test_handoff_result_repr_is_content_free():
    result = HandoffResult(
        success=True,
        request_id="req-1",
        handoff_id="h-1",
        state=HandoffState.OFFERED,
    )
    rep = repr(result)
    assert "req-1" not in rep
    assert "h-1" not in rep
    assert "success=True" in rep
    assert "offered" in rep


def test_handoff_result_error_repr_is_content_free():
    result = HandoffResult(
        success=False,
        handoff_id="h-1",
        error_code=HandoffErrorCode.HANDOFF_NOT_FOUND,
    )
    rep = repr(result)
    assert "h-1" not in rep
    assert "handoff_not_found" in rep


def test_handoff_state_terminal_status():
    assert not HandoffState.OFFERED.is_terminal
    assert HandoffState.ACCEPTED.is_terminal
    assert HandoffState.REJECTED.is_terminal
    assert HandoffState.CANCELLED.is_terminal
    assert HandoffState.EXPIRED.is_terminal


def test_handoff_error_codes_are_safe_strings():
    for code in HandoffErrorCode:
        assert isinstance(code.value, str)
        assert " " not in code.value
