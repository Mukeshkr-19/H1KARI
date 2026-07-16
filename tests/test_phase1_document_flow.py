from __future__ import annotations

import sqlite3
from pathlib import Path

from core.action_audit import ActionAuditStore
from core.action_policy import Actor, ActorContext
from core.document_reader import DocumentReadError, TextDocument
from core.document_service import DocumentService
from core.grants import GrantStore
from core.policy_service import PolicyService
from core.router import GenerationResult
from core.tasks import (
    InMemoryTaskStore,
    SqliteTaskStore,
    TaskIntentService,
    TaskRecordContext,
    TaskStatus,
)


OWNER = ActorContext("owner-a", Actor.OWNER, "session-1")
TASK_CONTEXT = TaskRecordContext(
    speaker_label="owner-a", session_id="session-1", actor="owner"
)


class FakeRouter:
    def __init__(self, responses=None, *, error=False):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    def generate_document(
        self, prompt, *, allowed_providers, before_provider_call, context, **kwargs
    ):
        if self.error:
            raise RuntimeError("private provider detail")
        allowed = tuple(allowed_providers)
        self.calls.append((prompt, context, allowed))
        attempted = []
        for provider in allowed:
            if not before_provider_call(provider):
                continue
            attempted.append(provider)
            text = self.responses.get(provider)
            if text:
                return GenerationResult(text, provider, "fake-model", tuple(attempted))
        return GenerationResult(None, None, None, tuple(attempted))


def _service(tmp_path, *, reader=None, router=None, store=None):
    grants = GrantStore(tmp_path / "grants" / "grants.db")
    audit = ActionAuditStore(tmp_path / "audit" / "audit.db")
    tasks = TaskIntentService(store or InMemoryTaskStore())
    reads = []

    def default_reader(path):
        reads.append(path)
        return TextDocument(Path(path), "private document text", 21)

    service = DocumentService(
        tasks,
        PolicyService(grants, audit),
        router or FakeRouter({"first": "bounded explanation"}),
        reader=reader or default_reader,
    )
    return service, reads, audit


def test_prepare_does_not_read_or_contact_provider(tmp_path):
    router = FakeRouter({"first": "unused"})
    service, reads, _audit = _service(tmp_path, router=router)

    result = service.prepare("relative/private.txt", actor=OWNER, context=TASK_CONTEXT)

    assert result.status == "queued"
    assert reads == []
    assert router.calls == []
    task = service.tasks.get_task(result.task_id, context=TASK_CONTEXT)
    assert task.selected_path == "relative/private.txt"


def test_confirm_reads_original_path_once_and_uses_exact_fallback(tmp_path):
    router = FakeRouter({"second": "fallback explanation"})
    service, reads, audit = _service(tmp_path, router=router)
    prepared = service.prepare("./chosen.txt", actor=OWNER, context=TASK_CONTEXT)

    result = service.confirm_and_explain(
        prepared.task_id, ("first", "second"), actor=OWNER, context=TASK_CONTEXT
    )

    assert reads == ["./chosen.txt"]
    assert router.calls[0][2] == ("first", "second")
    assert result.status == "completed"
    assert result.provider == "second"
    assert result.attempted_providers == ("first", "second")
    assert result.explanation == "fallback explanation"
    records = audit.list_recent(10)
    assert all(row.resource_ref is None or row.resource_ref.startswith("sha256.") for row in records)
    assert "chosen.txt" not in repr(records)
    assert "private document text" not in repr(records)


def test_successful_primary_leaves_no_unused_fallback_grant(tmp_path):
    service, _reads, _audit = _service(
        tmp_path, router=FakeRouter({"first": "primary explanation"})
    )
    prepared = service.prepare("chosen.txt", actor=OWNER, context=TASK_CONTEXT)

    result = service.confirm_and_explain(
        prepared.task_id, ("first", "second"), actor=OWNER, context=TASK_CONTEXT
    )

    assert result.status == "completed"
    with sqlite3.connect(service.policy.grants.db_path) as conn:
        provider_grants = conn.execute(
            "SELECT destination, remaining_uses FROM approval_grants "
            "WHERE action = 'provider.send_document'"
        ).fetchall()
    assert provider_grants == [("first", 0)]


