"""Memory candidate quality gate — classify without auto-accept."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

EXTRACTION_POLICY_VERSION = "v2"

QUALITY_KEEP = "keep"
QUALITY_WEAK = "weak"
QUALITY_REJECT = "reject_from_queue"

_FILLER_EXACT = frozenset(
    {
        "ok",
        "okay",
        "k",
        "kk",
        "yes",
        "yeah",
        "yep",
        "no",
        "nope",
        "sure",
        "got it",
        "noted",
        "thanks",
        "thank you",
        "hi",
        "hello",
        "hey",
        "bye",
        "goodbye",
        "exit",
        "quit",
        "continue",
        "stop",
        "do it",
        "go on",
        "never mind",
    }
)

_COMMAND_PATTERNS = (
    re.compile(r"^(exit|quit|bye|goodbye|stop|continue|do it|go on)\b", re.I),
    re.compile(r"^(open|close|run|start|stop)\s+\w+", re.I),
    re.compile(r"^explain\b", re.I),
)

_QUESTION_FORMS = (
    re.compile(r"^do\s+(?:you|u)\s+(?:know|remember)\b", re.I),
    re.compile(r"^who\s+is\b", re.I),
    re.compile(r"^what(?:'s|\s+is|\s+are)\b", re.I),
    re.compile(r"^whats\b", re.I),
    re.compile(r"^what\s+(?:does|do|did)\b", re.I),
    re.compile(r"^where\s+(?:does|do|did|is|are|am)\b", re.I),
    re.compile(r"^when\s+(?:does|do|did|is|are|was|were)\b", re.I),
    re.compile(r"^how\s+(?:does|do|did|is|are)\b", re.I),
    re.compile(r"^can\s+(?:you|u)\b", re.I),
    re.compile(r"^could\s+(?:you|u)\b", re.I),
    re.compile(r"^did\s+(?:you|u)\b", re.I),
)

_VAGUE_PATTERNS = (
    re.compile(r"^(this|that|it)\s+(is|was|seems)\s+(good|bad|fine|ok|okay)\b", re.I),
    re.compile(r"^something\s+(happened|went)", re.I),
    re.compile(r"^(maybe|perhaps|i think)\s+(something|that)\b", re.I),
    re.compile(r"^that\s+thing\b", re.I),
)

_ASSISTANT_MARKERS = re.compile(
    r"^(i am hikari|i'm hikari|as an ai|as your assistant)\b", re.I
)

_DURABLE_PATTERNS = (
    re.compile(r"\bremember\s+(?:this|that)\b", re.I),
    re.compile(r"\bmy\s+name\s+is\b", re.I),
    re.compile(r"\b(?:you\s+can\s+|u\s+can\s+)?call\s+me\s+[A-Za-z]", re.I),
    re.compile(r"\bmy\s+(dad|father|mom|mother|sister|brother|gf|girlfriend|partner|wife|husband)\b", re.I),
    re.compile(r"\bi\s+(live|work|study)\s+(?:in|at)\b", re.I),
    re.compile(r"\b(?:i\s+was|1st|first)\s+born\s+(?:in|at)\b", re.I),
    re.compile(r"\bmy\s+birth\s*place\s+is\b", re.I),
    re.compile(
        r"\bi\s+(?:am\s+)?(?:doing|pursuing|getting|completing)\s+my\s+"
        r"(?:bachelor|master|undergraduate|graduate)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:right\s+now|currently|at\s+the\s+moment)\b.+\b(?:i'?m|i am)\s+in\b", re.I
    ),
    re.compile(r"\b(?:i'?m|i am)\s+currently\s+in\b", re.I),
    re.compile(r"\b(?:i'?m|i am)\s+in\s+[A-Za-z]", re.I),
    re.compile(r"\bi\s+prefer\b", re.I),
    re.compile(r"\bi\s+don'?t\s+like\b", re.I),
    re.compile(r"\bmy\s+fav(?:ou?rite)?\s+[a-z][a-z\s-]{0,40}\s+is\b", re.I),
    re.compile(r"\bfor\s+hikari\b.+\b(decided|should|use|prefer|will)\b", re.I),
    re.compile(r"\bhikari\b.+\b(decided|should|use|prefer)\b", re.I),
    re.compile(r"\b(?:return\s+)?flights?\b", re.I),
    re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b", re.I),
    re.compile(r"\bis\s+my\s+(sister|brother|dad|mom|gf|girlfriend|partner)\b", re.I),
    re.compile(
        r"\b(?:graduat(?:e|ing|ion)|rising\s+senior|senior\s+year)\b",
        re.I,
    ),
)

# Case-sensitive capitalized tokens only (no re.I - avoids matching every English word).
_CAPITALIZED_WORD = re.compile(r"\b[A-Z][a-z]{2,}\b")

# Sentence-initial / function words that are capitalized in English but not entities.
_STOP_CAPITALIZED = frozenset(
    {
        "My",
        "The",
        "This",
        "That",
        "What",
        "When",
        "Where",
        "Why",
        "How",
        "Can",
        "Could",
        "Do",
        "Did",
        "Are",
        "Was",
        "Were",
        "Have",
        "Has",
        "Had",
        "Will",
        "Would",
        "Should",
        "May",
        "Might",
        "Must",
        "Not",
        "But",
        "And",
        "For",
        "With",
        "From",
        "Into",
        "Your",
        "Our",
        "His",
        "Her",
        "Its",
    }
)


@dataclass(frozen=True)
class QualityVerdict:
    label: str
    reasons: List[str]

    def to_metadata(self) -> dict:
        return {
            "quality_label": self.label,
            "quality_reasons": self.reasons,
            "extraction_policy_version": EXTRACTION_POLICY_VERSION,
        }


def classify_candidate(
    statement: str,
    *,
    candidate_type: str = "fact",
    is_user: bool = True,
    explicit_remember: bool = False,
) -> QualityVerdict:
    """Classify extracted text; reject_from_queue items should not enter review."""
    text = (statement or "").strip()
    low = text.lower().rstrip(".!?")
    reasons: List[str] = []

    if not is_user:
        return QualityVerdict(QUALITY_REJECT, ["assistant_segment"])

    if not text or len(low) < 4:
        return QualityVerdict(QUALITY_REJECT, ["too_short"])

    if explicit_remember:
        return QualityVerdict(QUALITY_KEEP, ["explicit_remember"])

    if low in _FILLER_EXACT or len(low.split()) <= 2 and low in _FILLER_EXACT:
        return QualityVerdict(QUALITY_REJECT, ["filler"])

    if text.endswith("?"):
        return QualityVerdict(QUALITY_REJECT, ["question"])

    if _is_question_form(low):
        return QualityVerdict(QUALITY_REJECT, ["question"])

    for pat in _COMMAND_PATTERNS:
        if pat.search(low):
            return QualityVerdict(QUALITY_REJECT, ["command_only"])

    if _ASSISTANT_MARKERS.search(low):
        return QualityVerdict(QUALITY_REJECT, ["assistant_identity"])

    for pat in _VAGUE_PATTERNS:
        if pat.search(low):
            return QualityVerdict(QUALITY_WEAK, ["vague_statement"])

    if len(low.split()) < 4 and not has_entity_hint(text):
        return QualityVerdict(QUALITY_REJECT, ["short_without_entity"])

    durable_hits = sum(1 for p in _DURABLE_PATTERNS if p.search(text))
    if durable_hits >= 1:
        reason = ["durable_pattern"]
        if candidate_type in (
            "identity",
            "relation",
            "education",
            "preference",
            "location",
            "current_location",
            "travel",
            "decision",
            "plan",
            "event",
        ):
            reason.append(f"type:{candidate_type}")
        return QualityVerdict(QUALITY_KEEP, reason)

    if _looks_declarative_fact(low):
        if has_entity_hint(text):
            return QualityVerdict(QUALITY_KEEP, ["declarative_with_entity"])
        return QualityVerdict(QUALITY_WEAK, ["declarative_low_entity"])

    return QualityVerdict(QUALITY_REJECT, ["no_durable_signal"])


def _is_question_form(low: str) -> bool:
    """Informal questions without a trailing '?', checked before durable patterns."""
    return any(pat.search(low) for pat in _QUESTION_FORMS)


def has_entity_hint(text: str) -> bool:
    """True when statement contains a proper name / place token (case-sensitive)."""
    if not text:
        return False
    if re.search(r"\bHIKARI\b", text):
        return True
    for match in _CAPITALIZED_WORD.finditer(text):
        if match.group(0) not in _STOP_CAPITALIZED:
            return True
    return False


def _looks_declarative_fact(low: str) -> bool:
    return bool(
        re.search(
            r"\b(is|are|am|live|lives|study|studies|work|works|prefer|remember)\b",
            low,
        )
    )


def apply_quality_gate(
    statement: str,
    *,
    candidate_type: str = "fact",
    is_user: bool = True,
    explicit_remember: bool = False,
) -> Tuple[Optional[str], QualityVerdict]:
    """Return (statement_or_none, verdict). None statement means skip candidate creation."""
    verdict = classify_candidate(
        statement,
        candidate_type=candidate_type,
        is_user=is_user,
        explicit_remember=explicit_remember,
    )
    if verdict.label == QUALITY_REJECT:
        return None, verdict
    return statement.strip(), verdict


def filter_pending_for_review(candidates: list) -> list:
    """Drop reject_from_queue; keep weak and keep for human review."""
    out = []
    for cand in candidates:
        label = (cand.metadata or {}).get("quality_label", QUALITY_KEEP)
        if label == QUALITY_REJECT:
            continue
        out.append(cand)
    return out
