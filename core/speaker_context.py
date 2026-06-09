"""Track current speaker vs household primary user (avoid identity mixing)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Optional

_UNSET_PRIMARY = object()

_NAME_TOKEN = r"[A-Za-z][a-z]*(?:\s+[A-Za-z][a-z]*)*"

_BLOCKED_NAME_SUFFIX = frozenset(
    {
        "talking",
        "here",
        "again",
        "back",
        "just",
        "testing",
        "visiting",
        "calling",
        "speaking",
    }
)

_OWNER_RELATION_RE = re.compile(
    r"\b(?:i\s+am|i'm)\s+(?:your\s+)?(?:owner'?s?|household\s+owner'?s?)\s+"
    r"(sister|brother|mother|father|son|daughter|wife|husband|parent|cousin)\b",
    re.I,
)

_GUEST_VISIT_RECALL = re.compile(
    r"\b(?:did|has|have)\s+(?:my\s+)?(?:(sister|brother|mother|father|mom|dad|"
    r"wife|husband|partner|guest)\s+)?(?:talk|spoke|spoken|chat)\w*\s+"
    r"(?:to\s+)?(?:you|u)\b",
    re.I,
)
_GUEST_WHO_VISITED = re.compile(
    r"\b(?:who\s+(?:talked|spoke|visited|was\s+here|came\s+by)|"
    r"did\s+anyone\s+(?:talk|speak|visit)|any\s+guests?)\b",
    re.I,
)

_SELF_INTRO_RE = re.compile(
    rf"(?:^|\.\s*|\b)(?:i am|i'm|my name is)\s+({_NAME_TOKEN})\b",
    re.IGNORECASE,
)
_SELF_RELATION_RE = re.compile(
    rf"(?:\bi am\b|\bi'm\b)\s+({_NAME_TOKEN})[,\s]+"
    rf"({_NAME_TOKEN})'s\s+"
    r"(sister|brother|mother|father|son|daughter|wife|husband|parent|cousin)",
    re.IGNORECASE,
)
_CALL_ME_NAME_RE = re.compile(
    rf"^call\s+me\s+({_NAME_TOKEN})\b",
    re.IGNORECASE,
)

_SESSION_SPEAKER_PATTERNS = (
    re.compile(
        rf"\b(?:i\s+am|i'm)\s+({_NAME_TOKEN})\s+talking\s+to\s+you(?:\s+now)?\b",
        re.I,
    ),
    re.compile(rf"\bthis\s+is\s+({_NAME_TOKEN})\b", re.I),
    re.compile(rf"\b({_NAME_TOKEN})\s+here\b", re.I),
)


@dataclass
class GuestVisitRecord:
    guest_name: str
    relation: Optional[str] = None

_SPEAKER_RESET_CLEAR = (
    re.compile(r"\bi'?m\s+just\s+testing\b", re.I),
    re.compile(r"\bjust\s+testing\b", re.I),
)
_SPEAKER_RESET_PRIMARY = (
    re.compile(r"\bback\s+to\s+(?:owner|(?:the\s+)?primary(?:\s+user)?)\b", re.I),
    re.compile(
        rf"\b(?:this\s+is|it'?s)\s+({_NAME_TOKEN})\s+again\b",
        re.I,
    ),
)

_GUEST_PERSONAL_QUESTION = re.compile(
    r"\b(?:does|do|did|can|will|would|is|are|was|were)\s+my\s+"
    r"(?:brother|sister|mom|mother|dad|father|parents?|wife|husband|partner)\b",
    re.I,
)

# Affectionate nicknames — not legal names for profile overwrite
CASUAL_NAMES = frozenset(
    {
        "baby",
        "babe",
        "bro",
        "dude",
        "man",
        "sweetie",
        "honey",
        "dear",
        "boss",
        "king",
        "queen",
    }
)

_NON_NAME_SELF_INTRO_PREFIXES = frozenset(
    {
        "in",
        "at",
        "currently",
        "right",
        "here",
        "feeling",
        "going",
        "staying",
        "doing",
        "studying",
        "working",
        "trying",
        "having",
        "getting",
        "looking",
        "living",
        "visiting",
        "traveling",
        "travelling",
    }
)

_speaker_ctx: Optional["SpeakerContext"] = None


def is_temporary_speaker_intro(text: str) -> bool:
    """True when the utterance switches session speaker, not owner identity."""
    if not (text or "").strip():
        return False
    for pat in _SESSION_SPEAKER_PATTERNS:
        if pat.search(text):
            return True
    return False


def is_speaker_context_reset(text: str) -> bool:
    """True when the user clears guest speaker context or returns to owner."""
    if not (text or "").strip():
        return False
    for pat in _SPEAKER_RESET_CLEAR:
        if pat.search(text):
            return True
    for pat in _SPEAKER_RESET_PRIMARY:
        if pat.search(text):
            return True
    return False


def is_guest_visit_recall_question(text: str) -> bool:
    """Owner asking whether a guest spoke with HIKARI recently."""
    if not (text or "").strip():
        return False
    q = text.strip()
    if _GUEST_WHO_VISITED.search(q):
        return True
    return bool(_GUEST_VISIT_RECALL.search(q))


def extract_guest_visit_relation_asked(text: str) -> Optional[str]:
    m = _GUEST_VISIT_RECALL.search(text or "")
    if not m or not m.group(1):
        return None
    rel = m.group(1).lower()
    if rel in ("mom", "dad"):
        return "mother" if rel == "mom" else "father"
    return rel


def is_guest_scoped_personal_question(text: str) -> bool:
    """Personal/family questions that must not use the household owner's memory."""
    if not (text or "").strip():
        return False
    q = text.strip().lower().rstrip("?!.")
    if _GUEST_PERSONAL_QUESTION.search(q):
        return True
    if re.search(r"\bdo\s+(?:you|u)\s+know\s+my\s+", q):
        return any(
            rel in q
            for rel in (
                "brother",
                "sister",
                "mom",
                "mother",
                "dad",
                "father",
                "parents",
                "parent",
            )
        )
    if re.search(r"\bwho\s+is\s+my\s+", q):
        return any(
            rel in q
            for rel in ("brother", "sister", "mom", "mother", "dad", "father")
        )
    return False