def test_wrong_owner_and_guest_cannot_prepare_or_reconnect(tmp_path):
    service, _reads, _audit = _service(tmp_path)
    prepared = service.prepare("chosen.txt", actor=OWNER, context=TASK_CONTEXT)
    other = ActorContext("owner-b", Actor.OWNER, "session-1")
    other_context = TaskRecordContext(
        speaker_label="owner-b", session_id="session-1", actor="owner"
    )
    guest = ActorContext("guest-a", Actor.GUEST, "session-1")
    guest_context = TaskRecordContext(
        speaker_label="guest-a", session_id="session-1", actor="guest", is_guest=True
    )

    assert service.prepare("x.txt", actor=guest, context=guest_context).error_code == "actor_not_authorized"
    assert service.reconnect(prepared.task_id, actor=other, context=other_context).error_code == "task_not_found"
    assert service.confirm_and_explain(
        prepared.task_id, ("first",), actor=guest, context=guest_context
    ).error_code == "actor_not_authorized"


def test_router_cannot_call_unapproved_destination(tmp_path):
    class IntrusiveRouter(FakeRouter):
        def generate_document(self, prompt, *, before_provider_call, **kwargs):
            assert before_provider_call("unapproved") is False
            return GenerationResult(None, None, None, ())

    service, reads, audit = _service(tmp_path, router=IntrusiveRouter())
    prepared = service.prepare("chosen.txt", actor=OWNER, context=TASK_CONTEXT)
    result = service.confirm_and_explain(
        prepared.task_id, ("approved",), actor=OWNER, context=TASK_CONTEXT
    )

    assert reads == ["chosen.txt"]
    assert result.status == "interrupted"
    assert result.error_code == "provider_not_authorized"
    assert all(row.destination != "unapproved" for row in audit.list_recent())


def test_completed_task_reconnects_from_sqlite_under_new_session(tmp_path):
    db = tmp_path / "tasks.db"
    service, _reads, _audit = _service(
        tmp_path, store=SqliteTaskStore(db), router=FakeRouter({"first": "saved answer"})
    )
    prepared = service.prepare("chosen.txt", actor=OWNER, context=TASK_CONTEXT)
    service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )
    new_actor = ActorContext("owner-a", Actor.OWNER, "session-2")
    new_context = TaskRecordContext(
        speaker_label="owner-a", session_id="session-2", actor="owner"
    )
    fresh_tasks = TaskIntentService(SqliteTaskStore(db))
    fresh = DocumentService(
        fresh_tasks,
        service.policy,
        FakeRouter(),
        reader=lambda _path: (_ for _ in ()).throw(AssertionError("must not read")),
    )

    result = fresh.reconnect(prepared.task_id, actor=new_actor, context=new_context)

    assert result.status == "completed"
    assert result.explanation == "saved answer"


def test_follow_up_uses_prior_explanation_without_rereading(tmp_path):
    router = FakeRouter({"first": "root explanation"})
    service, reads, _audit = _service(tmp_path, router=router)
    prepared = service.prepare("chosen.txt", actor=OWNER, context=TASK_CONTEXT)
    service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )
    router.responses["first"] = "follow-up answer"

    follow = service.follow_up(
        prepared.task_id,
        "What is the conclusion?",
        ("first",),
        actor=OWNER,
        context=TASK_CONTEXT,
    )

    assert reads == ["chosen.txt"]
    assert follow.status == "completed"
    assert follow.explanation == "follow-up answer"
    prompt, document_context, _providers = router.calls[-1]
    assert "root explanation" in prompt
    assert "What is the conclusion?" in prompt
    assert document_context == ""
    child = service.tasks.get_task(follow.task_id, context=TASK_CONTEXT)
    assert child.parent_task_id == prepared.task_id
    assert child.selected_path is None


