"""Synthetic fixtures for durable action plans and verified outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from core.brain_v2.durable_memory_actions import (
    AtomicWriteResult,
    DurableMemoryAdapters,
    ReadbackResult,
    RetireResult,
    SupersedeResult,
    execute_durable_plan,
    plan_durable_action,
    validate_fact_body,
)
from core.brain_v2.durable_memory_intent import parse_durable_memory_intent


ACTOR = "owner.primary"
SESSION = "sess.alpha-1"


@dataclass
class FakeStore:
    rows: Dict[str, dict] = field(default_factory=dict)
    writes: int = 0
    force_conflict: bool = False
    force_pending: bool = False
    fail_readback: bool = False

    def atomic_write_active(
        self, *, body: str, idempotency_key: str, actor_id: str, session_id: str
    ) -> AtomicWriteResult:
        self.writes += 1
        for row in self.rows.values():
            if row.get("idempotency_key") == idempotency_key:
                return AtomicWriteResult(
                    memory_id=row["id"], created=False, conflict=False
                )
        mid = f"mem-{len(self.rows) + 1}"
        if self.force_conflict:
            return AtomicWriteResult(memory_id=mid, created=False, conflict=True)
        if self.force_pending:
            self.rows[mid] = {
                "id": mid,
                "body": body,
                "active": False,
                "pending": True,
                "idempotency_key": idempotency_key,
            }
            return AtomicWriteResult(
                memory_id=mid, created=True, pending_review=True
            )
        self.rows[mid] = {
            "id": mid,
            "body": body,
            "active": True,
            "pending": False,
            "idempotency_key": idempotency_key,
            "audit": ["created"],
        }
        return AtomicWriteResult(memory_id=mid, created=True)

    def create_and_accept_candidate(
        self, *, body: str, idempotency_key: str, actor_id: str, session_id: str
    ) -> AtomicWriteResult:
        return self.atomic_write_active(
            body=body,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            session_id=session_id,
        )

    def verify_readback(self, *, memory_id: str, expected_body: str) -> ReadbackResult:
        if self.fail_readback:
            return ReadbackResult(found=False, active=False, statement_matches=False)
        row = self.rows.get(memory_id)
        if not row:
            return ReadbackResult(found=False, active=False, statement_matches=False)
        return ReadbackResult(
            found=True,
            active=bool(row.get("active")),
            statement_matches=row.get("body") == expected_body,
        )

    def find_exact_active(self, *, body: str) -> Optional[str]:
        for mid, row in self.rows.items():
            if row.get("active") and row.get("body") == body:
                return mid
        return None

    def supersede(
        self, *, old_memory_id: str, new_body: str, idempotency_key: str
    ) -> SupersedeResult:
        old = self.rows[old_memory_id]
        old["active"] = False
        old["lifecycle"] = "superseded"
        old.setdefault("audit", []).append("superseded")
        new_id = f"mem-{len(self.rows) + 1}"
        self.rows[new_id] = {
            "id": new_id,
            "body": new_body,
            "active": True,
            "idempotency_key": idempotency_key,
            "supersedes": old_memory_id,
            "audit": list(old.get("audit") or []) + ["created_from_supersede"],
        }
        return SupersedeResult(
            old_memory_id=old_memory_id,
            new_memory_id=new_id,
            preserved_audit=True,
        )

    def retire(self, *, memory_id: str, idempotency_key: str) -> RetireResult:
        row = self.rows[memory_id]
        row["active"] = False
        row["lifecycle"] = "retired"
        row.setdefault("audit", []).append(f"retired:{idempotency_key}")
        # preserved: row still present (non-destructive)
        return RetireResult(memory_id=memory_id, retired=True, preserved=True)


def _intent(text: str, **kwargs):
    return parse_durable_memory_intent(
        text, actor_id=ACTOR, session_id=SESSION, **kwargs
    )


def test_save_plan_requires_readback_and_idempotency():
    intent = _intent("save this into my Brain: I live in North City")
    plan = plan_durable_action(intent)
    assert plan.kind == "save_atomic"
    assert plan.requires_readback is True
    assert plan.hard_delete is False
    assert plan.idempotency_key.startswith("dmi-")


def test_save_success_needs_readback():
    store = FakeStore()
    adapters = DurableMemoryAdapters(
        atomic_write=store,
        readback=store,
        exact_active=store,
    )
    plan = plan_durable_action(
        _intent("save this to my Brain: I live in North City")
    )
    outcome = execute_durable_plan(
        plan, adapters=adapters, actor_id=ACTOR, session_id=SESSION
    )
    assert outcome.status == "accepted"
    assert outcome.readback_ok is True
    assert outcome.recallable is True

    store.fail_readback = True
    store2 = FakeStore(fail_readback=True)
    adapters2 = DurableMemoryAdapters(atomic_write=store2, readback=store2)
    outcome2 = execute_durable_plan(
        plan, adapters=adapters2, actor_id=ACTOR, session_id=SESSION
    )
    assert outcome2.status == "unavailable"
    assert outcome2.reason == "save_outcome_unknown"


def test_pending_review_and_conflict_outcomes():
    pending = FakeStore(force_pending=True)
    adapters = DurableMemoryAdapters(atomic_write=pending, readback=pending)
    plan = plan_durable_action(
        _intent("remember this: I live in North City")
    )
    out = execute_durable_plan(
        plan, adapters=adapters, actor_id=ACTOR, session_id=SESSION
    )
    assert out.status == "pending_review"
    assert out.recallable is False

    conflict = FakeStore(force_conflict=True)
    adapters_c = DurableMemoryAdapters(atomic_write=conflict, readback=conflict)
    out_c = execute_durable_plan(
        plan, adapters=adapters_c, actor_id=ACTOR, session_id=SESSION
    )
    assert out_c.status == "pending_conflict"


def test_inferred_suggestion_never_silently_persists():
    intent = _intent("I like green tea", inferred_candidate=True)
    # preference_without_consent wins over inferred when pattern matches
    if intent.reason_code == "preference_without_consent":
        plan = plan_durable_action(intent)
        assert plan.kind == "noop"
    intent2 = _intent("I work as a designer", inferred_candidate=True)
    plan2 = plan_durable_action(intent2)
    assert plan2.kind == "suggest_review"
    out = execute_durable_plan(
        plan2,
        adapters=DurableMemoryAdapters(),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert out.status == "pending_review"
    assert out.recallable is False


def test_validation_rejects_task_sensitive_third_party():
    assert validate_fact_body("remind me to call mom") == "task_not_fact"
    assert validate_fact_body("my password is hunter2") == "sensitivity_blocked"
    assert (
        validate_fact_body("my friend lives in Paris") == "third_party_ambiguity"
    )


def test_correction_supersedes_non_destructively():
    store = FakeStore()
    adapters = DurableMemoryAdapters(
        atomic_write=store,
        readback=store,
        exact_active=store,
        supersede=store,
    )
    save_plan = plan_durable_action(
        _intent("remember this: I live in North City")
    )
    saved = execute_durable_plan(
        save_plan, adapters=adapters, actor_id=ACTOR, session_id=SESSION
    )
    assert saved.status == "accepted"
    old_id = saved.memory_id
    correct_plan = plan_durable_action(
        _intent("correct this in my Brain: I live in South City")
    )
    assert correct_plan.kind == "correct_supersede"
    assert correct_plan.hard_delete is False
    corrected = execute_durable_plan(
        correct_plan,
        adapters=adapters,
        actor_id=ACTOR,
        session_id=SESSION,
        target_memory_id=old_id,
    )
    assert corrected.status == "corrected"
    assert corrected.superseded_id == old_id
    assert corrected.readback_ok is True
    assert store.rows[old_id]["lifecycle"] == "superseded"
    assert "superseded" in store.rows[old_id]["audit"]
    assert store.rows[old_id]  # still present


def test_forget_retires_non_destructively():
    store = FakeStore()
    adapters = DurableMemoryAdapters(
        atomic_write=store,
        readback=store,
        exact_active=store,
        retire=store,
    )
    saved = execute_durable_plan(
        plan_durable_action(_intent("remember this: I live in North City")),
        adapters=adapters,
        actor_id=ACTOR,
        session_id=SESSION,
    )
    forgotten = execute_durable_plan(
        plan_durable_action(
            _intent("forget this from my Brain: I live in North City")
        ),
        adapters=adapters,
        actor_id=ACTOR,
        session_id=SESSION,
        target_memory_id=saved.memory_id,
    )
    assert forgotten.status == "forgotten"
    assert store.rows[saved.memory_id]["lifecycle"] == "retired"
    assert store.rows[saved.memory_id]  # not hard-deleted


def test_missing_adapters_unavailable():
    plan = plan_durable_action(
        _intent("save this as a memory: I live in North City")
    )
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert out.status == "unavailable"
    assert out.reason == "adapter_unavailable"


def test_unresolved_anaphora_does_not_write():
    plan = plan_durable_action(_intent("remember this"))
    assert plan.kind == "request_exact_fact"
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert out.status == "not_saved"
    assert out.reason == "unresolved_target"

def test_correction_forget_idempotency_binds_target_id():
    from core.brain_v2.durable_memory_actions import make_idempotency_key

    body = "I live in North City"
    key_a = make_idempotency_key(
        actor_id=ACTOR,
        session_id=SESSION,
        action="correct",
        body=body,
        target_memory_id="mem-1",
    )
    key_b = make_idempotency_key(
        actor_id=ACTOR,
        session_id=SESSION,
        action="correct",
        body=body,
        target_memory_id="mem-2",
    )
    assert key_a != key_b
    forget_a = make_idempotency_key(
        actor_id=ACTOR,
        session_id=SESSION,
        action="forget",
        body=body,
        target_memory_id="mem-1",
    )
    forget_b = make_idempotency_key(
        actor_id=ACTOR,
        session_id=SESSION,
        action="forget",
        body=body,
        target_memory_id="mem-2",
    )
    assert forget_a != forget_b


def test_adapter_exception_returns_unavailable_without_raw_message():
    class Boom:
        def atomic_write_active(self, **kwargs):
            raise RuntimeError("SECRET_ADAPTER_TRACE")

        def verify_readback(self, **kwargs):
            raise RuntimeError("SECRET_ADAPTER_TRACE")

    boom = Boom()
    plan = plan_durable_action(
        _intent("save this as a memory: I live in North City")
    )
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(atomic_write=boom, readback=boom),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert out.status == "unavailable"
    assert out.reason == "save_outcome_unknown"
    assert "SECRET" not in repr(out)


def test_invalid_actor_rejects_before_adapter():
    class Counting:
        def __init__(self):
            self.calls = 0

        def atomic_write_active(self, **kwargs):
            self.calls += 1
            return AtomicWriteResult(memory_id="mem-x", created=True)

        def verify_readback(self, **kwargs):
            self.calls += 1
            return ReadbackResult(True, True, True)

    store = Counting()
    plan = plan_durable_action(
        _intent("save this as a memory: I live in North City")
    )
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(atomic_write=store, readback=store),
        actor_id="Bad Actor!",
        session_id=SESSION,
    )
    assert out.status == "rejected"
    assert store.calls == 0


def test_correction_requires_correlated_old_and_distinct_new_id():
    store = FakeStore()
    adapters = DurableMemoryAdapters(
        atomic_write=store,
        readback=store,
        exact_active=store,
        supersede=store,
    )
    saved = execute_durable_plan(
        plan_durable_action(_intent("remember this: I live in North City")),
        adapters=adapters,
        actor_id=ACTOR,
        session_id=SESSION,
    )
    class MismatchSupersede:
        def supersede(self, *, old_memory_id, new_body, idempotency_key):
            return SupersedeResult(
                old_memory_id="mem-OTHER",
                new_memory_id="mem-new",
                preserved_audit=True,
            )

        def verify_readback(self, **kwargs):
            return ReadbackResult(True, True, True)

    bad = MismatchSupersede()
    out = execute_durable_plan(
        plan_durable_action(
            _intent("correct this in my Brain: I live in South City")
        ),
        adapters=DurableMemoryAdapters(supersede=bad, readback=bad),
        actor_id=ACTOR,
        session_id=SESSION,
        target_memory_id=saved.memory_id,
    )
    assert out.status == "unavailable"
    assert out.reason == "correction_outcome_unknown"

def test_save_mutates_then_raises_is_uncertain():
    from core.brain_v2.durable_memory_outcome import format_durable_acknowledgment

    class MutateThenRaise:
        def __init__(self):
            self.mutated = False

        def atomic_write_active(self, **kwargs):
            self.mutated = True
            raise RuntimeError("SECRET_SAVE_TRACE_XYZ")

        def verify_readback(self, **kwargs):
            raise AssertionError("readback must not run")

    store = MutateThenRaise()
    plan = plan_durable_action(
        _intent("save this as a memory: I live in North City")
    )
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(atomic_write=store, readback=store),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert store.mutated is True
    assert out.status == "unavailable"
    assert out.reason == "save_outcome_unknown"
    ack = format_durable_acknowledgment(out)
    assert "Not saved" not in ack.message
    assert "whether the durable save completed" in ack.message
    assert "SECRET" not in repr(out) and "SECRET" not in repr(ack)
    assert "SECRET" not in ack.message


def test_supersede_mutates_then_raises_is_uncertain():
    from core.brain_v2.durable_memory_outcome import format_durable_acknowledgment

    class MutateThenRaise:
        def __init__(self):
            self.mutated = False

        def supersede(self, **kwargs):
            self.mutated = True
            raise RuntimeError("SECRET_CORRECT_TRACE")

        def verify_readback(self, **kwargs):
            raise AssertionError("no")

    bad = MutateThenRaise()
    plan = plan_durable_action(
        _intent("correct this in my Brain: I live in South City")
    )
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(supersede=bad, readback=bad),
        actor_id=ACTOR,
        session_id=SESSION,
        target_memory_id="mem-1",
    )
    assert bad.mutated is True
    assert out.reason == "correction_outcome_unknown"
    ack = format_durable_acknowledgment(out)
    assert "Not saved" not in ack.message
    assert "correction completed" in ack.message
    assert "SECRET" not in repr(out) and "SECRET" not in ack.message


def test_retire_mutates_then_raises_is_uncertain():
    from core.brain_v2.durable_memory_outcome import format_durable_acknowledgment

    class MutateThenRaise:
        def __init__(self):
            self.mutated = False

        def retire(self, **kwargs):
            self.mutated = True
            raise RuntimeError("SECRET_FORGET_TRACE")

    bad = MutateThenRaise()
    plan = plan_durable_action(
        _intent("forget this from my Brain: I live in North City")
    )
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(retire=bad),
        actor_id=ACTOR,
        session_id=SESSION,
        target_memory_id="mem-1",
    )
    assert bad.mutated is True
    assert out.reason == "forget_outcome_unknown"
    ack = format_durable_acknowledgment(out)
    assert "Not saved" not in ack.message
    assert "retired" in ack.message.lower()
    assert "SECRET" not in repr(out) and "SECRET" not in ack.message


def test_malformed_post_write_result_is_uncertain():
    class BadWrite:
        def atomic_write_active(self, **kwargs):
            return {"memory_id": "mem-1"}  # wrong type

        def verify_readback(self, **kwargs):
            return ReadbackResult(True, True, True)

    plan = plan_durable_action(
        _intent("save this as a memory: I live in North City")
    )
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(atomic_write=BadWrite(), readback=BadWrite()),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert out.status == "unavailable"
    assert out.reason == "save_outcome_unknown"


def test_readback_exception_is_uncertain_not_not_saved():
    from core.brain_v2.durable_memory_outcome import format_durable_acknowledgment

    class WriteOkReadRaise:
        def atomic_write_active(self, **kwargs):
            return AtomicWriteResult(memory_id="mem-1", created=True)

        def verify_readback(self, **kwargs):
            raise RuntimeError("SECRET_READBACK")

    plan = plan_durable_action(
        _intent("save this as a memory: I live in North City")
    )
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(
            atomic_write=WriteOkReadRaise(), readback=WriteOkReadRaise()
        ),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert out.reason == "save_outcome_unknown"
    ack = format_durable_acknowledgment(out)
    assert "Not saved" not in ack.message
    assert "whether the durable save completed" in ack.message
    assert "SECRET" not in ack.message


def test_missing_adapters_known_no_write():
    from core.brain_v2.durable_memory_outcome import format_durable_acknowledgment

    plan = plan_durable_action(
        _intent("save this as a memory: I live in North City")
    )
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert out.status == "unavailable"
    assert out.reason == "adapter_unavailable"
    ack = format_durable_acknowledgment(out)
    assert ack.message == "Not saved."


def test_malformed_anaphora_never_invokes_adapter():
    class Counting:
        def __init__(self):
            self.calls = 0

        def atomic_write_active(self, **kwargs):
            self.calls += 1
            return AtomicWriteResult(memory_id="mem-x", created=True)

        def verify_readback(self, **kwargs):
            self.calls += 1
            return ReadbackResult(True, True, True)

    store = Counting()
    intent = parse_durable_memory_intent(
        "remember this",
        actor_id=ACTOR,
        session_id=SESSION,
        recent_context=[{"text": "I live in North City"}],  # type: ignore[arg-type]
        now_ms=2_000,
    )
    assert intent.target == "unresolved"
    assert intent.request_exact_fact is True
    plan = plan_durable_action(intent)
    assert plan.kind == "request_exact_fact"
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(atomic_write=store, readback=store),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert store.calls == 0
    assert out.reason == "unresolved_target"

def test_correction_readback_exception_is_correction_uncertain():
    from core.brain_v2.durable_memory_outcome import format_durable_acknowledgment

    class SupersedeOkReadRaise:
        def supersede(self, *, old_memory_id, new_body, idempotency_key):
            return SupersedeResult(
                old_memory_id=old_memory_id,
                new_memory_id="mem-new-1",
                preserved_audit=True,
            )

        def verify_readback(self, **kwargs):
            raise RuntimeError("SECRET_CORR_READBACK")

    plan = plan_durable_action(
        _intent("correct this in my Brain: I live in South City")
    )
    out = execute_durable_plan(
        plan,
        adapters=DurableMemoryAdapters(
            supersede=SupersedeOkReadRaise(), readback=SupersedeOkReadRaise()
        ),
        actor_id=ACTOR,
        session_id=SESSION,
        target_memory_id="mem-1",
    )
    assert out.status == "unavailable"
    assert out.reason == "correction_outcome_unknown"
    ack = format_durable_acknowledgment(out)
    assert ack.message == "Could not verify whether the correction completed."
    assert "save completed" not in ack.message
    assert "Not saved" not in ack.message
    assert "SECRET" not in repr(out) and "SECRET" not in ack.message


def test_correction_readback_false_is_correction_uncertain():
    from core.brain_v2.durable_memory_outcome import format_durable_acknowledgment

    class SupersedeOkReadFalse:
        def supersede(self, *, old_memory_id, new_body, idempotency_key):
            return SupersedeResult(
                old_memory_id=old_memory_id,
                new_memory_id="mem-new-2",
                preserved_audit=True,
            )

        def verify_readback(self, **kwargs):
            return ReadbackResult(found=True, active=False, statement_matches=False)

    out = execute_durable_plan(
        plan_durable_action(
            _intent("correct this in my Brain: I live in South City")
        ),
        adapters=DurableMemoryAdapters(
            supersede=SupersedeOkReadFalse(), readback=SupersedeOkReadFalse()
        ),
        actor_id=ACTOR,
        session_id=SESSION,
        target_memory_id="mem-1",
    )
    assert out.reason == "correction_outcome_unknown"
    ack = format_durable_acknowledgment(out)
    assert ack.message == "Could not verify whether the correction completed."


def test_save_readback_failure_is_save_uncertain():
    from core.brain_v2.durable_memory_outcome import format_durable_acknowledgment

    class WriteOkReadFalse:
        def atomic_write_active(self, **kwargs):
            return AtomicWriteResult(memory_id="mem-1", created=True)

        def verify_readback(self, **kwargs):
            return ReadbackResult(found=False, active=False, statement_matches=False)

    out = execute_durable_plan(
        plan_durable_action(_intent("save this as a memory: I live in North City")),
        adapters=DurableMemoryAdapters(
            atomic_write=WriteOkReadFalse(), readback=WriteOkReadFalse()
        ),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert out.reason == "save_outcome_unknown"
    ack = format_durable_acknowledgment(out)
    assert ack.message == "Could not verify whether the durable save completed."


def test_pending_flags_without_usable_id_are_uncertain():
    class EmptyPending:
        def atomic_write_active(self, **kwargs):
            return AtomicWriteResult(
                memory_id="", created=True, pending_review=True
            )

        def verify_readback(self, **kwargs):
            raise AssertionError("no")

    class EmptyConflict:
        def atomic_write_active(self, **kwargs):
            return AtomicWriteResult(memory_id="", created=False, conflict=True)

        def verify_readback(self, **kwargs):
            raise AssertionError("no")

    class BothFlags:
        def atomic_write_active(self, **kwargs):
            return AtomicWriteResult(
                memory_id="mem-1", created=True, conflict=True, pending_review=True
            )

        def verify_readback(self, **kwargs):
            raise AssertionError("no")

    for store in (EmptyPending(), EmptyConflict(), BothFlags()):
        out = execute_durable_plan(
            plan_durable_action(
                _intent("save this as a memory: I live in North City")
            ),
            adapters=DurableMemoryAdapters(atomic_write=store, readback=store),
            actor_id=ACTOR,
            session_id=SESSION,
        )
        assert out.status == "unavailable"
        assert out.reason == "save_outcome_unknown"


def test_non_boolean_result_flags_are_uncertain():
    class BadBoolWrite:
        def atomic_write_active(self, **kwargs):
            return AtomicWriteResult(
                memory_id="mem-1",
                created="yes",  # type: ignore[arg-type]
                conflict=False,
                pending_review=False,
            )

        def verify_readback(self, **kwargs):
            return ReadbackResult(True, True, True)

    out = execute_durable_plan(
        plan_durable_action(_intent("save this as a memory: I live in North City")),
        adapters=DurableMemoryAdapters(
            atomic_write=BadBoolWrite(), readback=BadBoolWrite()
        ),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert out.reason == "save_outcome_unknown"


def test_malformed_readback_booleans_cannot_accept_or_correct():
    class WriteOkBadRead:
        def atomic_write_active(self, **kwargs):
            return AtomicWriteResult(memory_id="mem-1", created=True)

        def verify_readback(self, **kwargs):
            return ReadbackResult(
                found="yes",  # type: ignore[arg-type]
                active=True,
                statement_matches=True,
            )

        def supersede(self, *, old_memory_id, new_body, idempotency_key):
            return SupersedeResult(
                old_memory_id=old_memory_id,
                new_memory_id="mem-2",
                preserved_audit=True,
            )

    store = WriteOkBadRead()
    save = execute_durable_plan(
        plan_durable_action(_intent("save this as a memory: I live in North City")),
        adapters=DurableMemoryAdapters(atomic_write=store, readback=store),
        actor_id=ACTOR,
        session_id=SESSION,
    )
    assert save.status == "unavailable"
    assert save.reason == "save_outcome_unknown"

    corr = execute_durable_plan(
        plan_durable_action(
            _intent("correct this in my Brain: I live in South City")
        ),
        adapters=DurableMemoryAdapters(supersede=store, readback=store),
        actor_id=ACTOR,
        session_id=SESSION,
        target_memory_id="mem-1",
    )
    assert corr.status == "unavailable"
    assert corr.reason == "correction_outcome_unknown"


def test_malformed_supersede_audit_and_retire_flags():
    class BadAudit:
        def supersede(self, *, old_memory_id, new_body, idempotency_key):
            return SupersedeResult(
                old_memory_id=old_memory_id,
                new_memory_id="mem-2",
                preserved_audit="yes",  # type: ignore[arg-type]
            )

        def verify_readback(self, **kwargs):
            return ReadbackResult(True, True, True)

    class BadRetire:
        def retire(self, *, memory_id, idempotency_key):
            return RetireResult(
                memory_id=memory_id,
                retired="yes",  # type: ignore[arg-type]
                preserved=True,
            )

    corr = execute_durable_plan(
        plan_durable_action(
            _intent("correct this in my Brain: I live in South City")
        ),
        adapters=DurableMemoryAdapters(supersede=BadAudit(), readback=BadAudit()),
        actor_id=ACTOR,
        session_id=SESSION,
        target_memory_id="mem-1",
    )
    assert corr.reason == "correction_outcome_unknown"

    forgotten = execute_durable_plan(
        plan_durable_action(
            _intent("forget this from my Brain: I live in North City")
        ),
        adapters=DurableMemoryAdapters(retire=BadRetire()),
        actor_id=ACTOR,
        session_id=SESSION,
        target_memory_id="mem-1",
    )
    assert forgotten.reason == "forget_outcome_unknown"
