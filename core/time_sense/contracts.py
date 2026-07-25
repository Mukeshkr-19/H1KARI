"""Immutable typed contracts for the Time Sense package.

All types here are frozen dataclasses or string enums. No I/O, timers,
threads, subprocesses, network, notification, persistence, external tool
calls, or access to Brain v2 personal facts is present. Every timestamp
boundary is timezone-aware by contract; naive datetimes are rejected.

These contracts describe caller-supplied observations only. They never
mutate task or job objects and never schedule, deliver, retry, or cancel
anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Shared limits
# ---------------------------------------------------------------------------

MAX_PHRASE_CHARS = 256
MAX_IDENTIFIER_LENGTH = 128
MAX_IDENTIFIER_CHARS_MSG = f"{MAX_IDENTIFIER_LENGTH} characters"
MAX_EVIDENCE_CODE_CHARS = 64
MAX_TASK_KIND_CHARS = 64
MAX_PAYLOAD_HINT_CHARS = 160
MAX_TIMEZONE_NAME_CHARS = 64
DEFAULT_MAX_FUTURE_HORIZON = timedelta(days=366)
MIN_FUTURE_HORIZON = timedelta(minutes=1)
MAX_SNAPSHOT_ITEMS = 50
MAX_SUMMARY_CHARS = 4000
MINUTES_PER_DAY = 1440


def _require_aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a timezone-aware datetime")
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _bounded_text(value: object, *, limit: int, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    cleaned = "".join(
        ch for ch in value if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    ).strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _evidence_code(value: object, *, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    code = value.strip()
    if not code:
        return None
    if len(code) > MAX_EVIDENCE_CODE_CHARS:
        raise ValueError(f"{name} exceeds {MAX_EVIDENCE_CODE_CHARS} characters")
    if not all(ch.isalnum() or ch in (".", "_", "-") for ch in code):
        raise ValueError(f"{name} contains invalid characters")
    return code


def _validate_identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"{name} exceeds {MAX_IDENTIFIER_CHARS_MSG}")


def _validate_kind(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > MAX_TASK_KIND_CHARS:
        raise ValueError(f"{name} exceeds {MAX_TASK_KIND_CHARS} characters")


def _validate_status(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > MAX_TASK_KIND_CHARS:
        raise ValueError(f"{name} exceeds {MAX_TASK_KIND_CHARS} characters")


# ---------------------------------------------------------------------------
# Conversational timing
# ---------------------------------------------------------------------------


class TemporalPrecision(StrEnum):
    """How precisely a conversational time phrase resolves.

    - ``INSTANT``: a single resolved timestamp (e.g. "in 20 minutes").
    - ``WINDOW``: a bounded conversational time window with a start and end
      (e.g. "this afternoon", "after lunch"). No single instant is claimed.
    - ``VAGUE``: too imprecise to resolve without clarification (e.g. "soon").
      ``requires_clarification`` is True and ``window`` may hold a wide
      fallback window, but no instant is invented.
    """

    INSTANT = "instant"
    WINDOW = "window"
    VAGUE = "vague"


@dataclass(frozen=True)
class TimeReference:
    """An injected reference clock reading plus an original phrase.

    ``reference_at`` is the caller-supplied "now"; it must be timezone-aware.
    No component of Time Sense ever calls ``datetime.now`` internally.
    """

    reference_at: datetime
    phrase: str

    def __post_init__(self) -> None:
        _require_aware(self.reference_at, "reference_at")
        if self.phrase is not None and not isinstance(self.phrase, str):
            raise ValueError("phrase must be a string")
        phrase = (self.phrase or "").strip()
        if not phrase:
            raise ValueError("phrase must be a non-empty string")
        if len(phrase) > MAX_PHRASE_CHARS:
            raise ValueError("phrase exceeds maximum length")
        object.__setattr__(self, "phrase", phrase)

    @property
    def timezone_name(self) -> str:
        """IANA name of the reference timezone, or ``UTC`` if offset-only."""
        tz = self.reference_at.tzinfo
        if tz is None:
            return "UTC"
        return getattr(tz, "key", None) or "UTC"


@dataclass(frozen=True)
class TemporalInterpretation:
    """Structured result of interpreting one conversational time phrase.

    Invariants:

    - ``original_phrase`` preserves the owner's wording for provenance.
    - When ``precision`` is ``INSTANT``, ``resolution`` is a single
      timezone-aware datetime and ``window`` is ``None``. The result never
      claims a reminder or task was scheduled.
    - When ``precision`` is ``WINDOW`` or ``VAGUE``, ``resolution`` is
      ``None`` and ``window`` is a ``(start, end)`` pair of timezone-aware
      datetimes. For ``VAGUE``, ``requires_clarification`` is True.
    - ``confidence`` is in ``0.0..1.0``.
    - ``reference`` is the exact injected clock reading used.
    - ``notes`` is a tuple of short, content-free explanation strings.
    """

    original_phrase: str
    precision: TemporalPrecision
    confidence: float
    reference: TimeReference
    resolution: Optional[datetime] = None
    window: Optional[Tuple[datetime, datetime]] = None
    requires_clarification: bool = False
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.precision, TemporalPrecision):
            raise ValueError("precision must be a TemporalPrecision member")
        if not isinstance(self.reference, TimeReference):
            raise ValueError("reference must be a TimeReference")
        if (
            not isinstance(self.confidence, float)
            or self.confidence < 0.0
            or self.confidence > 1.0
        ):
            raise ValueError("confidence must be in 0.0..1.0")
        if self.precision is TemporalPrecision.INSTANT:
            if self.resolution is None:
                raise ValueError("instant precision requires a resolution")
            _require_aware(self.resolution, "resolution")
            if self.window is not None:
                raise ValueError("instant precision must not carry a window")
        else:
            if self.resolution is not None:
                raise ValueError(
                    "non-instant precision must not carry a resolution"
                )
            if self.window is None:
                raise ValueError("non-instant precision requires a window")
            start, end = self.window
            start = _require_aware(start, "window.start")
            end = _require_aware(end, "window.end")
            if end < start:
                raise ValueError("window end must not precede window start")
        if self.precision is TemporalPrecision.VAGUE and not self.requires_clarification:
            raise ValueError("vague precision must require clarification")
        if not isinstance(self.requires_clarification, bool):
            raise ValueError("requires_clarification must be a boolean")
        object.__setattr__(
            self,
            "original_phrase",
            _bounded_text(
                self.original_phrase,
                limit=MAX_PHRASE_CHARS,
                name="original_phrase",
            )
            or self.original_phrase,
        )
        if not isinstance(self.notes, tuple):
            raise ValueError("notes must be a tuple")
        for note in self.notes:
            if not isinstance(note, str) or not note.strip():
                raise ValueError("notes must be non-empty strings")

    @property
    def resolved(self) -> bool:
        """True only when a single instant was resolved (not a window)."""
        return self.precision is TemporalPrecision.INSTANT


# ---------------------------------------------------------------------------
# Task progress and stuck detection
# ---------------------------------------------------------------------------


class TaskProgressState(StrEnum):
    """Caller-asserted task progress state, derived from supplied evidence.

    These are observations only — Time Sense never inspects job/task stores
    and never mutates a task or job.
    """

    MAKING_PROGRESS = "making_progress"
    NO_PROGRESS = "no_progress"
    REPEATED_FAILURE = "repeated_failure"
    RETRYING = "retrying"
    OVERDUE = "overdue"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_USER_INPUT = "waiting_for_user_input"
    DELIVERY_FAILED = "delivery_failed"
    HEARTBEAT_MISSING = "heartbeat_missing"
    UNKNOWN = "unknown"


class StuckReason(StrEnum):
    """Content-free reason codes for a stuck assessment.

    Distinct codes preserve the difference between technical failure and
    deliberate waiting on an external actor (approval or user input).
    """

    NOT_STUCK = "not_stuck"
    NO_PROGRESS_DELAYED = "no_progress_delayed"
    REPEATED_FAILURE = "repeated_failure"
    RETRIES_WITHOUT_MOVEMENT = "retries_without_movement"
    OVERDUE = "overdue"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_USER_INPUT = "waiting_for_user_input"
    DELIVERY_FAILED = "delivery_failed"
    HEARTBEAT_MISSING = "heartbeat_missing"
    REPEATED_FAILURE_CATEGORY = "repeated_failure_category"


@dataclass(frozen=True)
class TaskProgressObservation:
    """Caller-supplied evidence about one task at one point in time.

    ``observed_at`` must be timezone-aware. ``evidence_codes`` are opaque,
    bounded, content-free strings — never raw payloads or error text.
    Quiet-hours context, when supplied, prevents delivery pauses from being
    misread as task failure.
    """

    task_id: str
    kind: str
    state: TaskProgressState
    observed_at: datetime
    parsed_at: Optional[datetime] = None
    last_progress_at: Optional[datetime] = None
    last_error_category: Optional[str] = None
    last_error_count: int = 0
    repeated_failure_category: Optional[str] = None
    repeated_failure_count: int = 0
    attempt_count: int = 0
    overdue: bool = False
    blocked_dependency: bool = False
    waiting_for_approval: bool = False
    waiting_for_user_input: bool = False
    delivery_failed: bool = False
    heartbeat_missing: bool = False
    quiet_hours_active: bool = False
    evidence_codes: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _validate_identifier("task_id", self.task_id)
        _validate_kind("kind", self.kind)
        if not isinstance(self.state, TaskProgressState):
            raise ValueError("state must be a TaskProgressState member")
        _require_aware(self.observed_at, "observed_at")
        if self.parsed_at is not None:
            _require_aware(self.parsed_at, "parsed_at")
        if self.last_progress_at is not None:
            _require_aware(self.last_progress_at, "last_progress_at")
        for name in (
            "last_error_count",
            "repeated_failure_count",
            "attempt_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in (
            "overdue",
            "blocked_dependency",
            "waiting_for_approval",
            "waiting_for_user_input",
            "delivery_failed",
            "heartbeat_missing",
            "quiet_hours_active",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        object.__setattr__(
            self,
            "last_error_category",
            _evidence_code(self.last_error_category, name="last_error_category"),
        )
        object.__setattr__(
            self,
            "repeated_failure_category",
            _evidence_code(
                self.repeated_failure_category,
                name="repeated_failure_category",
            ),
        )
        if not isinstance(self.evidence_codes, tuple):
            raise ValueError("evidence_codes must be a tuple")
        codes: list[str] = []
        for code in self.evidence_codes:
            codes.append(_evidence_code(code, name="evidence_codes"))
        object.__setattr__(self, "evidence_codes", tuple(codes))

    @property
    def is_external_block(self) -> bool:
        """True when the task is waiting on approval or user input.

        This distinction is what keeps a deliberate external wait separate
        from a technical failure so that Time Sense never reports an
        approval-blocked task as a broken system.
        """
        return self.waiting_for_approval or self.waiting_for_user_input


@dataclass(frozen=True)
class StuckAssessment:
    """Deterministic assessment of whether a task is stuck.

    ``stuck`` is True only when caller-supplied evidence supports it. A single
    slow observation never produces ``stuck=True``; conversely an approval or
    user-input wait never produces ``stuck=True`` even when delayed.

    - ``severity`` is in ``0.0..1.0``.
    - ``confidence`` is in ``0.0..1.0`` and reflects evidence strength.
    - ``reason`` is a single ``StuckReason`` code.
    - ``evidence_codes`` are the opaque codes that produced the assessment.
    """

    task_id: str
    stuck: bool
    reason: StuckReason
    severity: float
    confidence: float
    evidence_codes: Tuple[str, ...] = field(default_factory=tuple)
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _validate_identifier("task_id", self.task_id)
        if not isinstance(self.reason, StuckReason):
            raise ValueError("reason must be a StuckReason member")
        if not isinstance(self.stuck, bool):
            raise ValueError("stuck must be a boolean")
        for name in ("severity", "confidence"):
            value = getattr(self, name)
            if not isinstance(value, float) or value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be in 0.0..1.0")
        if not self.stuck and self.reason is not StuckReason.NOT_STUCK:
            raise ValueError("a non-stuck assessment must use NOT_STUCK")
        if self.stuck and self.reason is StuckReason.NOT_STUCK:
            raise ValueError("a stuck assessment must not use NOT_STUCK")
        if not isinstance(self.evidence_codes, tuple):
            raise ValueError("evidence_codes must be a tuple")
        codes: list[str] = []
        for code in self.evidence_codes:
            codes.append(_evidence_code(code, name="evidence_codes"))
        object.__setattr__(self, "evidence_codes", tuple(codes))
        if not isinstance(self.notes, tuple):
            raise ValueError("notes must be a tuple")
        for note in self.notes:
            if not isinstance(note, str) or not note.strip():
                raise ValueError("notes must be non-empty strings")


# ---------------------------------------------------------------------------
# Background awareness and notifications
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuietHoursContext:
    """Bounded quiet-hours context for notification recommendations.

    Mirrors the existing ``core.jobs.quiet_hours.QuietHours`` window
    semantics without coupling to a store or scheduler. When ``active`` is
    True the caller is inside the quiet window and notifications should be
    suppressed or deferred.
    """

    timezone_name: str
    active: bool
    start_minute: int
    end_minute: int
    suppressed: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timezone_name, str)
            or not self.timezone_name.strip()
        ):
            raise ValueError("timezone_name must be a non-empty string")
        if len(self.timezone_name) > MAX_TIMEZONE_NAME_CHARS:
            raise ValueError(
                f"timezone_name exceeds {MAX_TIMEZONE_NAME_CHARS} characters"
            )
        for name in ("start_minute", "end_minute"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if value < 0 or value >= MINUTES_PER_DAY:
                raise ValueError(f"{name} must be in 0..{MINUTES_PER_DAY - 1}")
        for name in ("active", "suppressed"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")


class NotificationAdvice(StrEnum):
    """Content-free recommendation for a notification."""

    DELIVER = "deliver"
    DEFER = "defer"
    SUPPRESS = "suppress"


@dataclass(frozen=True)
class NotificationRecommendation:
    """Quiet-hours-aware advice about whether to surface a notification.

    This is a recommendation only; Time Sense never delivers or pauses a
    notification itself. ``delivered`` is True only when caller-supplied
    state confirms actual delivery.
    """

    advice: NotificationAdvice
    quiet_hours: Optional[QuietHoursContext] = None
    delivered: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.advice, NotificationAdvice):
            raise ValueError("advice must be a NotificationAdvice member")
        if not isinstance(self.delivered, bool):
            raise ValueError("delivered must be a boolean")
        if self.quiet_hours is not None and not isinstance(
            self.quiet_hours, QuietHoursContext
        ):
            raise ValueError("quiet_hours must be a QuietHoursContext")
        if self.delivered and self.advice is NotificationAdvice.SUPPRESS:
            raise ValueError("delivered notification must not be suppressed")
        if (
            self.quiet_hours is not None
            and self.quiet_hours.active
            and self.advice is NotificationAdvice.DELIVER
        ):
            raise ValueError("must not recommend delivery during quiet hours")


@dataclass(frozen=True)
class BackgroundActivity:
    """One bounded item in a background-awareness snapshot.

    Free-form payloads are never carried. ``kind`` is an opaque label,
    ``status`` is a content-free state string, and ``payload_hint`` is an
    optional bounded, sanitized label. Sensitive task text is excluded by
    default and only included when ``include_payload_hint`` was explicitly
    requested by the caller.
    """

    item_id: str
    kind: str
    status: str
    observed_at: datetime
    payload_hint: Optional[str] = None

    def __post_init__(self) -> None:
        _validate_identifier("item_id", self.item_id)
        _validate_kind("kind", self.kind)
        _validate_status("status", self.status)
        _require_aware(self.observed_at, "observed_at")
        object.__setattr__(
            self,
            "payload_hint",
            _bounded_text(
                self.payload_hint,
                limit=MAX_PAYLOAD_HINT_CHARS,
                name="payload_hint",
            ),
        )


@dataclass(frozen=True)
class AwarenessSnapshot:
    """Bounded snapshot of caller-supplied background work.

    Counts are capped at ``MAX_SNAPSHOT_ITEMS`` per bucket. No background
    thread, scheduler, database read, polling, subprocess, automatic
    delivery, general AI, or Brain v2 personal-fact access is performed.

    Optional ``summary`` prose must avoid unverified active-execution or
    completion claims. The producer guarantees such claims only when the
    supplied observations support them; ``summary_provenance`` records the
    concrete basis in content-free terms.
    """

    reference_at: datetime
    active_tasks: Tuple[BackgroundActivity, ...] = field(default_factory=tuple)
    recently_completed: Tuple[BackgroundActivity, ...] = field(default_factory=tuple)
    approval_blocked: Tuple[BackgroundActivity, ...] = field(default_factory=tuple)
    user_input_blocked: Tuple[BackgroundActivity, ...] = field(default_factory=tuple)
    retrying_jobs: Tuple[BackgroundActivity, ...] = field(default_factory=tuple)
    stuck_candidates: Tuple[BackgroundActivity, ...] = field(default_factory=tuple)
    delivery_delayed: Tuple[BackgroundActivity, ...] = field(default_factory=tuple)
    quiet_hours_suppressed: Tuple[BackgroundActivity, ...] = field(default_factory=tuple)
    quiet_hours: Optional[QuietHoursContext] = None
    summary: Optional[str] = None
    summary_provenance: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_aware(self.reference_at, "reference_at")
        for name in (
            "active_tasks",
            "recently_completed",
            "approval_blocked",
            "user_input_blocked",
            "retrying_jobs",
            "stuck_candidates",
            "delivery_delayed",
            "quiet_hours_suppressed",
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple):
                raise ValueError(f"{name} must be a tuple")
            if len(value) > MAX_SNAPSHOT_ITEMS:
                raise ValueError(f"{name} exceeds {MAX_SNAPSHOT_ITEMS} items")
            for item in value:
                if not isinstance(item, BackgroundActivity):
                    raise ValueError(
                        f"{name} must contain BackgroundActivity items"
                    )
        if self.quiet_hours is not None and not isinstance(
            self.quiet_hours, QuietHoursContext
        ):
            raise ValueError("quiet_hours must be a QuietHoursContext")
        object.__setattr__(
            self,
            "summary",
            _bounded_text(
                self.summary, limit=MAX_SUMMARY_CHARS, name="summary"
            ),
        )
        if not isinstance(self.summary_provenance, tuple):
            raise ValueError("summary_provenance must be a tuple")
        for prov in self.summary_provenance:
            if not isinstance(prov, str) or not prov.strip():
                raise ValueError(
                    "summary_provenance entries must be non-empty strings"
                )

    @property
    def emptiness(self) -> bool:
        """True when no background work of any kind was supplied."""
        return not any(
            getattr(self, name)
            for name in (
                "active_tasks",
                "recently_completed",
                "approval_blocked",
                "user_input_blocked",
                "retrying_jobs",
                "stuck_candidates",
                "delivery_delayed",
                "quiet_hours_suppressed",
            )
        )


__all__ = [
    "AwarenessSnapshot",
    "BackgroundActivity",
    "DEFAULT_MAX_FUTURE_HORIZON",
    "MAX_EVIDENCE_CODE_CHARS",
    "MAX_IDENTIFIER_LENGTH",
    "MAX_PAYLOAD_HINT_CHARS",
    "MAX_PHRASE_CHARS",
    "MAX_SNAPSHOT_ITEMS",
    "MAX_SUMMARY_CHARS",
    "MAX_TASK_KIND_CHARS",
    "MIN_FUTURE_HORIZON",
    "MINUTES_PER_DAY",
    "NotificationAdvice",
    "NotificationRecommendation",
    "QuietHoursContext",
    "StuckAssessment",
    "StuckReason",
    "TaskProgressObservation",
    "TaskProgressState",
    "TemporalInterpretation",
    "TemporalPrecision",
    "TimeReference",
]