def test_exposed_follow_up_can_be_cancelled_before_zero_egress_execution(tmp_path):
    router = FakeRouter({"first": "root explanation"})
    service, _reads, _audit = _service(tmp_path, router=router)
    prepared = service.prepare("chosen.txt", actor=OWNER, context=TASK_CONTEXT)
    service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )
    calls_before_follow_up = len(router.calls)

    child = service.prepare_follow_up(
        prepared.task_id, "Why?", actor=OWNER, context=TASK_CONTEXT
    )
    cancelled = service.cancel(child.task_id, actor=OWNER, context=TASK_CONTEXT)
    result = service.execute_follow_up(
        child.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )

    assert child.status == "queued"
    assert cancelled.status == "cancelled"
    assert result.error_code == "invalid_task_state"
    assert len(router.calls) == calls_before_follow_up


def test_reader_failure_is_content_free_and_failed(tmp_path):
    def broken_reader(_path):
        raise DocumentReadError("invalid_utf8", "private path and content")

    service, _reads, audit = _service(tmp_path, reader=broken_reader)
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)
    result = service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )

    assert result.status == "failed"
    assert result.error_code == "invalid_utf8"
    task = service.tasks.get_task(prepared.task_id, context=TASK_CONTEXT)
    assert task.last_error == "invalid_utf8"
    assert "private path" not in repr(audit.list_recent())


def test_read_audit_failure_stops_before_provider(tmp_path):
    router = FakeRouter({"first": "must not be generated"})
    service, reads, _audit = _service(tmp_path, router=router)
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)

    def fail_result(*_args, **_kwargs):
        raise OSError("audit storage unavailable")

    service.policy.audit.record_result = fail_result
    result = service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )

    assert reads == ["secret.txt"]
    assert router.calls == []
    assert result.status == "failed"
    assert result.error_code == "audit_failed"


def test_failed_post_read_transition_stops_before_provider(tmp_path):
    router = FakeRouter({"first": "must not be generated"})
    service, _reads, _audit = _service(tmp_path, router=router)
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)
    service.tasks.update_progress = lambda *_args, **_kwargs: None

    result = service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )

    assert router.calls == []
    assert result.status == "failed"
    assert result.error_code == "task_conflict"


def test_provider_audit_failure_prevents_completion(tmp_path):
    router = FakeRouter({"first": "must not complete"})
    service, _reads, audit = _service(tmp_path, router=router)
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)
    real_record_result = audit.record_result
    calls = 0

    def fail_provider_result(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("audit storage unavailable")
        return real_record_result(*args, **kwargs)

    audit.record_result = fail_provider_result
    result = service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )

    assert router.calls
    assert result.status == "interrupted"
    assert result.error_code == "audit_failed"
    task = service.tasks.get_task(prepared.task_id, context=TASK_CONTEXT)
    assert task.status is TaskStatus.INTERRUPTED
    assert task.result_summary is None


def test_cancellation_during_provider_authorization_sends_no_content(tmp_path):
    sent = []

    class CancellationAwareRouter(FakeRouter):
        def generate_document(self, prompt, *, before_provider_call, **kwargs):
            if before_provider_call("first"):
                sent.append(kwargs["context"])
            return GenerationResult(None, None, None, ())

    service, _reads, audit = _service(tmp_path, router=CancellationAwareRouter())
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)
    real_authorize = service.policy.authorize

    def authorize_then_cancel(request):
        decision = real_authorize(request)
        if request.action == "provider.send_document":
            service.tasks.cancel_task(prepared.task_id, context=TASK_CONTEXT)
        return decision

    service.policy.authorize = authorize_then_cancel
    result = service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )

    assert sent == []
    assert result.status == "cancelled"
    provider_audits = [
        row for row in audit.list_recent() if row.action == "provider.send_document"
    ]
    assert len(provider_audits) == 1
    assert provider_audits[0].result_status == "cancelled"
    assert provider_audits[0].result_code == "task_cancelled"


