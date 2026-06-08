"""Allow/deny filtering for regex-based memory extraction (reduce graph junk)."""

from __future__ import annotations

import re
import string
from typing import Iterable, Optional, Set

# Common English stopwords + fragments that should never become graph nodes
STOPWORDS: Set[str] = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "if",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "at",
    "by",
    "from",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "it",
    "this",
    "that",
    "these",
    "those",
    "i",
    "you",
    "we",
    "they",
    "me",
    "my",
    "your",
    "our",
    "their",
    "not",
    "no",
    "yes",
    "so",
    "just",
    "like",
    "about",
    "into",
    "over",
    "after",
    "before",
    "up",
    "down",
    "out",
    "off",
    "also",
    "very",
    "can",
    "could",
    "would",
    "should",
    "will",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "get",
    "got",
    "go",
    "going",
    "went",
    "come",
    "came",
    "use",
    "using",
    "used",
    "labeled",
    "called",
    "thing",
    "things",
    "stuff",
    "something",
    "anything",
    "nothing",
    "someone",
    "anyone",
}

# Labels / UI noise from transcripts
JUNK_TOKENS: Set[str] = {
    "unknown",
    "null",
    "undefined",
    "n/a",
    "na",
    "none",
    "error",
    "okay",
    "ok",
    "uh",
    "um",
    "hmm",
}


def normalize_token(name: str) -> str:
    return name.strip().strip(string.punctuation).lower()


def is_allowed_entity_name(name: str, *, min_len: int = 2, max_len: int = 80) -> bool:
    raw = (name or "").strip()
    if not raw:
        return False
    if len(raw) < min_len or len(raw) > max_len:
        return False
    key = normalize_token(raw)
    if not key:
        return False
    if key in STOPWORDS or key in JUNK_TOKENS:
        return False
    if key.isdigit():
        return False
    if re.fullmatch(r"[._\-/]+", raw):
        return False
    return True


def filter_entity_triples(
    entities: Iterable[tuple],
) -> list[tuple[str, str, str]]:
    """entities: (node_type, name, content)"""
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node_type, name, content in entities:
        if not is_allowed_entity_name(name):
            continue
        k = (node_type, normalize_token(name))
        if k in seen:
            continue
        seen.add(k)
        out.append((node_type, name.strip(), content or ""))
    return out


def filter_preference_subject(subject: str) -> Optional[str]:
    s = (subject or "").strip()
    if not s:
        return None
    parts = [p for p in re.split(r"[\s,;]+", s) if p]
    for p in parts:
        if is_allowed_entity_name(p, min_len=3):
            return p.strip()
    return None
