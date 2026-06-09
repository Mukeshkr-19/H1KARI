"""Read-only previews for Brain v2 accepted-memory repair actions."""

from __future__ import annotations

from typing import List, Optional

from core.brain_v2.coordinator import BrainV2Coordinator
from core.brain_v2.memory_lifecycle import is_operator_reviewed_correction
from core.brain_v2.repair_safety import format_live_repair_warning
from core.brain_v2.schemas import SourceLinkedMemory


def _format_audit_tail(metadata: dict, *, limit: int = 3) -> List[str]:
    audit = list((metadata or {}).get("correction_audit") or [])
    if not audit:
        return []
    lines = ["correction_audit (recent):"]
    for entry in audit[-limit:]:
        action = entry.get("action", "?")
        at = entry.get("at", "?")
        reason = entry.get("reason")
        suffix = f" reason={reason}" if reason else ""
        lines.append(f"  - {action} at {at}{suffix}")
    return lines


def format_accepted_memory_detail(
    coordinator: BrainV2Coordinator,
    memory: SourceLinkedMemory,
) -> List[str]:
    meta = memory.metadata or {}
    status = meta.get("lifecycle_status", "active")
    ctype = meta.get("candidate_type", "n/a")
    lines = [
        f"memory_id:     {memory.memory_id}",
        f"episode_id:    {memory.episode_id}",
        f"candidate_id:  {memory.candidate_id}",
        f"status:        {status}",
        f"type:          {ctype}",
        f"layer:         {memory.layer or 'n/a'}",
        f"accepted_at:   {memory.accepted_at or 'n/a'}",
        f"statement:     {memory.statement}",
    ]
    if meta.get("supersedes"):
        lines.append(f"supersedes:      {meta.get('supersedes')}")
    if meta.get("superseded_by"):
        lines.append(f"superseded_by:   {meta.get('superseded_by')}")
    if is_operator_reviewed_correction(memory):
        lines.append("evidence:        operator-reviewed correction (no new transcript segments)")
        pred = meta.get("predecessor_evidence_segment_ids") or []
        if pred:
            lines.append(f"predecessor_evidence_segment_ids: {len(pred)} segment(s)")
    else:
        seg_ids = memory.source_segment_ids or []
        lines.append(f"evidence:        {len(seg_ids)} source-linked transcript segment(s)")
        segments = coordinator.store.get_raw_segments(memory.episode_id)
        id_set = set(seg_ids)
        for seg in segments:
            if seg.segment_id in id_set:
                role = "user" if seg.is_user else seg.speaker_label
                lines.append(f"  [{role}] {seg.text[:240]}")
    lines.extend(_format_audit_tail(meta))
    return lines


def preview_retire(memory: SourceLinkedMemory, *, live_warning: bool) -> List[str]:
    lines = [
        "Repair preview: RETIRE",
        f"  memory_id: {memory.memory_id}",
        f"  statement: {memory.statement}",
        "  effect: mark lifecycle_status=retired; row and evidence preserved; excluded from recall",
    ]
    if live_warning:
        lines.append("")
        lines.append(format_live_repair_warning("retire"))
    return lines


def preview_supersede(
    memory: SourceLinkedMemory,
    *,
    statement: str,
    candidate_type: Optional[str],
    live_warning: bool,
) -> List[str]:
    lines = [
        "Repair preview: SUPERSEDE",
        f"  memory_id: {memory.memory_id}",
        f"  current:   {memory.statement}",
        f"  replacement statement: {statement}",
    ]
    if candidate_type:
        lines.append(f"  replacement type: {candidate_type}")
    pred = list(memory.source_segment_ids or [])
    lines.extend(
        [
            "  effect: old row -> superseded; new active row with operator correction provenance",
            f"  predecessor evidence preserved: {len(pred)} segment id(s) in metadata",
        ]
    )
    if live_warning:
        lines.append("")
        lines.append(format_live_repair_warning("supersede"))
    return lines


def preview_edit_metadata(
    memory: SourceLinkedMemory,
    *,
    candidate_type: Optional[str],
    live_warning: bool,
) -> List[str]:
    meta = memory.metadata or {}
    current_type = meta.get("candidate_type", "n/a")
    lines = [
        "Repair preview: EDIT_METADATA",
        f"  memory_id: {memory.memory_id}",
        f"  statement: {memory.statement} (unchanged)",
        f"  current type: {current_type}",
    ]
    if candidate_type:
        lines.append(f"  new type: {candidate_type}")
    else:
        lines.append("  new type: (no change — pass --brain-v2-memory-type to update)")
    lines.append("  effect: metadata/type update with correction_audit entry")
    if live_warning:
        lines.append("")
        lines.append(format_live_repair_warning("edit_metadata"))
    return lines
