"""Bounded preparation contracts for Phase 3 browser research.

This module validates and retains research inputs only in memory until an
approved execution path consumes them. It performs no browser, HTTP, DNS,
network, provider, filesystem, logging, or persistence work.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from core.action_policy import ActorContext, validate_actor_context
from core.productivity.contracts import (
    ActionProposal,
    ActionTarget,
    PreviewField,
    ProductivityAction,
    TargetKind,
)

QUERY_MAX = 2000
DOMAIN_MAX = 253
DOMAINS_MAX = 16
DOMAINS_PREVIEW_MAX = 4078  # 16 * 253 + 15 * ", " — fits PreviewField (4096)
MAX_RESULTS_MAX = 20
MAX_RESULTS_DEFAULT = 10
RESEARCH_PROPOSAL_TTL = 900.0
RESEARCH_PREPARATION_LIMIT = 64

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$"
)
_IPv4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_NUMERIC_LABEL_RE = re.compile(r"^(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)$")
_IDNA_SEPARATORS = frozenset("\u3002\uff0e\uff61")


class ResearchPreparationError(ValueError):
    """Fixed preparation failure without user content or exception details."""


def _valid_text(value: object, maximum: int, *, allow_newline_tab: bool) -> bool:
    if not isinstance(value, str) or len(value) > maximum:
        return False
    for char in value:
        if allow_newline_tab and char in "\n\t":
            continue
        if ord(char) < 32 or ord(char) == 127:
            return False
        if unicodedata.category(char) == "Cf":
            return False
    return True


def _normalize_domain(domain: object) -> str:
    """Return a deterministic lowercase ASCII domain or raise.

    Rejects schemes, credentials, ports, paths, wildcards, IP literals,
    numeric alternate forms (decimal, hex, octal), single-label names,
    control characters, Unicode format characters, IDNA alternate
    separators, and IDNA mappings that change registrable spelling.
    International domains are IDNA-encoded to ASCII with a strict
    round-trip check; the operation is local and does not perform DNS
    resolution.
    """
    if not isinstance(domain, str):
        raise ResearchPreparationError("invalid research input")

    stripped = domain.strip()
    if not stripped or len(stripped) > DOMAIN_MAX:
        raise ResearchPreparationError("invalid research input")

    # Reject control characters and Unicode format characters.
    for char in stripped:
        code = ord(char)
        if code < 32 or code == 127:
            raise ResearchPreparationError("invalid research input")
        if unicodedata.category(char) == "Cf":
            raise ResearchPreparationError("invalid research input")

    # Reject scheme/credentials/port/path/wildcard characters and fragments.
    if any(ch in stripped for ch in (":", "@", "/", "*", "?", "#", "[")):
        raise ResearchPreparationError("invalid research input")

    # Reject IPv4 literals explicitly.
    if _IPv4_RE.fullmatch(stripped):
        raise ResearchPreparationError("invalid research input")

    lowered = stripped.lower()

    # Reject IDNA alternate separators (U+3002, U+FF0E, U+FF61) that
    # silently change separator semantics under IDNA-2003.
    if any(ch in lowered for ch in _IDNA_SEPARATORS):
        raise ResearchPreparationError("invalid research input")

    if lowered.isascii():
        # Pure ASCII domain: validate directly without IDNA transformation.
        ascii_domain = lowered
    else:
        # Non-ASCII: strict IDNA encode + round-trip check. Reject mappings
        # that change registrable spelling (e.g. faß.de → fass.de) or
        # separator semantics (e.g. example。com → example.com).
        try:
            ascii_domain = lowered.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            raise ResearchPreparationError("invalid research input") from None
        try:
            decoded = ascii_domain.encode("ascii").decode("idna")
        except (UnicodeError, ValueError):
            raise ResearchPreparationError("invalid research input") from None
        if decoded != lowered:
            raise ResearchPreparationError("invalid research input")

    if len(ascii_domain) > DOMAIN_MAX or not _DOMAIN_RE.fullmatch(ascii_domain):
        raise ResearchPreparationError("invalid research input")

    # Require at least two labels (one dot) — reject single-label
    # localhost-style names and numeric host representations.
    if "." not in ascii_domain:
        raise ResearchPreparationError("invalid research input")

    # Reject numeric alternate forms (decimal, hex, octal) where every
    # label is numeric — these are browser-style IP representations.
    labels = ascii_domain.split(".")
    if all(_NUMERIC_LABEL_RE.fullmatch(label) for label in labels):
        raise ResearchPreparationError("invalid research input")

    return ascii_domain


@dataclass(frozen=True, repr=False)
class PreparedResearchInput:
    """Server-private browser-research input with a content-free representation."""

    query: str
    domains: tuple[str, ...]
    max_results: int

    def __post_init__(self) -> None:
        if not isinstance(self.query, str):
            raise ResearchPreparationError("invalid research input")
        if not self.query or not _valid_text(
            self.query, QUERY_MAX, allow_newline_tab=False
        ):
            raise ResearchPreparationError("invalid research input")
        # Reject whitespace-only queries without rewriting valid content.
        if not self.query.strip():
            raise ResearchPreparationError("invalid research input")

        if not isinstance(self.domains, tuple):
            raise ResearchPreparationError("invalid research input")
        if len(self.domains) > DOMAINS_MAX:
            raise ResearchPreparationError("invalid research input")

        # Canonicalize every domain before immutable construction so that
        # noncanonical direct domains (e.g. Example.COM, münchen.de) are
        # never retained unchanged.
        canonical_domains: list[str] = []
        seen: set[str] = set()
        for domain in self.domains:
            normalized = _normalize_domain(domain)
            if normalized in seen:
                raise ResearchPreparationError("invalid research input")
            seen.add(normalized)
            canonical_domains.append(normalized)
        object.__setattr__(self, "domains", tuple(canonical_domains))

        if isinstance(self.max_results, bool) or not isinstance(self.max_results, int):
            raise ResearchPreparationError("invalid research input")
        if not 1 <= self.max_results <= MAX_RESULTS_MAX:
            raise ResearchPreparationError("invalid research input")

    def __repr__(self) -> str:
        return "PreparedResearchInput(...)"


@dataclass(frozen=True, repr=False)
class ResearchPreparation:
    """A public research proposal paired with its server-private input."""

    proposal: ActionProposal
    input: PreparedResearchInput

    def __repr__(self) -> str:
        return "ResearchPreparation(...)"


class ResearchProposalFactory:
    """Create canonical browser-research proposals with injected time and IDs."""

    def __init__(
        self,
        clock: Callable[[], float],
        proposal_id_factory: Callable[[], str],
        *,
        ttl_seconds: float = RESEARCH_PROPOSAL_TTL,
    ) -> None:
        if not callable(clock) or not callable(proposal_id_factory):
            raise TypeError("clock and proposal ID factory must be callable")
        # Convert and validate inside one guard that replaces every failure —
        # including exceptions raised by a hostile __float__ on an int/float
        # subclass — with a fixed message and no chained cause.
        try:
            if (
                not isinstance(ttl_seconds, (int, float))
                or isinstance(ttl_seconds, bool)
            ):
                raise ValueError
            ttl_f = float(ttl_seconds)
            if not math.isfinite(ttl_f) or not 1.0 <= ttl_f <= 900.0:
                raise ValueError
        except Exception:
            raise ValueError("invalid proposal lifetime") from None
        self._clock = clock
        self._proposal_id_factory = proposal_id_factory
        self._ttl_seconds = ttl_f

    def _now_and_id(self) -> tuple[float, str]:
        try:
            now = self._clock()
            proposal_id = self._proposal_id_factory()
        except Exception:
            raise ResearchPreparationError("research preparation failed") from None

        # Convert and validate inside one guard that replaces every failure —
        # including exceptions raised by a hostile __float__ on an int/float
        # subclass — with a fixed message and no chained cause.
        try:
            if (
                isinstance(now, bool)
                or not isinstance(now, (int, float))
                or not isinstance(proposal_id, str)
                or not _IDENTIFIER_RE.fullmatch(proposal_id)
            ):
                raise ValueError
            now_f = float(now)
            if not math.isfinite(now_f):
                raise ValueError
        except Exception:
            raise ResearchPreparationError("research preparation failed") from None
        return now_f, proposal_id

    @staticmethod
    def _normalize_domains(domains: object) -> tuple[str, ...]:
        """Return a normalized domain tuple preserving caller order.

        Accepts None, a single domain string, or a sequence of domain strings.
        Duplicates are preserved so that higher-level validation can reject them.
        """
        if domains is None:
            return ()
        if isinstance(domains, str):
            return (_normalize_domain(domains),)
        if isinstance(domains, (list, tuple)):
            return tuple(_normalize_domain(d) for d in domains)
        raise ResearchPreparationError("invalid research input")

    def prepare(
        self,
        actor: ActorContext,
        query: object,
        domains: object = None,
        max_results: object = MAX_RESULTS_DEFAULT,
    ) -> ResearchPreparation:
        valid_actor, _ = validate_actor_context(actor)
        if not valid_actor:
            raise ResearchPreparationError("research preparation failed")

        now, proposal_id = self._now_and_id()

        try:
            domain_tuple = self._normalize_domains(domains)
            research_input = PreparedResearchInput(query, domain_tuple, max_results)
        except ResearchPreparationError:
            raise
        except Exception:
            raise ResearchPreparationError("research preparation failed") from None

        targets = tuple(
            ActionTarget(TargetKind.WEB_DOMAIN, domain)
            for domain in research_input.domains
        )

        try:
            preview_fields: list[PreviewField] = []
            # Preview the exact accepted query without truncation.
            preview_fields.append(
                PreviewField(
                    "query",
                    "Query",
                    research_input.query,
                    truncated=False,
                )
            )
            if research_input.domains:
                domains_preview = ", ".join(research_input.domains)
                if len(domains_preview) > DOMAINS_PREVIEW_MAX:
                    raise ResearchPreparationError("invalid research input")
                preview_fields.append(
                    PreviewField(
                        "domains",
                        "Allowed domains",
                        domains_preview,
                        truncated=False,
                    )
                )
            preview_fields.append(
                PreviewField(
                    "max_results",
                    "Maximum results",
                    str(research_input.max_results),
                    truncated=False,
                )
            )

            proposal = ActionProposal(
                proposal_id=proposal_id,
                action=ProductivityAction.BROWSER_RESEARCH,
                actor=actor,
                targets=targets,
                preview_fields=tuple(preview_fields),
                created_at=float(now),
                expires_at=float(now) + self._ttl_seconds,
            )
        except ResearchPreparationError:
            raise
        except Exception:
            raise ResearchPreparationError("research preparation failed") from None
        return ResearchPreparation(proposal, research_input)


class ResearchPreparationRegistry:
    """Bounded actor/session-scoped in-memory registry for prepared inputs.

    Holds at most 64 entries keyed by exact (actor_id, session_id, proposal_id).
    No persistence is performed.
    """

    def __init__(self, *, limit: int = RESEARCH_PREPARATION_LIMIT) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 64
        ):
            raise ValueError("invalid registry limit")
        self._limit = limit
        self._items: dict[tuple[str, str, str], PreparedResearchInput] = {}

    @staticmethod
    def _key(actor: ActorContext, proposal_id: str) -> tuple[str, str, str]:
        valid_actor, _ = validate_actor_context(actor)
        if (
            not valid_actor
            or not isinstance(proposal_id, str)
            or not _IDENTIFIER_RE.fullmatch(proposal_id)
        ):
            raise ResearchPreparationError("research registry operation failed")
        return actor.actor_id, actor.session_id, proposal_id

    def put(
        self, actor: ActorContext, proposal_id: str, item: PreparedResearchInput
    ) -> None:
        if not isinstance(item, PreparedResearchInput):
            raise ResearchPreparationError("research registry item rejected")
        key = self._key(actor, proposal_id)
        if key not in self._items and len(self._items) >= self._limit:
            raise ResearchPreparationError("research registry is full")
        self._items[key] = item

    def get(self, actor: ActorContext, proposal_id: str) -> PreparedResearchInput | None:
        return self._items.get(self._key(actor, proposal_id))

    def remove(self, actor: ActorContext, proposal_id: str) -> None:
        self._items.pop(self._key(actor, proposal_id), None)

    def clear_session(self, actor_id: str, session_id: str) -> None:
        for key in tuple(self._items):
            if key[0] == actor_id and key[1] == session_id:
                self._items.pop(key, None)
