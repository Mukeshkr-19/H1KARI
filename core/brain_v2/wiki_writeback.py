"""Compile active accepted Brain v2 memories into private markdown wiki pages."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from core.brain_v2.episode_store import EpisodeStore
from core.brain_v2.memory_lifecycle import is_active_memory
from core.brain_v2.profile_summary import _profile_bucket
from core.brain_v2.schemas import SourceLinkedMemory
from core.brain_v2.wiki_paths import resolve_wiki_dir

WIKI_PAGE_NAMES: Tuple[str, ...] = (
    "profile.md",
    "education.md",
    "locations.md",
    "relationships.md",
    "preferences.md",
    "plans.md",
)

_BUCKET_TO_PAGE: Dict[str, str] = {
    "identity": "profile.md",
    "user_education": "education.md",
    "partner_education": "education.md",
    "location": "locations.md",
    "relation": "relationships.md",
    "preference": "preferences.md",
    "plan": "plans.md",
    "event": "plans.md",
}

_PAGE_TITLES: Dict[str, str] = {
    "profile.md": "Profile",
    "education.md": "Education",
    "locations.md": "Locations",
    "relationships.md": "Relationships",
    "preferences.md": "Preferences",
    "plans.md": "Plans",
}

_EMPTY_MESSAGE = (
    "No active accepted Brain v2 memories to compile into wiki pages."
)


@dataclass
class WikiCompileResult:
    pages: Dict[str, str] = field(default_factory=dict)
    memory_ids: List[str] = field(default_factory=list)
    skipped_guest: int = 0
    skipped_ineligible: int = 0

    @property
    def empty(self) -> bool:
        return not self.pages


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_guest_sourced(store: EpisodeStore, memory: SourceLinkedMemory) -> bool:
    meta = memory.metadata or {}
    if meta.get("guest_session") or meta.get("session_speaker_intro"):
        return True
    segment_ids = set(memory.source_segment_ids or [])
    if not segment_ids:
        return False
    for seg in store.get_raw_segments(memory.episode_id):
        if seg.segment_id not in segment_ids:
            continue
        seg_meta = seg.metadata or {}
        if seg_meta.get("session_speaker_intro") or seg_meta.get("guest_session"):
            return True
        speaker = (seg.speaker_label or "").strip().lower()
        if speaker.startswith("guest"):
            return True
    return False


def wiki_eligible_memory(
    store: EpisodeStore,
    memory: SourceLinkedMemory,
) -> bool:
    """True when an active accepted memory may appear in compiled wiki pages."""
    if not is_active_memory(memory):
        return False
    meta = memory.metadata or {}
    ctype = str(meta.get("candidate_type", "fact"))
    if ctype == "current_location":
        return False
    if _is_guest_sourced(store, memory):
        return False
    bucket = _profile_bucket(memory)
    return bucket in _BUCKET_TO_PAGE


def collect_wiki_memories(
    store: EpisodeStore,
    *,
    limit: int = 500,
) -> Tuple[List[SourceLinkedMemory], int, int]:
    """Return eligible memories plus skip counts (guest, ineligible type)."""
    raw = store.get_active_accepted_memories(limit=limit)
    eligible: List[SourceLinkedMemory] = []
    skipped_guest = 0
    skipped_ineligible = 0
    for mem in raw:
        if not is_active_memory(mem):
            skipped_ineligible += 1
            continue
        if _is_guest_sourced(store, mem):
            skipped_guest += 1
            continue
        if not wiki_eligible_memory(store, mem):
            skipped_ineligible += 1
            continue
        eligible.append(mem)
    eligible.sort(key=lambda m: m.memory_id)
    return eligible, skipped_guest, skipped_ineligible


def _format_page(
    filename: str,
    memories: Sequence[SourceLinkedMemory],
    *,
    generated_at: str,
) -> str:
    title = _PAGE_TITLES.get(filename, filename)
    lines = [
        f"# {title}",
        "",
        "Private compiled wiki from **active accepted** Brain v2 memories only.",
        "Excluded: pending, rejected, retired, superseded, session trip cities, "
        "tasks, guest content, and raw transcripts.",
        "",
        f"_compiled_at: {generated_at}_",
        "",
    ]
    if not memories:
        lines.append("*No active accepted memories.*")
        lines.append("")
    else:
        for mem in sorted(memories, key=lambda m: m.memory_id):
            meta = mem.metadata or {}
            ctype = str(meta.get("candidate_type", "fact"))
            stmt = (mem.statement or "").strip()
            lines.append(f"- {stmt}")
            lines.append(
                f"  - provenance: memory_id=`{mem.memory_id}`, "
                f"candidate_type=`{ctype}`"
            )
            if mem.candidate_id:
                lines.append(f"  - candidate_id=`{mem.candidate_id}`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def compile_wiki_pages(
    store: EpisodeStore,
    *,
    generated_at: Optional[str] = None,
) -> WikiCompileResult:
    """Build markdown pages from active accepted memories (read-only)."""
    generated = generated_at or _utc_now_iso()
    memories, skipped_guest, skipped_ineligible = collect_wiki_memories(store)
    if not memories:
        return WikiCompileResult(
            skipped_guest=skipped_guest,
            skipped_ineligible=skipped_ineligible,
        )

    grouped: Dict[str, List[SourceLinkedMemory]] = {name: [] for name in WIKI_PAGE_NAMES}
    for mem in memories:
        page = _BUCKET_TO_PAGE[_profile_bucket(mem)]
        grouped[page].append(mem)

    pages: Dict[str, str] = {}
    memory_ids: List[str] = []
    for filename in WIKI_PAGE_NAMES:
        items = grouped.get(filename) or []
        if not items:
            if filename == "plans.md":
                continue
        pages[filename] = _format_page(filename, items, generated_at=generated)
        memory_ids.extend(m.memory_id for m in items)

    return WikiCompileResult(
        pages=pages,
        memory_ids=sorted(memory_ids),
        skipped_guest=skipped_guest,
        skipped_ineligible=skipped_ineligible,
    )


def format_wiki_preview(result: WikiCompileResult, *, wiki_dir: Optional[Path] = None) -> str:
    target = wiki_dir or resolve_wiki_dir()
    if result.empty:
        return _EMPTY_MESSAGE

    lines = [
        "Wiki writeback preview (read-only)",
        f"output_dir: {target}",
        f"active_memories_compiled: {len(result.memory_ids)}",
        f"pages: {len(result.pages)}",
    ]
    if result.skipped_guest:
        lines.append(f"skipped_guest_sourced: {result.skipped_guest}")
    if result.skipped_ineligible:
        lines.append(f"skipped_ineligible: {result.skipped_ineligible}")
    lines.append("")
    for name in WIKI_PAGE_NAMES:
        if name not in result.pages:
            continue
        lines.append(f"--- {name} ---")
        lines.append(result.pages[name].rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def apply_wiki_writeback(
    result: WikiCompileResult,
    *,
    wiki_dir: Optional[Path] = None,
) -> Path:
    """Write compiled pages atomically; remove stale known pages when empty sections."""
    target = (wiki_dir or resolve_wiki_dir()).expanduser().resolve()

    # If the target directory exists, clean up stale wiki pages
    if target.is_dir():
        for filename in WIKI_PAGE_NAMES:
            path = target / filename
            if filename not in result.pages and path.is_file():
                path.unlink()

    if result.empty:
        return target

    target.mkdir(parents=True, exist_ok=True)
    for filename, content in result.pages.items():
        _atomic_write_text(target / filename, content)

    return target


def _open_readonly_store() -> EpisodeStore:
    from core.brain_v2.db_paths import open_readonly_episode_store

    return open_readonly_episode_store()


def cmd_wiki_preview() -> int:
    if not _brain_v2_enabled():
        print("Brain v2 is disabled (HIKARI_DISABLE_BRAIN_V2=1).", file=sys.stderr)
        return 1
    try:
        store = _open_readonly_store()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = compile_wiki_pages(store)
    print(format_wiki_preview(result))
    return 0


def cmd_wiki_writeback() -> int:
    if not _brain_v2_enabled():
        print("Brain v2 is disabled (HIKARI_DISABLE_BRAIN_V2=1).", file=sys.stderr)
        return 1
    try:
        store = _open_readonly_store()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = compile_wiki_pages(store)
    target = resolve_wiki_dir()
    if result.empty:
        apply_wiki_writeback(result, wiki_dir=target)
        print(_EMPTY_MESSAGE)
        return 0
    written = apply_wiki_writeback(result, wiki_dir=target)
    print(f"Wrote {len(result.pages)} wiki page(s) to {written}")
    for name in WIKI_PAGE_NAMES:
        if name in result.pages:
            print(f"  {name}")
    return 0


def _brain_v2_enabled() -> bool:
    from core.brain_v2.status import is_brain_v2_enabled

    return is_brain_v2_enabled()
