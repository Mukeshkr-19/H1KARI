"""Deterministic DurableMemoryIntent parsing (pure; no DB or network).

Detection, command stripping, anaphoric resolution, and policy classification
share one parsed result. Reprs never expose memory body text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal, Optional, Sequence, Tuple

ActionKind = Literal["none", "save", "correct", "forget"]
ScopeKind = Literal["ephemeral", "durable"]
TargetKind = Literal["inline", "anaphoric", "unresolved"]

MAX_TEXT_LEN = 2000
MAX_BODY_LEN = 1000
MAX_ID_LEN = 80
MAX_SPANS = 8
MAX_CONFIDENCE = 1.0
MIN_CONFIDENCE = 0.0
DEFAULT_ANAPHORA_TTL_MS = 120_000

_CANONICAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")

# Explicit durable-owner consent wrappers (bounded natural wording).
_SAVE_CORE = (
    r"(?:please\s+)?"
    r"(?:"
    r"remember\s+(?:this|that)(?:\s+(?:in|to|into)\s+(?:my\s+|your\s+)?(?:brain|memory))?|"
    r"keep\s+(?:this|that)\s+(?:in|to|into)\s+(?:my\s+|your\s+)?(?:brain|memory)|"
    r"(?:save|store|add|put)\s+(?:this|that)\s+"
    r"(?:as\s+(?:a\s+)?memory|(?:in|to|into)\s+(?:my\s+|your\s+)?(?:brain|memory))"
    r")"
)

_SAVE_ANY = re.compile(rf"\b{_SAVE_CORE}\b", re.I)
_SAVE_PREFIX = re.compile(
    rf"^\s*{_SAVE_CORE}(?:\s*[:;,\-]\s*|\s+)(?P<body>.+?)\s*$",
    re.I,
)
_SAVE_SUFFIX = re.compile(
    rf"^(?P<body>.+?)(?:\s*[,;.\-]\s*|\s+){_SAVE_CORE}\s*[.!?]*\s*$",
    re.I,
)
_SAVE_BARE = re.compile(rf"^\s*{_SAVE_CORE}\s*[.!?]*\s*$", re.I)

_CORRECT_CORE = (
    r"(?:please\s+)?"
    r"(?:"
    r"correct\s+(?:this|that)(?:\s+(?:in|to|into)\s+(?:my\s+|your\s+)?(?:brain|memory))?|"
    r"(?:update|replace)\s+(?:this|that)\s+(?:memory|(?:in|to|into)\s+(?:my\s+|your\s+)?(?:brain|memory))"
    r")"
)
_CORRECT_ANY = re.compile(rf"\b{_CORRECT_CORE}\b", re.I)
_CORRECT_PREFIX = re.compile(
    rf"^\s*{_CORRECT_CORE}(?:\s*[:;,\-]\s*|\s+)(?P<body>.+?)\s*$",
    re.I,
)
_CORRECT_SUFFIX = re.compile(
    rf"^(?P<body>.+?)(?:\s*[,;.\-]\s*|\s+){_CORRECT_CORE}\s*[.!?]*\s*$",
    re.I,
)
_CORRECT_BARE = re.compile(rf"^\s*{_CORRECT_CORE}\s*[.!?]*\s*$", re.I)

_FORGET_CORE = (
    r"(?:please\s+)?"
    r"(?:"
    r"forget\s+(?:this|that)(?:\s+(?:from|in)\s+(?:my\s+|your\s+)?(?:brain|memory))?|"
    r"(?:delete|remove|erase)\s+(?:this|that)\s+"
    r"(?:memory|(?:from|in)\s+(?:my\s+|your\s+)?(?:brain|memory))"
    r")"
)
_FORGET_ANY = re.compile(rf"\b{_FORGET_CORE}\b", re.I)
_FORGET_PREFIX = re.compile(
    rf"^\s*{_FORGET_CORE}(?:\s*[:;,\-]\s*|\s+)(?P<body>.+?)\s*$",
    re.I,
)
_FORGET_SUFFIX = re.compile(
    rf"^(?P<body>.+?)(?:\s*[,;.\-]\s*|\s+){_FORGET_CORE}\s*[.!?]*\s*$",
    re.I,
)
_FORGET_BARE = re.compile(rf"^\s*{_FORGET_CORE}\s*[.!?]*\s*$", re.I)

_NEGATED = re.compile(
    r"\b(?:don'?t|do\s+not|never|stop)\s+(?:please\s+)?"
    r"(?:remember|save|store|keep|forget|correct|update|delete|remove)\b",
    re.I,
)
_QUESTION_SAVE = re.compile(
    r"^\s*(?:should|can|could|would|do|did|will|may|might)\b.+\b"
    r"(?:remember|save|store|keep).+\?\s*$"
    r"|^\s*(?:did you|have you)\s+(?:remember|save|store).+\?\s*$",
    re.I,
)
_MODEL_CLAIM = re.compile(
    r"\b(?:i(?:'ve| have)?\s+saved|i\s+stored|i\s+remembered|"
    r"saved\s+that\s+to\s+your\s+brain|stored\s+that\s+in\s+your\s+brain)\b",
    re.I,
)
_QUOTED_CLAIM = re.compile(
    r"""['\"][^'\"]*\b(?:saved|remember|brain|memory)\b[^'\"]*['\"]""",
    re.I,
)
_OTHER_SYSTEM = re.compile(
    r"\b(?:tell|ask|instruct|configure)\s+(?:the\s+)?(?:system|server|backend|database|api|"
    r"other\s+assistant|chatgpt|model)\b.+\b(?:remember|save|store|keep)\b",
    re.I,
)
_THIRD_PARTY = re.compile(
    r"\b(?:their|his|her|someone(?:'s)?|another\s+person(?:'s)?)\s+"
    r"(?:ssn|social\s+security|password|secret|private\s+key|bank\s+account|"
    r"credit\s+card|medical\s+record)\b|"
    r"\b(?:ssn|password|secret)\s+of\s+(?:my\s+)?(?:friend|colleague|coworker|"
    r"neighbor|boss|client)\b",
    re.I,
)
_PREFERENCE_CASUAL = re.compile(
    r"^\s*i\s+(?:like|love|prefer|enjoy|hate|dislike)\b",
    re.I,
)
_DECLARATIVE_OWNER = re.compile(
    r"^\s*(?:i\s+(?:am|was|have|live|work|prefer|like|love|enjoy)|"
    r"my\s+(?:name|home|birthday|job|favorite|partner|sister|brother|"
    r"mom|dad|mother|father))\b",
    re.I,
)


