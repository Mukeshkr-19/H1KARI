"""Bounded, actor-scoped context for an active HIKARI conversation.

This layer is deliberately separate from durable Brain v2 memory.  It keeps
enough exact dialogue to resolve ordinary follow-ups while retaining a bounded
local digest for long sessions.  It never grants authority and never performs
I/O, provider calls, or persistence on import or construction.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import re
import threading
from typing import Deque, Dict, Mapping, Optional, Sequence, Tuple


_MAX_TURN_TEXT = 8_000
_MAX_RECENT_PAIRS = 8
_MAX_RELEVANT_PAIRS = 4
_MAX_SESSION_PAIRS = 256
_MAX_ARCHIVE_PAIRS = 512
_MAX_ARCHIVE_TEXT = 1_000
_MAX_DIGEST_CHARS = 12_000
_MAX_DIGEST_LINE = 360
_MAX_MESSAGE_CHARS = 24_000
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]{2,}", re.I)
_LOW_INFORMATION = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "could",
    "from",
    "have",
    "just",
    "like",
    "that",
    "their",
    "there",
    "these",
    "they",
    "this",
    "those",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "your",
}


def _bounded_text(value: object, *, limit: int = _MAX_TURN_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    text = value.replace("\x00", "").strip()
    return text[:limit]


def _tokens(text: str) -> frozenset[str]:
    return frozenset(
        token.casefold()
        for token in _WORD.findall(text or "")
        if token.casefold() not in _LOW_INFORMATION
    )


@dataclass(frozen=True)
class ConversationScope:
    """Server-derived identity for one isolated conversational stream."""

    actor_id: str
    session_id: str
    source: str
    speaker: str = "owner"
    guest: bool = False

    def __repr__(self) -> str:
        return "ConversationScope(guest=%s)" % self.guest


@dataclass(frozen=True)
class ConversationTurn:
    sequence: int
    user_text: str
    assistant_text: str
    source: str

    def __repr__(self) -> str:
        return f"ConversationTurn(sequence={self.sequence})"


@dataclass(frozen=True)
class ToolContextFrame:
    kind: str
    sequence: int
    slots: Tuple[Tuple[str, str], ...] = ()

    def slot(self, name: str) -> Optional[str]:
        for key, value in self.slots:
            if key == name:
                return value
        return None

    def __repr__(self) -> str:
        return f"ToolContextFrame(kind={self.kind!r}, sequence={self.sequence})"


@dataclass(frozen=True)
class ConversationContextPacket:
    """A bounded provider packet; repr intentionally excludes conversation text."""

    messages: Tuple[Mapping[str, str], ...] = ()
    digest: str = ""
    covered_through: int = 0

    def __repr__(self) -> str:
        return (
            "ConversationContextPacket("
            f"messages={len(self.messages)}, covered_through={self.covered_through})"
        )


@dataclass
class _SessionState:
    turns: Deque[ConversationTurn] = field(
        default_factory=lambda: deque(maxlen=_MAX_SESSION_PAIRS)
    )
    archive: Deque[ConversationTurn] = field(
        default_factory=lambda: deque(maxlen=_MAX_ARCHIVE_PAIRS)
    )
    digest_lines: Deque[str] = field(default_factory=deque)
    digest_chars: int = 0
    next_sequence: int = 1
    tools: Dict[str, ToolContextFrame] = field(default_factory=dict)


class ConversationContextEngine:
    """Thread-safe, in-memory context store keyed by transport-derived scope."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[ConversationScope, _SessionState] = {}

    def __repr__(self) -> str:
        with self._lock:
            return f"ConversationContextEngine(sessions={len(self._sessions)})"

    def _state(self, scope: ConversationScope) -> _SessionState:
        return self._sessions.setdefault(scope, _SessionState())

    def record_turn(
        self,
        scope: ConversationScope,
        user_text: str,
        assistant_text: str,
    ) -> None:
        user = _bounded_text(user_text)
        assistant = _bounded_text(assistant_text)
        if not user and not assistant:
            return
        with self._lock:
            state = self._state(scope)
            if len(state.turns) == state.turns.maxlen and state.turns:
                evicted = state.turns[0]
                self._append_digest(state, evicted)
                state.archive.append(
                    ConversationTurn(
                        sequence=evicted.sequence,
                        user_text=evicted.user_text[:_MAX_ARCHIVE_TEXT],
                        assistant_text=evicted.assistant_text[:_MAX_ARCHIVE_TEXT],
                        source=evicted.source,
                    )
                )
            state.turns.append(
                ConversationTurn(
                    sequence=state.next_sequence,
                    user_text=user,
                    assistant_text=assistant,
                    source=_bounded_text(scope.source, limit=32),
                )
            )
            state.next_sequence += 1

    def note_tool(
        self,
        scope: ConversationScope,
        kind: str,
        slots: Optional[Mapping[str, str]] = None,
    ) -> None:
        tool_kind = _bounded_text(kind, limit=48).casefold()
        if not tool_kind:
            return
        bounded_slots = tuple(
            sorted(
                (
                    _bounded_text(key, limit=48),
                    _bounded_text(value, limit=256),
                )
                for key, value in (slots or {}).items()
                if _bounded_text(key, limit=48) and _bounded_text(value, limit=256)
            )
        )[:12]
        with self._lock:
            state = self._state(scope)
            state.tools[tool_kind] = ToolContextFrame(
                kind=tool_kind,
                sequence=max(0, state.next_sequence - 1),
                slots=bounded_slots,
            )

    def latest_tool(
        self,
        scope: ConversationScope,
        kind: Optional[str] = None,
        *,
        max_age_turns: int = 24,
    ) -> Optional[ToolContextFrame]:
        with self._lock:
            state = self._sessions.get(scope)
            if state is None:
                return None
            if kind:
                frame = state.tools.get(kind.casefold())
            else:
                frame = max(
                    state.tools.values(),
                    key=lambda item: item.sequence,
                    default=None,
                )
            if frame is None:
                return None
            age = max(0, state.next_sequence - 1 - frame.sequence)
            return frame if age <= max_age_turns else None

    def compose(
        self,
        scope: ConversationScope,
        current_user_text: str,
    ) -> ConversationContextPacket:
        """Return recent exact turns plus relevant older turns under fixed bounds."""
        current_tokens = _tokens(current_user_text)
        with self._lock:
            state = self._sessions.get(scope)
            if state is None or not state.turns:
                return ConversationContextPacket()
            turns = list(state.turns)
            recent = turns[-_MAX_RECENT_PAIRS:]
            recent_sequences = {turn.sequence for turn in recent}
            active_older = turns[:-_MAX_RECENT_PAIRS]
            older = [*state.archive, *active_older]
            scored = []
            for turn in older:
                turn_tokens = _tokens(f"{turn.user_text} {turn.assistant_text}")
                overlap = len(current_tokens & turn_tokens)
                if overlap:
                    scored.append((overlap, turn.sequence, turn))
            relevant = [
                item[2]
                for item in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[
                    :_MAX_RELEVANT_PAIRS
                ]
            ]
            selected = sorted(
                {turn.sequence: turn for turn in (*relevant, *recent)}.values(),
                key=lambda turn: turn.sequence,
            )

            messages = []
            used_chars = 0
            for turn in selected:
                for role, content in (
                    ("user", turn.user_text),
                    ("assistant", turn.assistant_text),
                ):
                    if not content:
                        continue
                    remaining = _MAX_MESSAGE_CHARS - used_chars
                    if remaining <= 0:
                        break
                    bounded = content[:remaining]
                    messages.append({"role": role, "content": bounded})
                    used_chars += len(bounded)
                if used_chars >= _MAX_MESSAGE_CHARS:
                    break

            digest = "\n".join(state.digest_lines)
            if active_older:
                unselected = [
                    turn
                    for turn in active_older
                    if turn.sequence not in recent_sequences
                    and turn.sequence not in {item.sequence for item in relevant}
                ]
                tail = "\n".join(self._compact_turn(turn) for turn in unselected[-12:])
                digest = "\n".join(part for part in (digest, tail) if part)
            return ConversationContextPacket(
                messages=tuple(messages),
                digest=digest[-_MAX_DIGEST_CHARS:],
                covered_through=max((turn.sequence for turn in older), default=0),
            )

    def clear(self, scope: ConversationScope) -> None:
        with self._lock:
            self._sessions.pop(scope, None)

    def restore_pairs(
        self,
        scope: ConversationScope,
        pairs: Sequence[Tuple[str, str]],
    ) -> None:
        """Replace one in-memory scope from a validated private transcript."""
        if isinstance(pairs, (str, bytes)) or not isinstance(pairs, Sequence):
            return
        self.clear(scope)
        for pair in pairs[-(_MAX_SESSION_PAIRS + _MAX_ARCHIVE_PAIRS) :]:
            if (
                not isinstance(pair, Sequence)
                or isinstance(pair, (str, bytes))
                or len(pair) != 2
            ):
                self.clear(scope)
                return
            self.record_turn(scope, pair[0], pair[1])

    def clear_actor_session(self, actor_id: str, session_id: str) -> None:
        with self._lock:
            doomed = [
                scope
                for scope in self._sessions
                if scope.actor_id == actor_id and scope.session_id == session_id
            ]
            for scope in doomed:
                self._sessions.pop(scope, None)

    def clear_guest_scopes(self, actor_id: str, session_id: str) -> None:
        with self._lock:
            doomed = [
                scope
                for scope in self._sessions
                if scope.actor_id == actor_id
                and scope.session_id == session_id
                and scope.guest
            ]
            for scope in doomed:
                self._sessions.pop(scope, None)

    def _append_digest(self, state: _SessionState, turn: ConversationTurn) -> None:
        line = self._compact_turn(turn)
        if not line:
            return
        state.digest_lines.append(line)
        state.digest_chars += len(line) + 1
        while state.digest_lines and state.digest_chars > _MAX_DIGEST_CHARS:
            removed = state.digest_lines.popleft()
            state.digest_chars -= len(removed) + 1

    @staticmethod
    def _compact_turn(turn: ConversationTurn) -> str:
        user = re.sub(r"\s+", " ", turn.user_text).strip()
        assistant = re.sub(r"\s+", " ", turn.assistant_text).strip()
        text = f"#{turn.sequence} U: {user} | A: {assistant}"
        return text[:_MAX_DIGEST_LINE]


def validate_conversation_messages(
    messages: Optional[Sequence[Mapping[str, str]]],
) -> Tuple[Mapping[str, str], ...]:
    """Fail closed to a bounded user/assistant-only provider history."""
    if messages is None:
        return ()
    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
        return ()
    validated = []
    used_chars = 0
    for item in messages[: 2 * (_MAX_RECENT_PAIRS + _MAX_RELEVANT_PAIRS)]:
        if not isinstance(item, Mapping) or set(item) != {"role", "content"}:
            return ()
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            return ()
        remaining = _MAX_MESSAGE_CHARS - used_chars
        if remaining <= 0:
            break
        bounded = _bounded_text(content, limit=min(_MAX_TURN_TEXT, remaining))
        if not bounded:
            continue
        validated.append({"role": role, "content": bounded})
        used_chars += len(bounded)
    return tuple(validated)


def validate_conversation_digest(value: object) -> str:
    """Bound an older-turn digest while preserving its user-authored trust level."""
    return _bounded_text(value, limit=_MAX_DIGEST_CHARS)
