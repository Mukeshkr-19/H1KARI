"""Synthetic fixtures for truthful acknowledgments and retrieval policy."""

from __future__ import annotations

from core.brain_v2.durable_memory_actions import DurableActionOutcome
from core.brain_v2.durable_memory_outcome import (
    RetrievalCandidate,
    content_safe_diagnostic,
    decide_retrieval,
    format_durable_acknowledgment,
)


def test_accepted_requires_readback_for_saved_ack():
    ok = DurableActionOutcome(
        status="accepted",
        reason="ok",
        memory_id="mem-1",
        readback_ok=True,
        recallable=True,
    )
    ack = format_durable_acknowledgment(ok)
    assert ack.message == "Saved to long-term Brain."
    assert ack.recallable is True

    bad = DurableActionOutcome(
        status="accepted",
        reason="ok",
        memory_id="mem-1",
        readback_ok=False,
        recallable=False,
    )
    ack_bad = format_durable_acknowledgment(bad)
    assert ack_bad.message == "Could not verify whether the durable save completed."
    assert ack_bad.status == "unavailable"
    assert "Not saved" not in ack_bad.message


def test_pending_conflict_rejected_corrected_forgotten_acks():
    pending = format_durable_acknowledgment(
        DurableActionOutcome(status="pending_review", reason="ok")
    )
    assert "review" in pending.message.lower()
    assert pending.recallable is False

    conflict = format_durable_acknowledgment(
        DurableActionOutcome(
            status="pending_conflict", reason="conflict", memory_id="mem-conflict-1"
        )
    )
    assert "not replaced" in conflict.message.lower()

    rejected = format_durable_acknowledgment(
        DurableActionOutcome(status="rejected", reason="sensitivity_blocked")
    )
    assert rejected.message == "Not saved."
    assert rejected.reason_code == "sensitivity_blocked"

    unavailable = format_durable_acknowledgment(
        DurableActionOutcome(status="unavailable", reason="adapter_unavailable")
    )
    assert unavailable.message == "Not saved."

    corrected = format_durable_acknowledgment(
        DurableActionOutcome(
            status="corrected",
            reason="ok",
            memory_id="mem-2",
            superseded_id="mem-1",
            readback_ok=True,
            recallable=True,
        )
    )
    assert "superseded" in corrected.message.lower()

    forgotten = format_durable_acknowledgment(
        DurableActionOutcome(
            status="forgotten",
            reason="ok",
            retired_id="mem-1",
            recallable=False,
        )
    )
    assert "retired" in forgotten.message.lower()


def test_success_acknowledgments_require_correlated_record_ids():
    missing_saved_id = format_durable_acknowledgment(
        DurableActionOutcome(
            status="accepted",
            reason="ok",
            readback_ok=True,
            recallable=True,
        )
    )
    assert missing_saved_id.status == "unavailable"
    assert missing_saved_id.reason_code == "verification_failed"
    assert "Not saved" not in missing_saved_id.message

    missing_corrected_id = format_durable_acknowledgment(
        DurableActionOutcome(
            status="corrected",
            reason="ok",
            superseded_id="mem-1",
            readback_ok=True,
            recallable=True,
        )
    )
    assert missing_corrected_id.status == "unavailable"
    assert "Not saved" not in missing_corrected_id.message

    missing_retired_id = format_durable_acknowledgment(
        DurableActionOutcome(
            status="forgotten",
            reason="ok",
            recallable=False,
        )
    )
    assert missing_retired_id.status == "unavailable"
    assert "Not saved" not in missing_retired_id.message

    contradictory_forget = format_durable_acknowledgment(
        DurableActionOutcome(
            status="forgotten",
            reason="ok",
            retired_id="mem-1",
            recallable=True,
        )
    )
    assert contradictory_forget.status == "unavailable"
    assert "Not saved" not in contradictory_forget.message


def test_rejects_non_typed_outcome():
    try:
        format_durable_acknowledgment("I saved that")  # type: ignore[arg-type]
        assert False, "expected TypeError"
    except TypeError:
        pass


def test_exact_active_outranks_semantic_and_pending():
    decision = decide_retrieval(
        [
            RetrievalCandidate(
                memory_id="sem-1",
                lane="semantic_active",
                lifecycle="active",
                match_score=0.99,
            ),
            RetrievalCandidate(
                memory_id="ex-1",
                lane="exact_active",
                lifecycle="active",
                match_score=0.5,
            ),
            RetrievalCandidate(
                memory_id="pend-1",
                lane="status_only",
                lifecycle="pending",
                match_score=1.0,
                status_query_match=True,
            ),
        ]
    )
    assert decision.lane == "exact_active"
    assert decision.memory_id == "ex-1"


