"""Working memory — small fast layer for the active session."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional


@dataclass
class WorkingMemoryItem:
    key: str
    value: str
    kind: str = "turn"
    metadata: Dict[str, Any] = field(default_factory=dict)


class WorkingMemory:
    def __init__(self, max_items: int = 12):
        self.max_items = max_items
        self._items: Deque[WorkingMemoryItem] = deque(maxlen=max_items)
        self.current_task: Optional[str] = None
        self.active_session_id: Optional[str] = None
        self.speaker_context: Dict[str, str] = {}
        self.current_location: Optional[str] = None
        self.current_location_statement: Optional[str] = None
        self._session_facts: Deque[WorkingMemoryItem] = deque(maxlen=24)

    def clear(self) -> None:
        """Drop session turns and speaker context (e.g. after guest session ends)."""
        self._items.clear()
        self.current_task = None
        self.active_session_id = None
        self.speaker_context = {}
        self.current_location = None
        self.current_location_statement = None
        self._session_facts.clear()

    def set_session(self, session_id: str) -> None:
        self.clear()
        self.active_session_id = session_id

    def set_task(self, task: Optional[str]) -> None:
        self.current_task = (task or "").strip() or None

    def note_speaker(self, speaker: Optional[str], household: Optional[str] = None) -> None:
        if speaker:
            self.speaker_context["speaker"] = speaker
        if household:
            self.speaker_context["household"] = household

    def push_turn(self, user_text: str, assistant_text: str = "", **meta: Any) -> None:
        snippet = f"U: {(user_text or '')[:80]}"
        if assistant_text:
            snippet += f" | A: {assistant_text[:80]}"
        self._items.append(
            WorkingMemoryItem(key="turn", value=snippet, kind="turn", metadata=meta)
        )

    def push(self, key: str, value: str, kind: str = "note", **meta: Any) -> None:
        self._items.append(
            WorkingMemoryItem(key=key, value=value, kind=kind, metadata=meta)
        )

    def note_current_location(self, location: str, statement: str = "") -> None:
        loc = (location or "").strip()
        if not loc:
            return
        self.current_location = loc
        self.current_location_statement = (statement or "").strip() or None
        self.push(
            "current_location",
            loc,
            kind="current_location",
            statement=self.current_location_statement or "",
        )

    def get_current_location(self) -> Optional[tuple[str, str]]:
        if not self.current_location:
            return None
        stmt = self.current_location_statement or f"I'm in {self.current_location}."
        return self.current_location, stmt

    def note_session_fact(self, statement: str, **meta: Any) -> None:
        stmt = (statement or "").strip()
        if not stmt:
            return
        self._session_facts.append(
            WorkingMemoryItem(
                key="session_fact",
                value=stmt,
                kind="session_fact",
                metadata=dict(meta),
            )
        )

    def session_facts(self) -> List[str]:
        return [item.value for item in self._session_facts if item.value]

    def answer_from_session_facts(self, query: str) -> Optional[str]:
        """Best-effort recall from session-only facts (not durable Brain v2)."""
        q = (query or "").lower()
        if not q or not self._session_facts:
            return None
        for item in reversed(self._session_facts):
            stmt = item.value
            low = stmt.lower()
            if not stmt:
                continue
            if "study" in q or "major" in q or "degree" in q:
                if any(w in low for w in ("study", "student", "bachelor", "major", "graduat")):
                    return stmt.rstrip(".") + "."
            if "live" in q or "home" in q:
                if "live in" in low:
                    return stmt.rstrip(".") + "."
            if "name" in q and ("my name" in low or "call me" in low or "legal name" in low):
                return stmt.rstrip(".") + "."
            if "graduat" in q and "graduat" in low:
                return stmt.rstrip(".") + "."
        latest = self._session_facts[-1].value if self._session_facts else ""
        if latest and any(
            token in latest.lower()
            for token in re.findall(r"[a-z]{4,}", q)
            if token not in {"what", "does", "know", "about", "tell", "that", "this", "have"}
        ):
            return latest.rstrip(".") + "."
        return None

    def recent_items(self, limit: int = 8) -> List[WorkingMemoryItem]:
        return list(self._items)[-limit:]

    def to_context_lines(self, limit: int = 6) -> List[str]:
        lines: List[str] = []
        if self.current_task:
            lines.append(f"task: {self.current_task}")
        if self.current_location:
            lines.append(f"current_location: {self.current_location}")
        if self.speaker_context.get("speaker"):
            lines.append(f"speaker: {self.speaker_context['speaker']}")
        for item in self.recent_items(limit):
            lines.append(f"{item.kind}: {item.value}")
        return lines