class SpeakerContext:
    def __init__(self, primary_user: Any = _UNSET_PRIMARY):
        if primary_user is _UNSET_PRIMARY:
            env_primary = (os.getenv("HIKARI_PRIMARY_USER") or "").strip()
            resolved = env_primary or None
        elif primary_user:
            resolved = str(primary_user).strip()
        else:
            resolved = None
        self.primary_user: Optional[str] = resolved
        if self.primary_user:
            self.primary_user = self.primary_user.strip().title()
        self.current_speaker: Optional[str] = None
        self.last_family_relation: Optional[str] = None
        self.last_contact_kind: Optional[str] = None  # family | partner
        self.last_was_session_intro: bool = False
        self.session_speaker_mode: bool = False
        self._speaker_reset_pending: bool = False
        self._active_guest_relation: Optional[str] = None
        self.last_guest_visit: Optional[GuestVisitRecord] = None

    def consume_speaker_reset(self) -> bool:
        """True once after a phrase reset cleared or restored the active speaker."""
        pending = self._speaker_reset_pending
        self._speaker_reset_pending = False
        return pending

    def note_contact_discussed(self, kind: str, slot: Optional[str] = None) -> None:
        k = (kind or "").strip().lower()
        if k in ("family", "partner"):
            self.last_contact_kind = k
        if k == "family" and slot:
            self.last_family_relation = slot.lower()

    def set_primary_user(self, name: str) -> None:
        cleaned = (name or "").strip().title()
        if cleaned and cleaned.lower() not in ("user", "unknown"):
            self.primary_user = cleaned

    def _clear_contact_context(self) -> None:
        """Drop family/partner hints so a new session cannot inherit stale slots."""
        self.last_family_relation = None
        self.last_contact_kind = None

    def clear_current_speaker(self) -> None:
        self.current_speaker = None
        self.last_was_session_intro = False
        self.session_speaker_mode = False
        self._active_guest_relation = None
        self._clear_contact_context()

    def reset_to_primary(self) -> None:
        self.finalize_guest_visit()
        if self.primary_user:
            self.current_speaker = self.primary_user
        else:
            self.current_speaker = None
        self.last_was_session_intro = False
        self.session_speaker_mode = False
        self._clear_contact_context()

    def _normalize_name(self, name: str) -> Optional[str]:
        parts = [p for p in (name or "").strip().split() if p]
        while parts and parts[-1].lower() in _BLOCKED_NAME_SUFFIX:
            parts.pop()
        if not parts:
            return None
        n = " ".join(piece.title() for piece in parts)
        if len(n) < 2 or n.lower() in CASUAL_NAMES:
            return None
        if parts[0].lower() in _NON_NAME_SELF_INTRO_PREFIXES:
            return None
        return n

    def note_guest_relation_from_input(self, text: str) -> None:
        """Capture guest-only relation hints (not stored in owner Brain v2)."""
        if not self.is_guest_speaker():
            return
        m = _OWNER_RELATION_RE.search(text or "")
        if m:
            self._active_guest_relation = m.group(1).lower()

    def finalize_guest_visit(self) -> None:
        """Remember the last guest for owner recall after returning from guest mode."""
        if (
            self.current_speaker
            and self.primary_user
            and self.current_speaker.lower() != self.primary_user.lower()
        ):
            self.last_guest_visit = GuestVisitRecord(
                guest_name=self.current_speaker,
                relation=self._active_guest_relation,
            )
        self._active_guest_relation = None

    def _apply_reset(self, text: str) -> bool:
        if not text:
            return False
        for pat in _SPEAKER_RESET_CLEAR:
            if pat.search(text):
                self.finalize_guest_visit()
                self.clear_current_speaker()
                self._speaker_reset_pending = True
                return True
        for pat in _SPEAKER_RESET_PRIMARY:
            m = pat.search(text)
            if not m:
                continue
            if m.lastindex and m.group(1):
                person = self._normalize_name(m.group(1))
                if person and self.primary_user and person.lower() == self.primary_user.lower():
                    self.reset_to_primary()
                    self._speaker_reset_pending = True
                    return True
            self.reset_to_primary()
            self._speaker_reset_pending = True
            return True
        return False

    def update_from_input(self, text: str) -> Optional[str]:
        """Parse speaker from intro; returns detected name if any."""
        self.last_was_session_intro = False
        if not text:
            return None

        if self._apply_reset(text):
            return None

        for pat in _SESSION_SPEAKER_PATTERNS:
            m = pat.search(text)
            if m:
                person = self._normalize_name(m.group(1))
                if person:
                    self._clear_contact_context()
                    self.current_speaker = person
                    self.last_was_session_intro = True
                    self.session_speaker_mode = True
                    return person

        for match in _SELF_RELATION_RE.finditer(text):
            person = self._normalize_name(match.group(1))
            if person:
                self.current_speaker = person
                return person

        match = _CALL_ME_NAME_RE.match(text.strip())
        if match:
            person = self._normalize_name(match.group(1))
            if person:
                self.current_speaker = person
                return person

        for match in _SELF_INTRO_RE.finditer(text):
            person = self._normalize_name(match.group(1))
            if person:
                self.current_speaker = person
                return person

        return None

    def is_guest_speaker(self) -> bool:
        """True when the active speaker must not receive household owner personal memory."""
        if not self.current_speaker:
            return False
        if self.primary_user:
            return self.current_speaker.lower() != self.primary_user.lower()
        return self.session_speaker_mode

    def should_skip_owner_identity_learning(self, text: str) -> bool:
        """Guest/session intros must not overwrite owner profile or durable identity."""
        if self.is_guest_speaker():
            return True
        if self.last_was_session_intro:
            return True
        if is_temporary_speaker_intro(text):
            return True
        if is_speaker_context_reset(text):
            return True
        return False

    def prompt_context(self, *, brain_v2_authority: bool = False) -> str:
        """Session-visible speaker lines for prompts (no legacy profile names when authority on)."""
        lines = []
        if self.current_speaker:
            if brain_v2_authority:
                role = "guest speaker" if self.is_guest_speaker() else "session speaker"
            else:
                role = "guest speaker" if self.is_guest_speaker() else "primary user"
            lines.append(f"Current speaker: {self.current_speaker} ({role}).")
        if brain_v2_authority:
            if self.is_guest_speaker():
                lines.append(
                    "A guest or temporary session speaker is active. Use only reviewed Brain v2 "
                    "memories scoped to this speaker; do not apply the household owner's personal "
                    "memories until the verified owner session is restored."
                )
            return " ".join(lines)
        if self.primary_user and self.is_guest_speaker():
            lines.append(
                f"The household primary user is {self.primary_user}. "
                "Do not address the guest by the primary user's name or assume they share "
                "the primary user's personal details unless relevant to the question."
            )
        return " ".join(lines)

    def note_family_relation(self, relation: Optional[str]) -> None:
        if relation:
            self.last_family_relation = relation.lower()
            self.last_contact_kind = "family"


def get_speaker_context() -> SpeakerContext:
    global _speaker_ctx
    if _speaker_ctx is None:
        _speaker_ctx = SpeakerContext()
    return _speaker_ctx