ReasonCode = Literal[
    "empty_input",
    "invalid_correlation",
    "text_too_long",
    "ordinary_conversation",
    "preference_without_consent",
    "inferred_suggestion_only",
    "explicit_durable_consent",
    "anaphoric_resolved",
    "anaphoric_unresolved",
    "negated_request",
    "question_about_saving",
    "quoted_assistant_claim",
    "model_claim_of_save",
    "other_system_instruction",
    "third_party_unsafe",
    "correction_request",
    "forget_request",
]


@dataclass(frozen=True)
class SourceSpan:
    """Inclusive-exclusive character span into the original bounded text."""

    start: int
    end: int
    kind: str = "command"

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("invalid_span")


@dataclass(frozen=True)
class RecentContextUtterance:
    """Synthetic recent-context utterance for anaphoric resolution only."""

    actor_id: str
    session_id: str
    text: str
    spoken_at_ms: int
    speaker_role: str = "owner"
    restart_generation: int = 0


@dataclass(frozen=True)
class DurableMemoryIntent:
    """Immutable durable-memory intent. Body text is omitted from repr."""

    action: ActionKind
    scope: ScopeKind
    target: TargetKind
    normalized_body: str
    confidence: float
    source_spans: Tuple[SourceSpan, ...]
    actor_id: str
    session_id: str
    reason_code: ReasonCode
    request_exact_fact: bool = False

    def __post_init__(self) -> None:
        if not (MIN_CONFIDENCE <= float(self.confidence) <= MAX_CONFIDENCE):
            raise ValueError("confidence_out_of_bounds")
        if len(self.source_spans) > MAX_SPANS:
            raise ValueError("too_many_spans")
        if len(self.normalized_body) > MAX_BODY_LEN:
            raise ValueError("body_too_long")

    def __repr__(self) -> str:
        return (
            "DurableMemoryIntent("
            f"action={self.action!r}, scope={self.scope!r}, target={self.target!r}, "
            f"confidence={self.confidence!r}, spans={len(self.source_spans)}, "
            f"actor_id={self.actor_id!r}, session_id={self.session_id!r}, "
            f"reason_code={self.reason_code!r}, "
            f"request_exact_fact={self.request_exact_fact!r}, "
            f"body_len={len(self.normalized_body)})"
        )


