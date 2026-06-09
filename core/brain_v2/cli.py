"""Brain v2 review CLI — list, show, accept, reject, guided review (manual only)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional

from core.brain_v2.candidate_quality import filter_pending_for_review
from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.schemas import MemoryCandidate, MemoryCandidateStatus
from core.brain_v2.status import collect_brain_v2_status, format_brain_v2_status_lines, is_brain_v2_enabled

PROMOTE_CONFIRM_TOKEN = "PROMOTE"
REVIEW_ACTIONS_HELP = (
    "Actions: [a]ccept (no promote)  [p]romote (type PROMOTE)  "
    "[r]eject  [s]kip  [q]uit"
)


class ReviewAction(str, Enum):
    ACCEPT = "accept"
    ACCEPT_PROMOTE = "accept_promote"
    REJECT = "reject"
    SKIP = "skip"
    QUIT = "quit"


class ReviewStepResult(str, Enum):
    """How the review loop should advance after one action."""

    NEXT = "next"
    STAY = "stay"
    STOP = "stop"


@dataclass(frozen=True)
class ReviewIO:
    """Injectable input/output for guided review (tests avoid real stdin)."""

    write: Callable[[str], None]
    readline: Callable[[str], str]


def _coordinator_readonly(*, write: bool = False) -> BrainV2Coordinator:
    """Brain v2 access without neural memory init (default for review CLI)."""
    if not is_brain_v2_enabled():
        raise SystemExit("Brain v2 is disabled (HIKARI_DISABLE_BRAIN_V2=1).")
    from core.brain_v2.db_paths import open_episode_store

    store = open_episode_store(write=write)
    return BrainV2Coordinator(store=store, neural_bridge=None, allow_neural_procedural=False)


def _coordinator_promote(store=None) -> BrainV2Coordinator:
    """Brain v2 coordinator with neural bridge — only for confirmed promotion."""
    if not is_brain_v2_enabled():
        raise SystemExit("Brain v2 is disabled (HIKARI_DISABLE_BRAIN_V2=1).")
    from core.brain_v2.db_paths import open_episode_store

    episode_store = store if store is not None else open_episode_store(write=True)
    try:
        from core import neural_memory_bridge

        bridge = neural_memory_bridge if neural_memory_bridge.init_neural_memory() else None
    except Exception:
        bridge = None
    return BrainV2Coordinator(episode_store, neural_bridge=bridge)


def cmd_status() -> int:
    for line in format_brain_v2_status_lines():
        print(line)
    return 0


def cmd_conflicts() -> int:
    from core.brain_v2.conflicts import run_brain_v2_conflicts

    return run_brain_v2_conflicts()


def cmd_pending() -> int:
    coord = _coordinator_readonly(write=False)
    pending = filter_pending_for_review(
        coord.store.get_candidates(status=MemoryCandidateStatus.PENDING)
    )
    if not pending:
        print("No pending Brain v2 memory candidates.")
        return 0
    print(f"Pending candidates ({len(pending)}):")
    for cand in pending:
        meta = cand.metadata or {}
        rank = meta.get("rank_score", "?")
        quality = meta.get("quality_label", "keep")
        dup = ""
        if meta.get("duplicate_of"):
            dup = " dup"
        if meta.get("duplicate_of_existing_memory"):
            dup += " exists"
        print(
            f"  {cand.candidate_id[:8]}...  score={rank}  quality={quality}  "
            f"type={cand.candidate_type}{dup}  {cand.statement[:64]}"
        )
    return 0


def cmd_show(candidate_id: str) -> int:
    coord = _coordinator_readonly(write=False)
    cand = coord.store.get_candidate(candidate_id)
    if not cand:
        print(f"Candidate not found: {candidate_id}", file=sys.stderr)
        return 1
    meta = cand.metadata or {}
    print(f"candidate_id: {cand.candidate_id}")
    print(f"episode_id:   {cand.episode_id}")
    print(f"status:       {cand.review_status}")
    print(f"type:         {cand.candidate_type}")
    print(f"confidence:   {cand.confidence}")
    print(f"salience:     {cand.salience}")
    print(f"quality:      {meta.get('quality_label', 'n/a')}")
    reasons = meta.get("quality_reasons") or []
    if reasons:
        print(f"quality_why:  {', '.join(reasons)}")
    print(f"policy:       {meta.get('extraction_policy_version', 'n/a')}")
    print(f"rank_score:   {meta.get('rank_score', 'n/a')}")
    print(f"duplicate_of: {meta.get('duplicate_of', '—')}")
    if meta.get("duplicate_of_existing_memory"):
        print(f"matches_existing_memory: {meta.get('duplicate_of_existing_memory')}")
    print(f"dup_count:    {meta.get('duplicate_count', 1)}")
    print(f"created_at:   {cand.created_at}")
    print(f"statement:    {cand.statement}")
    print("review_commands:")
    print("  --brain-v2-accept-no-promote <id>  (Brain v2 only, safe default)")
    print(
        "  --brain-v2-accept <id> --confirm-promote PROMOTE  "
        "(also promotes to live neural DB)"
    )
    print("source_segment_ids:")
    for sid in cand.source_segment_ids or []:
        print(f"  - {sid}")
    segments = coord.store.get_raw_segments(cand.episode_id)
    if segments:
        print("source segments (verbatim):")
        id_set = set(cand.source_segment_ids or [])
        for seg in segments:
            if seg.segment_id in id_set:
                role = "user" if seg.is_user else seg.speaker_label
                print(f"  [{role}] {seg.text[:200]}")
    structured = coord.store.get_structured_episode(cand.episode_id)
    if structured:
        print(f"episode title: {structured.title}")
        print(f"episode summary: {structured.summary[:240]}")
    return 0


def cmd_accept(candidate_id: str, *, promote: bool = False) -> int:
    coord = _coordinator_promote() if promote else _coordinator_readonly(write=True)
    try:
        linked, promo_key = coord.accept_candidate(candidate_id, promote=promote)
    except KeyError:
        print(f"Candidate not found: {candidate_id}", file=sys.stderr)
        return 1
    meta = linked.metadata or {}
    if meta.get("merged_into_existing"):
        print(f"Merged into existing memory {linked.memory_id} (duplicate statement).")
    else:
        print(f"Accepted memory {linked.memory_id}")
    if promote:
        if promo_key:
            print(f"Promoted to neural memory: {promo_key}")
        else:
            print("Accepted, but neural promotion was not confirmed.")
    else:
        print("Accepted without neural promotion (Brain v2 source-linked memory only).")
    print(f"Source segments: {len(linked.source_segment_ids)}")
    return 0


def cmd_reject(candidate_id: str) -> int:
    coord = _coordinator_readonly(write=True)
    try:
        rejected = coord.reject_candidate(candidate_id)
    except KeyError:
        print(f"Candidate not found: {candidate_id}", file=sys.stderr)
        return 1
    print(f"Rejected candidate {rejected.candidate_id}")
    return 0


def cmd_consolidate() -> int:
    if not is_brain_v2_enabled():
        raise SystemExit("Brain v2 is disabled (HIKARI_DISABLE_BRAIN_V2=1).")
    coord = _coordinator_readonly(write=True)
    summary = coord.consolidate_pending_episodes()
    print(
        "Brain v2 consolidate: "
        f"episodes={summary['episodes']} "
        f"candidates={summary['candidates']} "
        f"skipped={summary['skipped']} "
        f"errors={summary['errors']}"
    )
    return 0 if summary["errors"] == 0 else 1


def cmd_retag_accepted() -> int:
    coord = _coordinator_readonly(write=True)
    counts = coord.retag_accepted_memories()
    print(
        "Brain v2 retag accepted: "
        f"updated={counts['updated']} unchanged={counts['unchanged']}"
    )
    return 0


def default_review_io() -> ReviewIO:
    return ReviewIO(write=lambda text: print(text, flush=True), readline=input)


def normalize_review_action(raw: str) -> Optional[ReviewAction]:
    key = (raw or "").strip().lower()
    if key in ("a", "accept"):
        return ReviewAction.ACCEPT
    if key in ("p", "promote"):
        return ReviewAction.ACCEPT_PROMOTE
    if key in ("r", "reject"):
        return ReviewAction.REJECT
    if key in ("s", "skip"):
        return ReviewAction.SKIP
    if key in ("q", "quit"):
        return ReviewAction.QUIT
    return None


def format_duplicate_markers(metadata: dict) -> str:
    markers: List[str] = []
    if metadata.get("duplicate_of"):
        markers.append("dup")
    if metadata.get("duplicate_of_existing_memory"):
        markers.append("exists")
    return ", ".join(markers) if markers else "—"


def format_source_segment_lines(coordinator: BrainV2Coordinator, candidate: MemoryCandidate) -> List[str]:
    lines: List[str] = []
    segments = coordinator.store.get_raw_segments(candidate.episode_id)
    id_set = set(candidate.source_segment_ids or [])
    for seg in segments:
        if seg.segment_id in id_set:
            role = "user" if seg.is_user else seg.speaker_label
            lines.append(f"  [{role}] {seg.text}")
    return lines


def format_candidate_review_display(
    coordinator: BrainV2Coordinator, candidate: MemoryCandidate
) -> str:
    meta = candidate.metadata or {}
    lines = [
        "",
        f"--- Candidate {candidate.candidate_id} ---",
        f"id:        {candidate.candidate_id}",
        f"type:      {candidate.candidate_type}",
        f"quality:   {meta.get('quality_label', 'keep')}",
        f"score:     {meta.get('rank_score', 'n/a')}",
        f"dup:       {format_duplicate_markers(meta)}",
        f"statement: {candidate.statement}",
    ]
    seg_lines = format_source_segment_lines(coordinator, candidate)
    if seg_lines:
        lines.append("source:")
        lines.extend(seg_lines)
    lines.append(REVIEW_ACTIONS_HELP)
    return "\n".join(lines)


def get_pending_review_candidates(coordinator: BrainV2Coordinator) -> List[MemoryCandidate]:
    return filter_pending_for_review(
        coordinator.store.get_candidates(status=MemoryCandidateStatus.PENDING)
    )


def confirm_promote(io: ReviewIO) -> bool:
    io.write(
        f"Promotion writes to live neural memory. Type {PROMOTE_CONFIRM_TOKEN} to confirm."
    )
    return io.readline("Confirm: ") == PROMOTE_CONFIRM_TOKEN


def apply_review_action(
    coordinator: BrainV2Coordinator,
    candidate_id: str,
    action: ReviewAction,
    io: ReviewIO,
) -> ReviewStepResult:
    if action == ReviewAction.QUIT:
        io.write("Review session ended.")
        return ReviewStepResult.STOP
    if action == ReviewAction.SKIP:
        io.write("Skipped (still pending).")
        return ReviewStepResult.NEXT
    if action == ReviewAction.REJECT:
        try:
            rejected = coordinator.reject_candidate(candidate_id)
        except KeyError:
            io.write(f"Candidate not found: {candidate_id}")
            return ReviewStepResult.NEXT
        io.write(f"Rejected candidate {rejected.candidate_id}")
        return ReviewStepResult.NEXT
    if action == ReviewAction.ACCEPT:
        try:
            linked, _promo = coordinator.accept_candidate(candidate_id, promote=False)
        except KeyError:
            io.write(f"Candidate not found: {candidate_id}")
            return ReviewStepResult.NEXT
        meta = linked.metadata or {}
        if meta.get("merged_into_existing"):
            io.write(
                f"Accepted (merged into existing memory {linked.memory_id}, no promote)."
            )
        else:
            io.write(
                f"Accepted without neural promotion (memory {linked.memory_id})."
            )
        return ReviewStepResult.NEXT
    if action == ReviewAction.ACCEPT_PROMOTE:
        if not confirm_promote(io):
            io.write("Promotion cancelled; candidate remains pending.")
            return ReviewStepResult.STAY
        promote_coord = _coordinator_promote(store=coordinator.store)
        try:
            linked, promo_key = promote_coord.accept_candidate(
                candidate_id, promote=True
            )
        except KeyError:
            io.write(f"Candidate not found: {candidate_id}")
            return ReviewStepResult.NEXT
        meta = linked.metadata or {}
        if meta.get("merged_into_existing"):
            io.write(f"Accepted (merged into existing memory {linked.memory_id}).")
        else:
            io.write(f"Accepted memory {linked.memory_id}.")
        if promo_key:
            io.write(f"Promoted to neural memory: {promo_key}")
        else:
            io.write("Accepted, but neural promotion was not confirmed.")
        return ReviewStepResult.NEXT
    io.write(f"Unknown review action: {action}")
    return ReviewStepResult.STAY


def run_interactive_review(
    *,
    coordinator: Optional[BrainV2Coordinator] = None,
    io: Optional[ReviewIO] = None,
) -> int:
    coord = coordinator or _coordinator_readonly(write=True)
    review_io = io or default_review_io()
    pending = get_pending_review_candidates(coord)
    if not pending:
        review_io.write("No pending Brain v2 memory candidates.")
        return 0

    review_io.write(
        f"Brain v2 guided review — {len(pending)} pending candidate(s)."
    )
    pos = 0
    while True:
        pending = get_pending_review_candidates(coord)
        if pos >= len(pending):
            review_io.write("Review complete.")
            return 0

        candidate = pending[pos]
        review_io.write(format_candidate_review_display(coord, candidate))
        raw = review_io.readline("Action? ")
        action = normalize_review_action(raw)
        if action is None:
            review_io.write(f"Unknown action '{raw.strip()}'. {REVIEW_ACTIONS_HELP}")
            continue

        step = apply_review_action(coord, candidate.candidate_id, action, review_io)
        if step == ReviewStepResult.STOP:
            return 0
        if step == ReviewStepResult.NEXT:
            if action == ReviewAction.SKIP:
                pos += 1
            else:
                pending = get_pending_review_candidates(coord)
                if pos >= len(pending):
                    review_io.write("Review complete.")
                    return 0


def cmd_review() -> int:
    return run_interactive_review()


def cmd_memories() -> int:
    coord = _coordinator_readonly(write=False)
    accepted = coord.store.get_active_accepted_memories(limit=100)
    if not accepted:
        print("No active accepted Brain v2 source-linked memories.")
        return 0
    print(f"Active accepted memories ({len(accepted)}):")
    for mem in accepted:
        seg_n = len(mem.source_segment_ids or [])
        status = (mem.metadata or {}).get("lifecycle_status", "active")
        print(
            f"  {mem.memory_id[:8]}...  status={status}  episode={mem.episode_id[:8]}...  "
            f"segments={seg_n}  {mem.statement[:80]}"
        )
    return 0


def _load_accepted_memory(memory_id: str):
    coord = _coordinator_readonly(write=False)
    resolved = coord.store.resolve_source_linked_memory_id(memory_id)
    memory = coord.store.get_source_linked_memory(resolved)
    if not memory:
        raise KeyError(memory_id)
    return coord, memory


def _guard_repair_apply(action: str, *, confirm_repair: Optional[str]) -> Optional[int]:
    from core.brain_v2.repair_safety import validate_repair_confirmation

    err = validate_repair_confirmation(action, confirm_repair)
    if err:
        print(err, file=sys.stderr)
        return 1
    return None


def cmd_repair_show(memory_id: str) -> int:
    try:
        coord, memory = _load_accepted_memory(memory_id)
    except KeyError:
        print(f"Accepted memory not found: {memory_id}", file=sys.stderr)
        return 1
    from core.brain_v2.repair_preview import format_accepted_memory_detail

    print("Accepted memory detail (read-only):")
    for line in format_accepted_memory_detail(coord, memory):
        print(line)
    return 0


def cmd_retire(
    memory_id: str,
    *,
    preview: bool = False,
    confirm_repair: Optional[str] = None,
) -> int:
    try:
        _coord, memory = _load_accepted_memory(memory_id)
    except KeyError:
        print(f"Accepted memory not found: {memory_id}", file=sys.stderr)
        return 1

    from core.brain_v2.repair_preview import preview_retire
    from core.brain_v2.repair_safety import repair_confirmation_required

    if preview:
        for line in preview_retire(
            memory, live_warning=repair_confirmation_required()
        ):
            print(line)
        return 0

    blocked = _guard_repair_apply("retire", confirm_repair=confirm_repair)
    if blocked is not None:
        return blocked

    coord = _coordinator_readonly(write=True)
    try:
        retired = coord.retire_accepted_memory(memory.memory_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Retired memory {retired.memory_id} (history preserved).")
    return 0


def cmd_supersede(
    memory_id: str,
    statement: str,
    *,
    candidate_type: Optional[str] = None,
    preview: bool = False,
    confirm_repair: Optional[str] = None,
) -> int:
    try:
        _coord, memory = _load_accepted_memory(memory_id)
    except KeyError:
        print(f"Accepted memory not found: {memory_id}", file=sys.stderr)
        return 1

    from core.brain_v2.repair_preview import preview_supersede
    from core.brain_v2.repair_safety import repair_confirmation_required

    if preview:
        for line in preview_supersede(
            memory,
            statement=statement,
            candidate_type=candidate_type,
            live_warning=repair_confirmation_required(),
        ):
            print(line)
        return 0

    blocked = _guard_repair_apply("supersede", confirm_repair=confirm_repair)
    if blocked is not None:
        return blocked

    coord = _coordinator_readonly(write=True)
    try:
        old, new = coord.supersede_accepted_memory(
            memory.memory_id, statement=statement, candidate_type=candidate_type
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Superseded {old.memory_id} -> active replacement {new.memory_id}")
    return 0


def cmd_edit_metadata(
    memory_id: str,
    *,
    candidate_type: Optional[str] = None,
    preview: bool = False,
    confirm_repair: Optional[str] = None,
) -> int:
    try:
        _coord, memory = _load_accepted_memory(memory_id)
    except KeyError:
        print(f"Accepted memory not found: {memory_id}", file=sys.stderr)
        return 1

    from core.brain_v2.repair_preview import preview_edit_metadata
    from core.brain_v2.repair_safety import repair_confirmation_required

    if preview:
        for line in preview_edit_metadata(
            memory,
            candidate_type=candidate_type,
            live_warning=repair_confirmation_required(),
        ):
            print(line)
        return 0

    if candidate_type is None:
        print(
            "No metadata change requested. Pass --brain-v2-memory-type <type> or use --repair-preview.",
            file=sys.stderr,
        )
        return 1

    blocked = _guard_repair_apply("edit_metadata", confirm_repair=confirm_repair)
    if blocked is not None:
        return blocked

    coord = _coordinator_readonly(write=True)
    try:
        updated = coord.edit_accepted_memory_metadata(
            memory.memory_id, candidate_type=candidate_type
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Updated metadata for memory {updated.memory_id}")
    return 0


def cmd_memory_history(memory_id: str) -> int:
    from core.brain_v2.memory_lifecycle import is_operator_reviewed_correction

    coord = _coordinator_readonly(write=False)
    try:
        chain = coord.accepted_memory_history(memory_id)
    except KeyError:
        print(f"Accepted memory not found: {memory_id}", file=sys.stderr)
        return 1
    print(f"Memory history ({len(chain)} record(s), newest first):")
    for mem in chain:
        status = (mem.metadata or {}).get("lifecycle_status", "active")
        if is_operator_reviewed_correction(mem):
            seg_note = "operator-reviewed (no transcript segments)"
        else:
            seg_note = f"{len(mem.source_segment_ids or [])} transcript segment(s)"
        print(f"  {mem.memory_id[:8]}... status={status} evidence={seg_note}")
        print(f"    statement: {mem.statement[:120]}")
        if (mem.metadata or {}).get("supersedes"):
            print(f"    supersedes: {(mem.metadata or {}).get('supersedes')}")
        if (mem.metadata or {}).get("superseded_by"):
            print(f"    superseded_by: {(mem.metadata or {}).get('superseded_by')}")
        pred = (mem.metadata or {}).get("predecessor_evidence_segment_ids")
        if pred:
            print(f"    predecessor_evidence_segment_ids: {len(pred)} (history only)")
        audit = (mem.metadata or {}).get("correction_audit") or []
        if audit:
            last = audit[-1]
            print(
                f"    last_audit: {last.get('action')} at {last.get('at')} "
                f"reason={last.get('reason', 'n/a')}"
            )
    return 0


def cmd_reconcile_status() -> int:
    from core.brain_v2.legacy_reconciliation import run_reconcile_status

    return run_reconcile_status(include_statements=False)


def cmd_repair_plan() -> int:
    from core.brain_v2.legacy_reconciliation import run_repair_plan

    return run_repair_plan()


def cmd_readiness() -> int:
    from core.brain_v2.readiness import run_brain_v2_readiness

    return run_brain_v2_readiness()


def cmd_live_qa_checklist() -> int:
    """Private operator checklist — generic steps only; no live memory content."""
    lines = [
        "Brain v2 private live QA checklist (run locally; do not paste outputs into Git):",
        "  1. hikari.py --brain-v2-status            (counts/metadata only — safe to note counts)",
        "  2. hikari.py --brain-v2-reconcile-status   (categories only; redacted — safe)",
        "  3. hikari.py --brain-v2-repair-plan        (plan only; no legacy apply — safe)",
        "  4. hikari.py --brain-v2-conflicts          (categories/redacted by default — safe)",
        "  4b. hikari.py --brain-v2-readiness         (counts/categories sign-off — safe)",
        "  5. PRIVATE CONTENT OUTPUT (do not paste publicly):",
        "       --brain-v2-pending, --brain-v2-memories, --brain-v2-show, --brain-v2-memory-history",
        "  6. hikari.py --brain-v2-eval               (synthetic fixtures only)",
        "  7. Re-run --brain-v2-status; confirm counts unchanged unless you accepted/reviewed",
        "  8. Brain v2 corrections (preview first, backup, then confirm):",
        "       --brain-v2-repair-show <id>",
        "       --brain-v2-retire <id> --repair-preview",
        "       --brain-v2-retire <id> --confirm-repair RETIRE",
        "       --brain-v2-supersede <id> --brain-v2-statement \"...\" --repair-preview",
        "       --brain-v2-supersede <id> --brain-v2-statement \"...\" --confirm-repair SUPERSEDE",
        "  9. Neural promotion still requires --confirm-promote PROMOTE exactly",
        " 10. Legacy personal recall is quarantined; copy-only repair is optional migration only",
    ]
    for line in lines:
        print(line)
    return 0


def run_brain_v2_cli(
    action: str,
    arg: Optional[str] = None,
    *,
    confirm_promote: Optional[str] = None,
) -> int:
    """Dispatch Brain v2 CLI actions.

    Neural promotion to live brain DB requires an exact ``PROMOTE`` token via
    ``confirm_promote`` on the ``accept`` action only. All other accept paths
    (``accept_no_promote``, interactive ``[a]``, or ``accept`` without token)
    never promote.
    """
    actions = {
        "status": cmd_status,
        "pending": cmd_pending,
        "review": cmd_review,
        "memories": cmd_memories,
        "consolidate": cmd_consolidate,
        "retag_accepted": cmd_retag_accepted,
    }
    if action in actions:
        return actions[action]()
    if action == "show":
        if not arg:
            print("--brain-v2-show requires <candidate_id>", file=sys.stderr)
            return 1
        return cmd_show(arg)
    if action == "accept":
        if not arg:
            print("--brain-v2-accept requires <candidate_id>", file=sys.stderr)
            return 1
        if confirm_promote is not None and confirm_promote != PROMOTE_CONFIRM_TOKEN:
            print(
                f"Invalid --confirm-promote token (expected exactly {PROMOTE_CONFIRM_TOKEN}); "
                "candidate not accepted and not promoted.",
                file=sys.stderr,
            )
            return 1
        promote = confirm_promote == PROMOTE_CONFIRM_TOKEN
        return cmd_accept(arg, promote=promote)
    if action == "accept_no_promote":
        if not arg:
            print("--brain-v2-accept-no-promote requires <candidate_id>", file=sys.stderr)
            return 1
        return cmd_accept(arg, promote=False)
    if action == "reject":
        if not arg:
            print("--brain-v2-reject requires <candidate_id>", file=sys.stderr)
            return 1
        return cmd_reject(arg)
    if action == "repair_show":
        if not arg:
            print("--brain-v2-repair-show requires <accepted_memory_id>", file=sys.stderr)
            return 1
        return cmd_repair_show(arg)
    if action == "retire":
        if not arg:
            print("--brain-v2-retire requires <accepted_memory_id>", file=sys.stderr)
            return 1
        return cmd_retire(arg)
    if action == "supersede":
        print("Use run_brain_v2_cli_supersede helper", file=sys.stderr)
        return 1
    if action == "edit_metadata":
        if not arg:
            print("--brain-v2-edit-metadata requires <accepted_memory_id>", file=sys.stderr)
            return 1
        return cmd_edit_metadata(arg)
    if action == "memory_history":
        if not arg:
            print("--brain-v2-memory-history requires <accepted_memory_id>", file=sys.stderr)
            return 1
        return cmd_memory_history(arg)
    if action == "reconcile_status":
        return cmd_reconcile_status()
    if action == "repair_plan":
        return cmd_repair_plan()
    if action == "live_qa_checklist":
        return cmd_live_qa_checklist()
    if action == "readiness":
        return cmd_readiness()
    print(f"Unknown brain v2 action: {action}", file=sys.stderr)
    return 1


def run_brain_v2_cli_retire(
    memory_id: str,
    *,
    preview: bool = False,
    confirm_repair: Optional[str] = None,
) -> int:
    return cmd_retire(
        memory_id, preview=preview, confirm_repair=confirm_repair
    )


def run_brain_v2_cli_supersede(
    memory_id: str,
    statement: str,
    *,
    candidate_type: Optional[str] = None,
    preview: bool = False,
    confirm_repair: Optional[str] = None,
) -> int:
    return cmd_supersede(
        memory_id,
        statement,
        candidate_type=candidate_type,
        preview=preview,
        confirm_repair=confirm_repair,
    )


def run_brain_v2_cli_edit_metadata(
    memory_id: str,
    *,
    candidate_type: Optional[str] = None,
    preview: bool = False,
    confirm_repair: Optional[str] = None,
) -> int:
    return cmd_edit_metadata(
        memory_id,
        candidate_type=candidate_type,
        preview=preview,
        confirm_repair=confirm_repair,
    )