def test_semantic_active_second():
    decision = decide_retrieval(
        [
            RetrievalCandidate(
                memory_id="sem-1",
                lane="semantic_active",
                lifecycle="active",
                match_score=0.8,
            ),
            RetrievalCandidate(
                memory_id="pend-1",
                lane="status_only",
                lifecycle="pending",
                status_query_match=True,
            ),
        ]
    )
    assert decision.lane == "semantic_active"
    assert decision.memory_id == "sem-1"


def test_no_blanket_pending_review_fallback():
    decision = decide_retrieval(
        [
            RetrievalCandidate(
                memory_id="pend-1",
                lane="status_only",
                lifecycle="pending",
                status_query_match=False,
            )
        ],
        status_query=False,
    )
    assert decision.lane == "none"
    assert decision.reason_code == "no_active_match"


def test_status_explanation_only_when_specifically_matched():
    decision = decide_retrieval(
        [
            RetrievalCandidate(
                memory_id="ret-1",
                lane="status_only",
                lifecycle="retired",
                status_query_match=True,
            )
        ],
        status_query=True,
    )
    assert decision.lane == "status_explanation"
    assert decision.explain_status == "retired"

    ambiguous = decide_retrieval(
        [
            RetrievalCandidate(
                memory_id="a",
                lane="status_only",
                lifecycle="pending",
                status_query_match=True,
            ),
            RetrievalCandidate(
                memory_id="b",
                lane="status_only",
                lifecycle="rejected",
                status_query_match=True,
            ),
        ],
        status_query=True,
    )
    assert ambiguous.lane == "none"


def test_diagnostics_never_expose_private_values():
    decision = decide_retrieval(
        [
            RetrievalCandidate(
                memory_id="mem-SECRET-VALUE",
                lane="exact_active",
                lifecycle="active",
                match_score=1.0,
            )
        ]
    )
    diag = content_safe_diagnostic(decision)
    assert "SECRET" not in diag
    assert "mem-SECRET-VALUE" not in diag
    assert "lane=exact_active" in diag


def test_verification_failed_ack_not_certain_not_saved():
    ack = format_durable_acknowledgment(
        DurableActionOutcome(
            status="unavailable",
            reason="save_outcome_unknown",
            memory_id="mem-1",
            readback_ok=False,
            recallable=False,
        )
    )
    assert ack.message == "Could not verify whether the durable save completed."
    assert ack.status == "unavailable"
    assert ack.reason_code == "save_outcome_unknown"
    assert "Not saved" not in ack.message


def test_invalid_scores_ignored_and_ties_are_deterministic():
    decision = decide_retrieval(
        [
            RetrievalCandidate(
                memory_id="bad-1",
                lane="semantic_active",
                lifecycle="active",
                match_score=float("nan"),
            ),
            RetrievalCandidate(
                memory_id="a-1",
                lane="semantic_active",
                lifecycle="active",
                match_score=0.5,
            ),
            RetrievalCandidate(
                memory_id="b-1",
                lane="semantic_active",
                lifecycle="active",
                match_score=0.5,
            ),
        ]
    )
    assert decision.lane == "semantic_active"
    assert decision.memory_id == "b-1"


def test_uncertain_save_correct_forget_acknowledgments():
    save_u = format_durable_acknowledgment(
        DurableActionOutcome(status="unavailable", reason="save_outcome_unknown")
    )
    assert save_u.message == "Could not verify whether the durable save completed."
    assert "Not saved" not in save_u.message

    corr_u = format_durable_acknowledgment(
        DurableActionOutcome(status="unavailable", reason="correction_outcome_unknown")
    )
    assert corr_u.message == "Could not verify whether the correction completed."
    assert "Not saved" not in corr_u.message

    forget_u = format_durable_acknowledgment(
        DurableActionOutcome(status="unavailable", reason="forget_outcome_unknown")
    )
    assert forget_u.message == "Could not verify whether the record was retired."
    assert "Not saved" not in forget_u.message

    for ack in (save_u, corr_u, forget_u):
        assert "SECRET" not in repr(ack)
        assert "mem-" not in repr(ack)


def test_pending_without_usable_id_is_uncertain():
    empty_conflict = format_durable_acknowledgment(
        DurableActionOutcome(status="pending_conflict", reason="conflict", memory_id="")
    )
    assert empty_conflict.status == "unavailable"
    assert empty_conflict.message == "Could not verify whether the durable save completed."
    assert "Not saved" not in empty_conflict.message