def is_canonical_correlation_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if not value or len(value) > MAX_ID_LEN:
        return False
    return bool(_CANONICAL_ID_RE.match(value))


def bound_owner_text(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if len(text) > MAX_TEXT_LEN:
        return text[:MAX_TEXT_LEN]
    return text


def normalize_memory_body(raw: str) -> str:
    body = re.sub(r"\s+", " ", (raw or "").strip())
    if len(body) > MAX_BODY_LEN:
        body = body[:MAX_BODY_LEN].rstrip()
    return body


def _ephemeral(
    *,
    actor_id: str,
    session_id: str,
    reason_code: ReasonCode,
    confidence: float = 1.0,
    spans: Tuple[SourceSpan, ...] = (),
) -> DurableMemoryIntent:
    return DurableMemoryIntent(
        action="none",
        scope="ephemeral",
        target="inline",
        normalized_body="",
        confidence=max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, confidence)),
        source_spans=spans,
        actor_id=actor_id,
        session_id=session_id,
        reason_code=reason_code,
        request_exact_fact=False,
    )


def _command_span(match: re.Match[str]) -> SourceSpan:
    return SourceSpan(start=match.start(), end=match.end(), kind="command")


def _strip_wrapper(
    text: str,
    *,
    prefix: re.Pattern[str],
    suffix: re.Pattern[str],
    bare: re.Pattern[str],
    any_pat: re.Pattern[str],
) -> Tuple[Optional[str], Tuple[SourceSpan, ...], bool]:
    """Return (body_or_None_if_bare, spans, matched)."""
    bare_m = bare.match(text)
    if bare_m:
        return None, (_command_span(bare_m),), True
    prefix_m = prefix.match(text)
    if prefix_m:
        body = normalize_memory_body(prefix_m.group("body"))
        # Approximate command span as everything before body.
        cmd_end = prefix_m.start("body")
        return body, (SourceSpan(0, max(0, cmd_end), "command"),), True
    suffix_m = suffix.match(text)
    if suffix_m:
        body = normalize_memory_body(suffix_m.group("body"))
        return body, (SourceSpan(suffix_m.start(0) + len(body), suffix_m.end(), "command"),), True
    any_m = any_pat.search(text)
    if any_m:
        # Matched but could not cleanly strip — treat as bare anaphoric trigger.
        return None, (_command_span(any_m),), True
    return "", (), False