def test_provider_callback_replay_does_not_reauthorize_or_overwrite_audit(tmp_path):
    class ReplayRouter(FakeRouter):
        def generate_document(self, prompt, *, before_provider_call, context, **kwargs):
            assert before_provider_call("first") is True
            assert before_provider_call("first") is False
            assert before_provider_call("second") is True
            return GenerationResult("answer", "second", "fake-model", ("first", "second"))

    service, _reads, audit = _service(tmp_path, router=ReplayRouter())
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)
    real_authorize = service.policy.authorize
    provider_authorizations = []

    def counting_authorize(request):
        if request.action == "provider.send_document":
            provider_authorizations.append(request.destination)
        return real_authorize(request)

    service.policy.authorize = counting_authorize
    result = service.confirm_and_explain(
        prepared.task_id, ("first", "second"), actor=OWNER, context=TASK_CONTEXT
    )

    assert result.status == "completed"
    assert provider_authorizations == ["first", "second"]
    provider_audits = {
        row.destination: row
        for row in audit.list_recent()
        if row.action == "provider.send_document"
    }
    assert set(provider_audits) == {"first", "second"}
    assert provider_audits["first"].result_status == "failed"
    assert provider_audits["first"].result_code == "provider_failed"
    assert provider_audits["second"].result_status == "success"
    assert provider_audits["second"].result_code is None


def test_provider_failure_is_retryable_and_stable(tmp_path):
    service, reads, audit = _service(tmp_path, router=FakeRouter(error=True))
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)
    result = service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )

    assert reads == ["secret.txt"]
    assert result.status == "interrupted"
    assert result.error_code == "provider_failed"
    task = service.tasks.get_task(prepared.task_id, context=TASK_CONTEXT)
    assert task.status is TaskStatus.INTERRUPTED
    assert service.reconnect(
        prepared.task_id, actor=OWNER, context=TASK_CONTEXT
    ).error_code == "provider_failed"
    assert "private provider detail" not in repr(audit.list_recent())


def test_unknown_reader_error_code_maps_to_read_failed(tmp_path):
    def broken_reader(_path):
        raise DocumentReadError("private_internal_code", "private detail")

    service, _reads, audit = _service(tmp_path, reader=broken_reader)
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)

    result = service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )

    assert result.status == "failed"
    assert result.error_code == "read_failed"
    assert "private_internal_code" not in repr(audit.list_recent())


def test_malformed_reader_error_code_maps_to_read_failed(tmp_path):
    def broken_reader(_path):
        raise DocumentReadError(["private"], "private detail")

    service, _reads, _audit = _service(tmp_path, reader=broken_reader)
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)

    result = service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )

    assert result.status == "failed"
    assert result.error_code == "read_failed"


def test_cancel_is_owner_scoped_and_reconnectable(tmp_path):
    service, _reads, _audit = _service(tmp_path)
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)
    other = ActorContext("owner-b", Actor.OWNER, "session-1")
    other_context = TaskRecordContext(
        speaker_label="owner-b", session_id="session-1", actor="owner"
    )
    guest = ActorContext("guest-a", Actor.GUEST, "session-1")
    guest_context = TaskRecordContext(
        speaker_label="guest-a", session_id="session-1", actor="guest", is_guest=True
    )

    assert service.cancel(
        prepared.task_id, actor=guest, context=guest_context
    ).error_code == "actor_not_authorized"
    assert service.cancel(
        prepared.task_id, actor=other, context=other_context
    ).error_code == "task_not_found"
    cancelled = service.cancel(
        prepared.task_id, actor=OWNER, context=TASK_CONTEXT
    )

    assert cancelled.status == "cancelled"
    assert cancelled.error_code is None
    assert service.reconnect(
        prepared.task_id, actor=OWNER, context=TASK_CONTEXT
    ).status == "cancelled"
    assert service.cancel(
        prepared.task_id, actor=OWNER, context=TASK_CONTEXT
    ).status == "cancelled"


def test_grant_ids_never_appear_in_api_or_audit_records(tmp_path):
    service, _reads, audit = _service(tmp_path)
    prepared = service.prepare("secret.txt", actor=OWNER, context=TASK_CONTEXT)
    completed = service.confirm_and_explain(
        prepared.task_id, ("first",), actor=OWNER, context=TASK_CONTEXT
    )

    with sqlite3.connect(service.policy.grants.db_path) as conn:
        grant_ids = [row[0] for row in conn.execute("SELECT grant_id FROM approval_grants")]
    visible = repr((prepared, completed, audit.list_recent()))
    assert grant_ids
    assert all(grant_id not in visible for grant_id in grant_ids)