def _is_nonneg_int(value: object) -> bool:
    """Reject bool (subclass of int) and non-integers."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def resolve_anaphoric_target(
    recent: Sequence[RecentContextUtterance],
    *,
    actor_id: str,
    session_id: str,
    now_ms: int,
    restart_generation: int = 0,
    ttl_ms: int = DEFAULT_ANAPHORA_TTL_MS,
) -> Tuple[Optional[str], ReasonCode]:
    """Pure injected recent-context resolver. Never guesses across boundaries."""
    if not is_canonical_correlation_id(actor_id) or not is_canonical_correlation_id(
        session_id
    ):
        return None, "invalid_correlation"
    if not _is_nonneg_int(now_ms) or not _is_nonneg_int(ttl_ms):
        return None, "anaphoric_unresolved"
    if not _is_nonneg_int(restart_generation):
        return None, "anaphoric_unresolved"

    eligible: list[str] = []
    try:
        iterator = iter(recent)
    except TypeError:
        return None, "anaphoric_unresolved"

    for item in iterator:
        if not isinstance(item, RecentContextUtterance):
            return None, "anaphoric_unresolved"
        if not is_canonical_correlation_id(item.actor_id):
            return None, "anaphoric_unresolved"
        if not is_canonical_correlation_id(item.session_id):
            return None, "anaphoric_unresolved"
        if not isinstance(item.text, str) or len(item.text) > MAX_TEXT_LEN:
            return None, "anaphoric_unresolved"
        if not _is_nonneg_int(item.spoken_at_ms):
            return None, "anaphoric_unresolved"
        if not _is_nonneg_int(item.restart_generation):
            return None, "anaphoric_unresolved"
        if not isinstance(item.speaker_role, str) or not item.speaker_role:
            return None, "anaphoric_unresolved"

        if item.actor_id != actor_id:
            continue
        if item.session_id != session_id:
            continue
        if item.speaker_role != "owner":
            continue
        if item.restart_generation != restart_generation:
            continue
        if item.spoken_at_ms > now_ms:
            continue
        if now_ms - item.spoken_at_ms > ttl_ms:
            continue
        body = normalize_memory_body(item.text)
        if not body:
            continue
        if not _DECLARATIVE_OWNER.search(body):
            continue
        # Skip utterances that are themselves durable commands without inline body.
        if _SAVE_BARE.match(body) or _CORRECT_BARE.match(body) or _FORGET_BARE.match(body):
            continue
        eligible.append(body)

    if len(eligible) == 1:
        return eligible[0], "anaphoric_resolved"
    return None, "anaphoric_unresolved"


def parse_durable_memory_intent(
    text: object,
    *,
    actor_id: object,
    session_id: object,
    recent_context: Optional[Sequence[RecentContextUtterance]] = None,
    now_ms: int = 0,
    restart_generation: int = 0,
    anaphora_ttl_ms: int = DEFAULT_ANAPHORA_TTL_MS,
    inferred_candidate: bool = False,
) -> DurableMemoryIntent:
    """Parse one utterance into a DurableMemoryIntent (pure)."""
    actor = actor_id if isinstance(actor_id, str) else ""
    session = session_id if isinstance(session_id, str) else ""
    if not is_canonical_correlation_id(actor) or not is_canonical_correlation_id(session):
        return _ephemeral(
            actor_id=actor[:MAX_ID_LEN] if isinstance(actor_id, str) else "",
            session_id=session[:MAX_ID_LEN] if isinstance(session_id, str) else "",
            reason_code="invalid_correlation",
            confidence=1.0,
        )

    raw = bound_owner_text(text)
    if isinstance(text, str) and len(text.strip()) > MAX_TEXT_LEN:
        return _ephemeral(
            actor_id=actor,
            session_id=session,
            reason_code="text_too_long",
        )
    if not raw:
        return _ephemeral(
            actor_id=actor,
            session_id=session,
            reason_code="empty_input",
        )

    if _NEGATED.search(raw):
        return _ephemeral(
            actor_id=actor,
            session_id=session,
            reason_code="negated_request",
        )
    if _QUESTION_SAVE.search(raw) or (raw.endswith("?") and _SAVE_ANY.search(raw)):
        return _ephemeral(
            actor_id=actor,
            session_id=session,
            reason_code="question_about_saving",
        )
    if _QUOTED_CLAIM.search(raw) and not _SAVE_PREFIX.match(raw) and not _SAVE_SUFFIX.match(raw):
        # Quoted discussion of memory without an explicit owner wrapper intent.
        if not (_SAVE_ANY.search(raw) or _CORRECT_ANY.search(raw) or _FORGET_ANY.search(raw)):
            return _ephemeral(
                actor_id=actor,
                session_id=session,
                reason_code="quoted_assistant_claim",
            )
    if _MODEL_CLAIM.search(raw) and not (
        _SAVE_PREFIX.match(raw) or _SAVE_SUFFIX.match(raw) or _SAVE_BARE.match(raw)
    ):
        return _ephemeral(
            actor_id=actor,
            session_id=session,
            reason_code="model_claim_of_save",
        )
    if _OTHER_SYSTEM.search(raw):
        return _ephemeral(
            actor_id=actor,
            session_id=session,
            reason_code="other_system_instruction",
        )
    if _THIRD_PARTY.search(raw):
        return _ephemeral(
            actor_id=actor,
            session_id=session,
            reason_code="third_party_unsafe",
        )

    # Prefer forget / correct / save in that order when wrappers collide.
    for action, prefix, suffix, bare, any_pat, reason in (
        (
            "forget",
            _FORGET_PREFIX,
            _FORGET_SUFFIX,
            _FORGET_BARE,
            _FORGET_ANY,
            "forget_request",
        ),
        (
            "correct",
            _CORRECT_PREFIX,
            _CORRECT_SUFFIX,
            _CORRECT_BARE,
            _CORRECT_ANY,
            "correction_request",
        ),
        (
            "save",
            _SAVE_PREFIX,
            _SAVE_SUFFIX,
            _SAVE_BARE,
            _SAVE_ANY,
            "explicit_durable_consent",
        ),
    ):
        body, spans, matched = _strip_wrapper(
            raw, prefix=prefix, suffix=suffix, bare=bare, any_pat=any_pat
        )
        if not matched:
            continue
        if body:
            return DurableMemoryIntent(
                action=action,  # type: ignore[arg-type]
                scope="durable",
                target="inline",
                normalized_body=body,
                confidence=0.95,
                source_spans=spans,
                actor_id=actor,
                session_id=session,
                reason_code=reason,  # type: ignore[arg-type]
                request_exact_fact=False,
            )
        # Anaphoric / unresolved bare command
        if recent_context is not None and not isinstance(recent_context, (list, tuple)):
            resolved, a_reason = None, "anaphoric_unresolved"
        else:
            resolved, a_reason = resolve_anaphoric_target(
                recent_context or (),
                actor_id=actor,
                session_id=session,
                now_ms=now_ms,  # type: ignore[arg-type]
                restart_generation=restart_generation,  # type: ignore[arg-type]
                ttl_ms=anaphora_ttl_ms,  # type: ignore[arg-type]
            )
        if resolved:
            return DurableMemoryIntent(
                action=action,  # type: ignore[arg-type]
                scope="durable",
                target="anaphoric",
                normalized_body=resolved,
                confidence=0.85,
                source_spans=spans,
                actor_id=actor,
                session_id=session,
                reason_code="anaphoric_resolved",
                request_exact_fact=False,
            )
        return DurableMemoryIntent(
            action=action,  # type: ignore[arg-type]
            scope="durable",
            target="unresolved",
            normalized_body="",
            confidence=0.7,
            source_spans=spans,
            actor_id=actor,
            session_id=session,
            reason_code="anaphoric_unresolved",
            request_exact_fact=True,
        )

    # No explicit durable consent — ordinary / preference / inferred suggestion.
    if _PREFERENCE_CASUAL.search(raw):
        return _ephemeral(
            actor_id=actor,
            session_id=session,
            reason_code="preference_without_consent",
        )
    if inferred_candidate:
        return DurableMemoryIntent(
            action="none",
            scope="ephemeral",
            target="inline",
            normalized_body=normalize_memory_body(raw),
            confidence=0.4,
            source_spans=(),
            actor_id=actor,
            session_id=session,
            reason_code="inferred_suggestion_only",
            request_exact_fact=False,
        )
    return _ephemeral(
        actor_id=actor,
        session_id=session,
        reason_code="ordinary_conversation",
    )


def iter_reason_codes() -> Iterable[str]:
    return (
        "empty_input",
        "invalid_correlation",
        "text_too_long",
        "ordinary_conversation",
        "preference_without_consent",
        "inferred_suggestion_only",
        "explicit_durable_consent",
        "anaphoric_resolved",
        "anaphoric_unresolved",
        "negated_request",
        "question_about_saving",
        "quoted_assistant_claim",
        "model_claim_of_save",
        "other_system_instruction",
        "third_party_unsafe",
        "correction_request",
        "forget_request",
    )
